# ADN DMR Peer Server - ODTP terminal session
#
# Copyright (C) 2026  Rodrigo Pérez, CE5RPY <ce5rpy@qmd.cl>
#
###############################################################################
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program; if not, write to the Free Software Foundation,
#   Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301  USA
###############################################################################

"""One ODTP terminal: logged in to the MASTER as an HBP peer whose OPTIONS carry TERMINAL=1."""

# The gateway holds no password: ODTP and HBP both answer SHA-256(salt + password), so the
# MASTER's salt is the terminal's challenge and the terminal's answer goes to the MASTER as the
# RPTK, which checks it like any hotspot's (self-care password, else the passphrase).

from __future__ import annotations

import logging
import os
from collections import deque
from enum import Enum
from typing import TYPE_CHECKING

from bitarray import bitarray

from ...domain import odtp
from ...domain.dmr.embedded_lc import collect_embedded_lc
from ...domain.dmr.frames import DmrdStream, superframe_embedding, voice_bits
from ...domain.dmrd import DmrdHeader, parse_dmrd_header
from ...domain.hbp_protocol import (
    DMRD,
    HBPF_DATA_SYNC,
    HBPF_SLT_VHEAD,
    HBPF_SLT_VTERM,
    HBPF_VOICE,
)
from ..hbp_peer_login import PeerLogin
from ..routing.helpers import UNIT_DATA_DTYPES
from .messages import Messages

if TYPE_CHECKING:
    from .gateway import OdtpGateway
    from .ports import MasterLink

logger = logging.getLogger(__name__)

RPTO_BATCH_S = 0.1  # every RPTO makes the MASTER recompute the peer's statics: batch a scan burst
UPLINK_IDLE_S = 1.0  # a call ends this long after its last frame when no terminator comes
DOWNLINK_IDLE_S = 1.0
FRAME_SPACING_S = 0.06
_EPSILON_S = 0.001


class SessionState(str, Enum):
    NEW = "new"
    OPENING = "opening"  # link to the MASTER open, RPTL sent
    CHALLENGED = "challenged"  # the MASTER's salt went to the terminal
    CONNECTING = "connecting"  # the terminal's answer went to the MASTER as the RPTK
    UP = "up"
    CLOSED = "closed"


def _field(text: str, size: int) -> bytes:
    return text.encode("ascii", "replace").ljust(size)[:size]


class _Uplink:
    """One call from the terminal as a DMRD stream; terminal embedded data rides every other superframe."""

    def __init__(self, src: int, dst: int, peer: bytes, private: bool, now: float) -> None:
        self.dst, self.private, self.last, self.frames = dst, private, now, 0
        self.stream = DmrdStream(src.to_bytes(3, "big"), dst.to_bytes(3, "big"), peer, 1, os.urandom(4), private=private)
        self._extra: deque[bytes] = deque(maxlen=8)
        self._superframe = self.stream.embedding

    def embed(self, lc: bytes) -> None:
        lc = bytes(lc[:odtp.LC_LEN])
        if len(lc) == odtp.LC_LEN and lc[0] & 0x3F in odtp.EMBEDDED_FLCOS and lc not in self._extra:
            self._extra.append(lc)

    def voice(self, ambe: bytes, now: float) -> bytes:
        n = self.frames % 6
        if n == 0:
            if self._extra and self._superframe is self.stream.embedding:
                self._superframe = superframe_embedding(self._extra.popleft())
            else:
                self._superframe = self.stream.embedding
        bits = bitarray(endian="big")
        bits.frombytes(ambe[:odtp.AUDIO_PAYLOAD])
        self.frames += 1
        self.last = now
        return self.stream.burst(n, bits[:108], bits[108:216], self._superframe)


