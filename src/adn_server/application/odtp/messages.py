# ADN DMR Peer Server - ODTP gateway text messages and locations
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

"""Text messages and locations of one ODTP terminal, as DMR packet data on the MASTER."""

# From the terminal: a private text goes as a confirmed packet in the format the addressee last
# wrote in (MOTOTRBO TMS, "DMR standard" UDP 5016, Hytera TMP), the configured one otherwise, and
# the radio's link ACK becomes MESSAGE_STATUS; a group text goes unconfirmed to the talkgroup; a
# location goes as unconfirmed NMEA data to ``location_dst``. To the terminal: packets for its
# peer are reassembled and checked, and the gateway answers the ACKs the sender asks for.

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field

from ...domain import odtp
from ...domain.dmr import data_header as hd
from ...domain.dmr import hdap, ip_sms
from ...domain.dmr.crc import crc32_data
from ...domain.dmr.data_blocks import (
    DT_DATA_HEADER,
    DT_RATE12,
    DT_RATE34,
    Block,
    build_blocks,
    data_frames,
    decode_block,
    decode_header_burst,
)
from ...domain.dmrd import DmrdHeader
from .ports import Clock, OdtpSettings, SmsFormat

logger = logging.getLogger(__name__)

STATUS_ACK = 0x08  # octet 9 of an ACK response header: class 0, type 1, status 0
GROUP_NET = 225  # MOTOTRBO group addresses: 225.x.y.z = talkgroup
_DUPLICATE_S = 120.0
_PACKET_TIMEOUT_S = 10.0
_RETRY_S = 6.0


@dataclass
class _Rx:
    header: hd.DataHeader
    seen: float
    blocks: list[Block] = field(default_factory=list)


@dataclass
class _Pending:
    frames: list[bytes]
    retried: bool = False