class TerminalSession:
    def __init__(self, gw: OdtpGateway, addr: tuple[str, int]) -> None:
        self.gw = gw
        self.addr = addr
        self.state = SessionState.NEW
        self.dmr_id = 0
        self.login_peer_id = 0  # the terminal logged in with a 9-digit peer ID, used as is
        self.suffix = gw.settings.peer_suffix
        self.description = ""
        self.since = self.last_rx = gw.clock.now()
        self.subs: set[int] = set()
        self.private_subs: set[int] = set()
        self.link: MasterLink | None = None
        self.peer_login: PeerLogin | None = None
        self._stats: dict = {}
        self._config: dict = {}
        self._seq = 0
        self._rt_seq = 0
        self._rpto_pending = False
        self.uplink: _Uplink | None = None
        self.messages: Messages | None = None
        self._down_stream: bytes | None = None
        self._down_last = 0.0
        self._down_frames = 0
        self._down_acc: dict = {}
        self._down_lcs: set[bytes] = set()
        self._busy_stream: bytes | None = None

    def __str__(self) -> str:
        who = f"{self.login_peer_id}/{self.dmr_id or '?'}" if self.login_peer_id else str(self.dmr_id or "?")
        return f"terminal {who} {self.addr[0]}:{self.addr[1]}"

    @property
    def key(self) -> int:
        return self.login_peer_id or self.dmr_id

    @property
    def peer_id(self) -> bytes:
        return (self.login_peer_id or self.dmr_id * 100 + self.suffix).to_bytes(4, "big")

    def options(self) -> bytes:
        tgs = ",".join(str(tg) for tg in sorted(self.subs))
        return f"TERMINAL=1;TS2={tgs};".encode() if tgs else b"TERMINAL=1;"

    def send(self, msg_type: int, payload: bytes = b"") -> None:
        if msg_type in odtp.REAL_TIME_SEQUENCE:
            self._rt_seq += 1
            flags = odtp.FLAG_REAL_TIME_1 if msg_type in odtp.REAL_TIME_FLAGGED else 0
            data = odtp.pack(msg_type, self._rt_seq, payload, flags)
        else:
            self._seq += 1
            data = odtp.pack(msg_type, self._seq, payload)
        self.gw.socket.send(data, self.addr)

    def _to_master(self, packet: bytes) -> None:
        if self.link is not None:
            self.link.send(packet)

    def _send_frames(self, frames: list[bytes]) -> None:
        """A data packet for the MASTER, one DMRD frame per TDMA burst time."""
        for i, frame in enumerate(frames):
            self.gw.clock.call_later(i * FRAME_SPACING_S, self._to_master, frame)

    # ── from the terminal ──

    def received(self, msg_type: int, payload: bytes) -> None:
        self.last_rx = self.gw.clock.now()
        if msg_type == odtp.KEEP_ALIVE:
            self._keep_alive(payload)
        elif msg_type == odtp.AUTHENTICATION:
            if self.state is SessionState.CHALLENGED and len(payload) >= 32 and self.peer_login is not None:
                self.state = SessionState.CONNECTING
                self.peer_login.send_key(payload[:32])
        elif msg_type == odtp.CLOSE:
            self.gw.drop(self, "closed by the terminal")
        elif self.state is not SessionState.UP:
            return
        elif msg_type in (odtp.SUBSCRIPTION, odtp.CANCELLING):
            self._subscribe(payload, add=msg_type == odtp.SUBSCRIPTION)
        elif msg_type == odtp.BUSY_NOTICE:
            self._busy_stream = self._down_stream
        elif msg_type == odtp.VOICE_HEADER:
            self._uplink_header(payload)
        elif msg_type == odtp.DMR_AUDIO_FRAME:
            if self.uplink is not None and len(payload) >= odtp.AUDIO_PAYLOAD:
                self._to_master(self.uplink.voice(payload, self.gw.clock.now()))
        elif msg_type == odtp.VOICE_TERMINATOR:
            self._uplink_end("terminator")
        elif msg_type == odtp.DMR_EMBEDDED_DATA:
            if self.uplink is not None:
                self.uplink.embed(payload)
        elif msg_type == odtp.MESSAGE_TEXT:
            self.messages.text_from_terminal(payload)
        elif msg_type == odtp.LOCATION_REPORT:
            self.messages.location_from_terminal(payload)

    def _keep_alive(self, payload: bytes) -> None:
        if self.state is SessionState.UP:
            self.send(odtp.KEEP_ALIVE)
            return
        parsed = odtp.parse_keep_alive(payload) if self.state is SessionState.NEW else None
        if parsed is None:
            return
        number, service, self.description = parsed
        if service != odtp.SERVICE_OPEN_TERMINAL or not 0 < number < 1_000_000_000:
            logger.info("(ODTP) %s: not an Open DMR Terminal (service 0x%02x, ID %s)", self, service, number)
            return
        dmr_id = number // 100 if number >= 100_000_000 else number  # a 9-digit login is DMR ID x 100 + 2 digits
        if dmr_id > 0xFFFFFF:
            logger.info("(ODTP) %s: ID %s is not a DMR ID", self, number)
            return
        self.dmr_id = dmr_id
        if number >= 100_000_000:
            self.login_peer_id = number
        else:
            old = self.gw.sessions_by_key.get(number)
            if old is not None:
                # The MASTER drops a connected peer when its ID logs in again: a second session
                # takes the other suffix and replaces the first only once the MASTER accepts it.
                s = self.gw.settings
                self.suffix = s.alt_suffix if old.suffix == s.peer_suffix else s.peer_suffix
        callsign = self.gw.callsigns(self.dmr_id)
        self._config = {
            "RADIO_ID": self.peer_id, "CALLSIGN": _field(callsign, 8), "RX_FREQ": _field("000000000", 9),
            "TX_FREQ": _field("000000000", 9), "TX_POWER": _field("01", 2), "COLORCODE": _field("01", 2),
            "LATITUDE": _field("0.0", 8), "LONGITUDE": _field("0.0", 9), "HEIGHT": _field("000", 3),
            "LOCATION": _field("ODTP terminal", 20), "DESCRIPTION": _field(self.description, 19),
            "SLOTS": b"4", "URL": _field("", 124), "SOFTWARE_ID": _field(self.description, 40),
            "PACKAGE_ID": _field("ODTP", 40), "OPTIONS": self.options(),
            "MASTER_IP": self.gw.settings.master_ip, "MASTER_PORT": self.gw.settings.master_port,
        }
        self.peer_login = PeerLogin(str(self), self._config, self._stats, self._to_master, logger, on_salt=self._salt)
        # The link opens last: a session that fails before it leaves nothing open.
        try:
            self.link = self.gw.links.open(self.from_master)
        except OSError as e:
            self.gw.drop(self, f"cannot reach the MASTER: {e}", notify=False)
            return
        self.gw.count_login()
        self.state = SessionState.OPENING
        self.since = self.gw.clock.now()
        self.peer_login.tick(self.gw.settings.max_missed)

    def _salt(self, salt: bytes) -> None:
        """The MASTER's salt is the terminal's challenge; its answer comes back as AUTHENTICATION."""
        if self.state is SessionState.OPENING:
            self.state = SessionState.CHALLENGED
            self.send(odtp.CHALLENGE, salt)

    def _subscribe(self, payload: bytes, *, add: bool) -> None:
        parsed = odtp.parse_subscription(payload)
        if parsed is None:
            if payload or add:
                return
            self.subs.clear()
            self.private_subs.clear()
        else:
            kind, number = parsed
            group = kind == odtp.SESSION_TYPE_GROUP_VOICE
            target = self.subs if group else self.private_subs
            (target.add if add else target.discard)(number)
        self.send(odtp.SUBSCRIPTION if add else odtp.CANCELLING)
        self._config["OPTIONS"] = self.options()
        if not self._rpto_pending:
            self._rpto_pending = True
            self.gw.clock.call_later(RPTO_BATCH_S, self._send_options)
        logger.info("(ODTP) %s subscriptions: TG %s, private %s", self, sorted(self.subs) or "-", sorted(self.private_subs) or "-")

    def _send_options(self) -> None:
        self._rpto_pending = False
        if self.peer_login is not None:
            self.peer_login.send_options()

    def _uplink_header(self, payload: bytes) -> None:
        parsed = odtp.parse_voice_header(payload)
        if parsed is None:
            return
        dst, _src, private = parsed  # the source is never the header's
        if self.uplink is not None and (self.uplink.dst, self.uplink.private) == (dst, private):
            return  # the header comes 2-3 times
        self._uplink_end("new call")
        # The source is always the session's DMR ID, never the header's: the 7-digit login or
        # login // 100. A terminal cannot talk as somebody else.
        self.uplink = uplink = _Uplink(self.dmr_id, dst, self.peer_id, private, self.gw.clock.now())
        for _ in range(3):
            self._to_master(uplink.stream.header())
        self.gw.clock.call_later(UPLINK_IDLE_S, self._watch_uplink, uplink)
        logger.info("(ODTP) %s TX %s %s", self, "private" if private else "TG", dst)

    def _watch_uplink(self, uplink: _Uplink) -> None:
        if self.uplink is not uplink:
            return
        left = uplink.last + UPLINK_IDLE_S - self.gw.clock.now()
        if left > _EPSILON_S:
            self.gw.clock.call_later(left, self._watch_uplink, uplink)
        else:
            self._uplink_end("no terminator from the app")

    def _uplink_end(self, reason: str) -> None:
        if self.uplink is not None:
            self._to_master(self.uplink.stream.terminator())
            logger.info("(ODTP) %s TX end (%d frames, %s)", self, self.uplink.frames, reason)
        self.uplink = None

    # ── from the MASTER ──

    def from_master(self, data: bytes) -> None:
        if data[:4] == DMRD:
            if self.state is SessionState.UP:
                self._downlink(data)
            return
        if self.peer_login is None:
            return
        before = self._stats.get("CONNECTION")
        self.peer_login.datagram(data[:4], data)
        after = self._stats.get("CONNECTION")
        if after == "YES" and before != "YES":
            self._master_up()
        elif after == "NO" and before != "NO":
            if self.state is SessionState.UP:
                self.gw.drop(self, "MASTER dropped the peer")
            else:  # silence to the terminal, as BrandMeister does
                what = "wrong password" if before == "AUTHENTICATED" else f"refused ({before})"
                self.gw.drop(self, f"MASTER login failed: {what}", notify=False)

    def _master_up(self) -> None:
        self.gw.register(self)
        self.state = SessionState.UP
        self.messages = Messages(self.dmr_id, int.from_bytes(self.peer_id, "big"), self.gw.settings, self.gw.clock,
                                 self._send_frames, self.send)
        self.send(odtp.KEEP_ALIVE)
        logger.info("(ODTP) %s up as peer %s (%s)", self, int.from_bytes(self.peer_id, "big"), self.description)

    def _downlink(self, data: bytes) -> None:
        h = parse_dmrd_header(data)
        if h is None:
            return
        private = h.call_type == "unit"
        if private and self.dmr_id and int.from_bytes(h.dst_id, "big") != self.dmr_id:
            return
        burst = data[20:53]
        if h.stream_id == self._down_stream:
            self._down_last = self.gw.clock.now()
        if h.stream_id == self._busy_stream:
            if h.frame_type == HBPF_DATA_SYNC and h.dtype_vseq == HBPF_SLT_VTERM:
                self._end_downlink("terminator")
            return
        if h.frame_type == HBPF_DATA_SYNC:
            if h.dtype_vseq in UNIT_DATA_DTYPES:
                self.messages.data_frame(h, burst)
            elif h.dtype_vseq == HBPF_SLT_VHEAD and h.stream_id != self._down_stream:
                self._start_downlink(h)  # the MASTER repeats the header 2-3 times
            elif h.dtype_vseq == HBPF_SLT_VTERM and h.stream_id == self._down_stream:
                self._end_downlink("terminator")
            return
        if h.stream_id != self._down_stream:
            self._start_downlink(h)  # joined late, or the header was lost
        self.send(odtp.DMR_AUDIO_FRAME, voice_bits(burst).tobytes())
        self._down_frames += 1
        lc = collect_embedded_lc(self._down_acc, h.dtype_vseq if h.frame_type == HBPF_VOICE else 0, burst)
        if lc is not None and lc[0] & 0x3F in odtp.EMBEDDED_FLCOS and lc not in self._down_lcs:
            self._down_lcs.add(lc)
            self.send(odtp.DMR_EMBEDDED_DATA, lc)

    def _start_downlink(self, h: DmrdHeader) -> None:
        if self._down_stream is not None:
            self._end_downlink("new call")
        self._down_stream = h.stream_id
        self._down_last = self.gw.clock.now()
        self._down_frames = 0
        self._down_acc = {}
        self._down_lcs = set()
        dst, src, private = int.from_bytes(h.dst_id, "big"), int.from_bytes(h.rf_src, "big"), h.call_type == "unit"
        self.send(odtp.VOICE_HEADER, odtp.voice_header(dst, src, private))
        logger.info("(ODTP) %s RX %s %s from %s", self, "private" if private else "TG", dst, src)
        self.gw.clock.call_later(DOWNLINK_IDLE_S, self._watch_downlink, h.stream_id)

    def _watch_downlink(self, stream_id: bytes) -> None:
        if self._down_stream != stream_id or self.state is not SessionState.UP:
            return
        left = self._down_last + DOWNLINK_IDLE_S - self.gw.clock.now()
        if left > _EPSILON_S:
            self.gw.clock.call_later(left, self._watch_downlink, stream_id)
        else:
            self._end_downlink("no terminator from the MASTER")

    def _end_downlink(self, reason: str) -> None:
        """The terminal leaves RX; a call it said it was busy for ends silently."""
        if self._down_stream != self._busy_stream:
            self.send(odtp.VOICE_TERMINATOR)
        logger.info("(ODTP) %s RX end (%d frames, %s)", self, self._down_frames, reason)
        self._down_stream = self._busy_stream = None

    # ── housekeeping ──

    def tick(self, now: float) -> None:
        s = self.gw.settings
        if self.state is not SessionState.UP:
            if now - self.since > s.login_timeout_s:
                self.gw.drop(self, f"login not finished ({self.state})", notify=False)
            return
        if self._stats.get("PING_OUTSTANDING") and self._stats.get("NUM_OUTSTANDING", 0) + 1 >= s.max_missed:
            self.gw.drop(self, "MASTER silent")
            return
        self.peer_login.tick(s.max_missed)
        if now - self.last_rx > s.terminal_timeout_s:
            self.gw.drop(self, "keep-alive timeout")

    def close(self, notify: bool) -> None:
        self._uplink_end("session closed")
        if notify and self.state is SessionState.UP:
            self.send(odtp.CLOSE)
        if self.peer_login is not None:
            self.peer_login.close()
        if self.link is not None:
            self.link.close()
            self.link = None
        self.state = SessionState.CLOSED