class Messages:
    """Packet data of one terminal: ``id`` is its DMR ID, ``peer`` its HBP peer ID."""

    def __init__(
        self,
        terminal_id: int,
        peer_id: int,
        settings: OdtpSettings,
        clock: Clock,
        send_frames: Callable[[list[bytes]], None],
        send_terminal: Callable[[int, bytes], None],
    ) -> None:
        self.id = terminal_id
        self.peer = peer_id
        self._s = settings
        self._clock = clock
        self._send = send_frames
        self._send_terminal = send_terminal
        self._rx: dict[bytes, _Rx] = {}
        self._format: dict[int, SmsFormat] = {}
        self._seen: dict[tuple[int, str], float] = {}
        self._pending: dict[int, _Pending] = {}
        self._ip_id = 0
        self._tms_seq = 0
        self._tmp_id = 0
        self._send_seq = 0
        self._last_location = -1e9

    def text_from_terminal(self, payload: bytes) -> None:
        parsed = odtp.parse_text_message(payload)
        if parsed is None:
            return
        _src, dst, group, text = parsed
        text = text.strip()
        if not text or not 0 < dst < 0x1000000:
            return
        if group:
            body = ip_sms.ip_udp(self.id, dst, ip_sms.PORT_DMR_STANDARD_SMS, ip_sms.standard_sms(text),
                                 self._next_ip_id(), dst_net=GROUP_NET)
            blocks, pad = build_blocks(body, rate34=False, confirmed=False)
            header = hd.DataHeader(dpf=hd.DPF_UNCONFIRMED, dst=dst, src=self.id, sap=hd.SAP_IP, group=True,
                                   pad_octets=pad, blocks_to_follow=len(blocks)).to_bytes()
            self._send(data_frames(header, blocks, self.id, dst, self.peer, 2, self._s.sms_preambles, group=True))
            logger.info("(ODTP) %s text -> TG %s: %r", self.id, dst, text)
            return
        fmt = self._format.get(dst, self._s.sms_format)
        frames = self._private_text(dst, text, fmt)
        self._pending[dst] = _Pending(frames)
        self._send(frames)
        self._clock.call_later(_RETRY_S, self._retry, dst, frames)
        logger.info("(ODTP) %s text -> %s (%s): %r", self.id, dst, fmt, text)

    def _private_text(self, dst: int, text: str, fmt: SmsFormat) -> list[bytes]:
        if fmt == "hytera":
            self._tmp_id = (self._tmp_id + 1) & 0xFFFFFFFF
            return self._short_frames(dst, hdap.text(self._tmp_id, dst, self.id, text), 2)
        if fmt == "tms":
            self._tms_seq = (self._tms_seq + 1) & 0x1F
            return self._ip_frames(dst, ip_sms.PORT_TMS, ip_sms.tms_simple_text(text, self._tms_seq), 2)
        return self._ip_frames(dst, ip_sms.PORT_DMR_STANDARD_SMS, ip_sms.standard_sms(text), 2)

    def _retry(self, radio: int, frames: list[bytes]) -> None:
        pending = self._pending.get(radio)
        if pending is None or pending.frames is not frames:
            return
        if pending.retried:
            del self._pending[radio]
            logger.info("(ODTP) %s: no ACK from %s for the text", self.id, radio)
            return
        pending.retried = True
        self._send(frames)
        self._clock.call_later(_RETRY_S, self._retry, radio, frames)

    def location_from_terminal(self, payload: bytes) -> None:
        nmea = odtp.parse_location_report(payload)
        now = self._clock.now()
        if not nmea or not self._s.location_dst or now - self._last_location < self._s.location_min_gap_s:
            return
        self._last_location = now
        if not nmea.endswith(b"\r\n"):
            nmea += b"\r\n"
        blocks, pad = build_blocks(nmea, rate34=False, confirmed=False)
        dst = self._s.location_dst
        header = hd.DataHeader(dpf=hd.DPF_UNCONFIRMED, dst=dst, src=self.id, sap=hd.SAP_IP,
                               pad_octets=pad, blocks_to_follow=len(blocks)).to_bytes()
        self._send(data_frames(header, blocks, self.id, dst, self.peer, 2))
        logger.info("(ODTP) %s location -> %s: %s", self.id, dst, nmea.strip().decode("ascii", "replace"))

    def data_frame(self, h: DmrdHeader, burst: bytes) -> None:
        """One data burst the MASTER delivered to this terminal's peer."""
        if h.call_type == "unit" and int.from_bytes(h.dst_id, "big") != self.id:
            return
        now = self._clock.now()
        if h.dtype_vseq == DT_DATA_HEADER:
            header = hd.DataHeader.from_bytes(decode_header_burst(burst))
            if header is None:
                return
            if header.dpf == hd.DPF_RESPONSE:
                self._response(header)
                return
            self._rx = {k: v for k, v in self._rx.items() if now - v.seen < _PACKET_TIMEOUT_S}
            if header.blocks:
                self._rx[h.stream_id] = _Rx(header, now)
            return
        rx = self._rx.get(h.stream_id)
        if rx is None or h.dtype_vseq not in (DT_RATE12, DT_RATE34):
            return
        confirmed = rx.header.confirmed or (rx.header.dpf == hd.DPF_SHORT_DATA_DEFINED and rx.header.sarq)
        last = len(rx.blocks) + 1 >= rx.header.blocks
        block = decode_block(burst, h.dtype_vseq, confirmed, last)
        if block is None:
            self._rx.pop(h.stream_id, None)
            return
        rx.blocks.append(block)
        if not last:
            return
        self._rx.pop(h.stream_id, None)
        user = b"".join(b.data for b in rx.blocks)
        if not all(b.crc_ok for b in rx.blocks) or crc32_data(user) != rx.blocks[-1].crc32:
            logger.info("(ODTP) %s: packet from %s with CRC errors, dropped", self.id, rx.header.src)
            return
        self._packet(rx.header, user, h.slot)

    def _response(self, header: hd.DataHeader) -> None:
        if header.dst != self.id or self._pending.pop(header.src, None) is None:
            return
        # An ACK is 0x08 whatever N(S) it acknowledges; anything else goes as the radio sent it.
        if (header.response_class, header.response_type) == hd.RESPONSE_ACK:
            status = STATUS_ACK
        else:
            status = (header.response_class << 6) | (header.response_type << 3) | header.response_status
        self._send_terminal(odtp.MESSAGE_STATUS, odtp.text_status(header.src, self.id, status))
        logger.info("(ODTP) %s: %s answered the text (class %d type %d)", self.id, header.src,
                    header.response_class, header.response_type)

    def _packet(self, h: hd.DataHeader, user: bytes, slot: int) -> None:
        radio, group = h.src, h.group
        if h.response_requested and not group:
            send_seq = h.send_seq if h.dpf == hd.DPF_CONFIRMED else 0
            ack = hd.response_ack(radio, self.id, h.sap, send_seq).to_bytes()
            self._send(data_frames(ack, [], self.id, radio, self.peer, slot))
        text, fmt = None, None
        if h.sap == hd.SAP_IP:
            text, fmt = self._ip_text(h, user, radio, group, slot)
        elif h.sap == hd.SAP_SHORT_DATA:
            found = hdap.find(user)
            tm = hdap.text_message(found[0]) if found else None
            if tm is not None:
                if tm.ack_required and not group:
                    self._send(self._short_frames(radio, hdap.text_ack(tm.request_id, radio, self.id), slot))
                text, fmt = tm.text, "hytera"
        if not text:
            return
        if not group:
            self._format[radio] = fmt
        now = self._clock.now()
        key = (radio, text)
        if now - self._seen.get(key, -1e9) < _DUPLICATE_S:  # a radio repeats a text it got no ACK for
            return
        self._seen = {k: t for k, t in self._seen.items() if now - t < _DUPLICATE_S}
        self._seen[key] = now
        dst = h.dst if group else self.id
        self._send_terminal(odtp.MESSAGE_TEXT, odtp.text_message(radio, dst, text, group))
        logger.info("(ODTP) %s text <- %s%s (%s): %r", self.id, radio, f" on TG {dst}" if group else "", fmt, text)

    def _ip_text(self, h: hd.DataHeader, user: bytes, radio: int, group: bool, slot: int) -> tuple[str | None, SmsFormat | None]:
        msg = ip_sms.parse_ip_udp(user)
        if msg is None:
            return None, None
        if msg.port == ip_sms.PORT_TMS:
            tms = ip_sms.tms_parse(msg.payload)
            if tms is None or tms.is_ack:
                return None, None
            if tms.ack_required and not group:
                self._send(self._ip_frames(radio, ip_sms.PORT_TMS, ip_sms.tms_ack(tms.sequence), slot, msg.dst_net))
            return tms.text, "tms"
        if msg.port != ip_sms.PORT_DMR_STANDARD_SMS:
            return None, None
        text = ip_sms.standard_sms_parse(msg.payload)
        if text is not None:
            return text, "standard"
        found = hdap.find(msg.payload)  # Hytera TMP over UDP 5016
        tm = hdap.text_message(found[0]) if found else None
        if tm is None:
            return None, None
        if tm.ack_required and not group:
            ack = hdap.text_ack(tm.request_id, radio, self.id)
            self._send(self._ip_frames(radio, ip_sms.PORT_DMR_STANDARD_SMS, ack, slot, msg.dst_net))
        return tm.text, "standard"

    def _ip_frames(self, radio: int, port: int, payload: bytes, slot: int, src_net: int = ip_sms.RADIO_NET) -> list[bytes]:
        """A confirmed IPv4/UDP packet to ``radio`` (rate 3/4, link ACK requested)."""
        user = ip_sms.ip_udp(self.id, radio, port, payload, self._next_ip_id(), src_net)
        blocks, pad = build_blocks(user, rate34=True, confirmed=True)
        self._send_seq = (self._send_seq + 1) & 7
        header = hd.DataHeader(dpf=hd.DPF_CONFIRMED, dst=radio, src=self.id, sap=hd.SAP_IP, response_requested=True,
                               pad_octets=pad, blocks_to_follow=len(blocks), resync=True,
                               send_seq=self._send_seq, fsn=hd.FSN_SINGLE_CONFIRMED).to_bytes()
        return data_frames(header, blocks, self.id, radio, self.peer, slot, self._s.sms_preambles)

    def _short_frames(self, radio: int, message: bytes, slot: int) -> list[bytes]:
        """Hytera short data as the radio sends a text: rate 3/4 with SARQ."""
        user = bytes([0x01, 0x00]) + message
        blocks, pad = build_blocks(user, rate34=True, confirmed=True)
        header = hd.DataHeader(dpf=hd.DPF_SHORT_DATA_DEFINED, dst=radio, src=self.id, sap=hd.SAP_SHORT_DATA,
                               response_requested=True, appended_blocks=len(blocks), sarq=True,
                               bit_padding=pad * 8).to_bytes()
        return data_frames(header, blocks, self.id, radio, self.peer, slot, self._s.sms_preambles)

    def _next_ip_id(self) -> int:
        self._ip_id = (self._ip_id + 1) & 0xFFFF
        return self._ip_id or int.from_bytes(os.urandom(2), "big")
