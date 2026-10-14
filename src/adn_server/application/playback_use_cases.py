# ADN DMR Peer Server - playback (echo) use case
# Copyright (C) 2026  Rodrigo Pérez, CE5RPY <ce5rpy@qmd.cl>
#
# Derived from ADN DMR Server / FreeDMR  / HBlink. Original license:
###############################################################################
# Copyright (C) 2020 Simon Adlem, G7RZU <g7rzu@gb7fr.org.uk>
# Copyright (C) 2016-2019 Cortney T. Buffington, N0MJS <n0mjs@me.com>
#
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

"""Port of playback.py dmrd_received: record group voice, then play back with new stream ID."""

from __future__ import annotations

import logging
from collections.abc import Callable
from random import randint
from time import time
from typing import Any

from ..domain import HBPF_DATA_SYNC, HBPF_SLT_VHEAD, HBPF_SLT_VTERM, HBPF_VOICE, bytes_4, int_id

logger = logging.getLogger(__name__)

# Legacy playback.py: sleep(2) before playback, sleep(0.06) between packets.
PLAYBACK_DELAY_S = 2.0
_PACKET_INTERVAL_S = 0.06
# Match bridge stream_trimmer_loop RX idle (routing_use_cases / legacy bridge_master).
_RECORD_IDLE_S = 5.0
# HBP ingress source timeout (bridge_master.py / routing_use_cases dmrd_received ~2183).
_SOURCE_MAX_S = 180.0


class PlaybackUseCases:
    """Legacy playback class behaviour; playback is scheduled on the reactor (non-blocking)."""

    def __init__(
        self,
        system_name: str,
        *,
        call_later: Callable[..., Any],
        get_protocol: Any = None,
    ) -> None:
        self._system = system_name
        self._call_later = call_later
        self._get_protocol = get_protocol
        self.STATUS: dict[str, Any] = {}
        self.CALL_DATA: list[bytes] = []
        self._rx_stream_by_slot: dict[int, bytes] = {}
        self._record_stream: bytes = b""
        self._seen_record_streams: set[bytes] = set()
        self._ignored_streams_logged: set[bytes] = set()
        self._record_ctx: dict[str, Any] = {}
        self._recording_active = False
        self._last_record_time = 0.0
        self._playback_busy = False
        self._playback_packets: list[bytes] = []
        self._playback_index = 0
        self._playback_stream_id = b""
        self._playback_ta_from_stream = b""
        self._delay_call: Any = None
        self._packet_call: Any = None
        self._idle_call: Any = None
        self._max_call: Any = None

    def dmrd_received(
        self,
        system_name: str,
        peer_id: bytes,
        rf_src: bytes,
        dst_id: bytes,
        seq: int,
        slot: int,
        call_type: str,
        frame_type: int,
        dtype_vseq: int,
        stream_id: bytes,
        data: bytes,
        ingress_pkt_time: float | None = None,
    ) -> None:
        """Port of playback.dmrd_received (playback.py lines 114-161)."""
        if call_type != "group":
            logger.debug(
                "(%s) Playback ignore non-group call type=%s stream=%s",
                self._system, call_type, int_id(stream_id),
            )
            return

        # Legacy blocked the reactor during delay + playback; ignore new voice meanwhile.
        if self._playback_busy:
            logger.debug(
                "(%s) Playback ignore stream %s (playback in progress)",
                self._system, int_id(stream_id),
            )
            return

        pkt_time = ingress_pkt_time if ingress_pkt_time is not None else time()
        proto = self._get_protocol() if self._get_protocol else None

        if self._recording_active and self.CALL_DATA and self._recording_exceeded_max(pkt_time):
            self._on_record_max_duration(proto)
            return

        slot_status = getattr(proto, "STATUS", {}).get(slot, {}) if proto else {}
        record_stream = self._record_stream or self._rx_stream_by_slot.get(
            slot, slot_status.get("RX_STREAM_ID", b"\x00"),
        )
        is_vhead = frame_type == HBPF_DATA_SYNC and dtype_vseq == HBPF_SLT_VHEAD
        idle_gap = pkt_time - self._last_record_time if self._last_record_time else 0.0

        if stream_id != record_stream:
            # Hotspot re-key mid-PTT: new VHEAD shortly after the last voice frame.
            if (
                self._recording_active
                and self.CALL_DATA
                and is_vhead
                and stream_id not in self._seen_record_streams
                and idle_gap < _RECORD_IDLE_S
            ):
                prev = record_stream
                self._record_stream = stream_id
                self._rx_stream_by_slot[slot] = stream_id
                self._seen_record_streams.add(stream_id)
                logger.debug(
                    "(%s) Recording continues: stream %s -> %s (same PTT)",
                    self._system, int_id(prev), int_id(stream_id),
                )
                # Same PTT re-key: track new stream but do not store a second VHEAD.
                self._last_record_time = pkt_time
                self._record_ctx = {
                    "slot": slot,
                    "rf_src": rf_src,
                    "peer_id": peer_id,
                    "dst_id": dst_id,
                }
                self._schedule_record_idle(proto)
                return
            # New PTT while prior recording never got VTERM (user released, hotspot cut, etc.).
            if self._recording_active and self.CALL_DATA and is_vhead and idle_gap >= _RECORD_IDLE_S:
                logger.info(
                    "(%s) New PTT after %.1fs idle; discarding prior recording (%d packets)",
                    self._system, idle_gap, len(self.CALL_DATA),
                )
                self._reset_recording_state(slot)
                self._begin_recording(slot, stream_id, pkt_time, rf_src, peer_id, dst_id, proto)
                self._append_voice(data, pkt_time, proto, slot, rf_src, peer_id, dst_id)
                return
            if self._recording_active and self.CALL_DATA:
                if stream_id not in self._ignored_streams_logged:
                    self._ignored_streams_logged.add(stream_id)
                    logger.debug(
                        "(%s) Ignoring stream %s (current recording stream %s)",
                        self._system, int_id(stream_id), int_id(record_stream),
                    )
                return
            if self.CALL_DATA:
                logger.debug(
                    "(%s) Discarding stale recording (%d packets) before new stream %s",
                    self._system, len(self.CALL_DATA), int_id(stream_id),
                )
                self._reset_recording_state(slot)
            self._begin_recording(slot, stream_id, pkt_time, rf_src, peer_id, dst_id, proto)
            self._append_voice(data, pkt_time, proto, slot, rf_src, peer_id, dst_id)
            return

        if (
            frame_type == HBPF_DATA_SYNC
            and dtype_vseq == HBPF_SLT_VTERM
            and self._recording_active
            and self.CALL_DATA
            and stream_id == record_stream
        ):
            call_duration = pkt_time - self.STATUS.get("RX_START", pkt_time)
            self.CALL_DATA.append(data)
            logger.info("(%s) *END   RECORDING* STREAM ID: %s", self._system, int_id(stream_id))
            self._commit_recording(proto, call_duration)
            return

        if self._recording_active and self.CALL_DATA:
            self._append_voice(data, pkt_time, proto, slot, rf_src, peer_id, dst_id)

    def _append_voice(
        self,
        data: bytes,
        pkt_time: float,
        proto: Any,
        slot: int,
        rf_src: bytes,
        peer_id: bytes,
        dst_id: bytes,
    ) -> None:
        self.CALL_DATA.append(data)
        if proto is not None and hasattr(proto, "store_ta_from_voice_burst") and len(data) >= 53:
            bits = data[15]
            frame_type = (bits & 0x30) >> 4
            vseq = bits & 0xF
            if frame_type != HBPF_DATA_SYNC and vseq in (1, 2, 3, 4):
                proto.store_ta_from_voice_burst(
                    peer_id, rf_src, self._record_stream, vseq, data[20:53],
                )
        self._last_record_time = pkt_time
        self._record_ctx = {
            "slot": slot,
            "rf_src": rf_src,
            "peer_id": peer_id,
            "dst_id": dst_id,
        }
        self._schedule_record_idle(proto)

    def _schedule_record_idle(self, proto: Any) -> None:
        self._cancel_record_idle()
        if not self._recording_active:
            return
        self._idle_call = self._call_later(_RECORD_IDLE_S, self._on_record_idle, proto)

    def _cancel_record_idle(self) -> None:
        if self._idle_call is not None and self._idle_call.active():
            self._idle_call.cancel()
        self._idle_call = None

    def _on_record_idle(self, proto: Any) -> None:
        self._idle_call = None
        if not self._recording_active or not self.CALL_DATA or self._playback_busy:
            return
        call_duration = time() - self.STATUS.get("RX_START", time())
        logger.info(
            "(%s) *END   RECORDING* idle timeout %.0fs (no VTERM) stream %s",
            self._system, _RECORD_IDLE_S, int_id(self._record_stream),
        )
        self._commit_recording(proto, call_duration)

    def _packet_is_vhead(self, data: bytes) -> bool:
        if len(data) < 16:
            return False
        bits = data[15]
        return ((bits & 0x30) >> 4) == HBPF_DATA_SYNC and (bits & 0xF) == HBPF_SLT_VHEAD

    def _packet_is_vterm(self, data: bytes) -> bool:
        if len(data) < 16:
            return False
        bits = data[15]
        return ((bits & 0x30) >> 4) == HBPF_DATA_SYNC and (bits & 0xF) == HBPF_SLT_VTERM

    def _make_vterm_packet(self, template: bytes, slot: int) -> bytes:
        """Build a voice terminator from the last recorded frame (idle/max end has no VTERM)."""
        if len(template) < 53:
            return template
        pkt = bytearray(template)
        ts_bit = 0x80 if slot == 2 else 0x00
        pkt[15] = ts_bit | (HBPF_DATA_SYNC << 4) | HBPF_SLT_VTERM
        pkt[4] = (int(pkt[4]) + 1) & 0xFF
        return bytes(pkt)

    def _ensure_vterm(self, recorded: list[bytes], slot: int) -> list[bytes]:
        if not recorded or self._packet_is_vterm(recorded[-1]):
            return recorded
        logger.debug(
            "(%s) Appending synthetic VTERM for playback (%d packets, no terminator from source)",
            self._system, len(recorded),
        )
        return recorded + [self._make_vterm_packet(recorded[-1], slot)]

    def _commit_recording(self, proto: Any, call_duration: float) -> None:
        ctx = self._record_ctx
        slot = ctx.get("slot", 1)
        recorded = self._ensure_vterm(list(self.CALL_DATA), slot)
        self._playback_ta_from_stream = self._record_stream
        self._reset_recording_state(slot)
        if not recorded:
            return
        self._playback_busy = True
        self._delay_call = self._call_later(
            PLAYBACK_DELAY_S,
            self._start_playback,
            proto,
            recorded,
            ctx.get("rf_src", b"\x00\x00\x00"),
            ctx.get("peer_id", b"\x00\x00\x00\x00"),
            ctx.get("dst_id", b"\x00\x00\x00"),
            slot,
            call_duration,
        )

    def _begin_recording(
        self,
        slot: int,
        stream_id: bytes,
        pkt_time: float,
        rf_src: bytes,
        peer_id: bytes,
        dst_id: bytes,
        proto: Any,
    ) -> None:
        self._recording_active = True
        self._record_stream = stream_id
        self._rx_stream_by_slot[slot] = stream_id
        self._seen_record_streams = {stream_id}
        self._ignored_streams_logged.clear()
        self._last_record_time = pkt_time
        self._record_ctx = {
            "slot": slot,
            "rf_src": rf_src,
            "peer_id": peer_id,
            "dst_id": dst_id,
        }
        self.STATUS["RX_START"] = pkt_time
        logger.info(
            "(%s) *START RECORDING* STREAM ID: %s SUB: %s REPEATER: %s TGID %s, TS %s",
            self._system, int_id(stream_id), int_id(rf_src), int_id(peer_id), int_id(dst_id), slot,
        )
        self._schedule_record_max(proto)
        self._schedule_record_idle(proto)

    def _recording_exceeded_max(self, pkt_time: float) -> bool:
        rx_start = self.STATUS.get("RX_START", 0.0)
        return bool(rx_start) and (pkt_time - rx_start) >= _SOURCE_MAX_S

    def _schedule_record_max(self, proto: Any) -> None:
        self._cancel_record_max()
        if not self._recording_active:
            return
        self._max_call = self._call_later(_SOURCE_MAX_S, self._on_record_max_duration, proto)

    def _cancel_record_max(self) -> None:
        if self._max_call is not None and self._max_call.active():
            self._max_call.cancel()
        self._max_call = None

    def _on_record_max_duration(self, proto: Any) -> None:
        self._max_call = None
        if not self._recording_active or not self.CALL_DATA or self._playback_busy:
            return
        call_duration = time() - self.STATUS.get("RX_START", time())
        logger.info(
            "(%s) *END   RECORDING* source timeout %.0fs (HBP max, no VTERM) stream %s",
            self._system, _SOURCE_MAX_S, int_id(self._record_stream),
        )
        self._commit_recording(proto, call_duration)

    def _reset_recording_state(self, slot: int) -> None:
        self._cancel_record_idle()
        self._cancel_record_max()
        self.CALL_DATA = []
        self._recording_active = False
        self._record_stream = b""
        self._seen_record_streams.clear()
        self._ignored_streams_logged.clear()
        self._last_record_time = 0.0
        self._record_ctx = {}
        self._rx_stream_by_slot.pop(slot, None)

    def _start_playback(
        self,
        proto: Any,
        recorded: list[bytes],
        rf_src: bytes,
        peer_id: bytes,
        dst_id: bytes,
        slot: int,
        call_duration: float,
    ) -> None:
        self._delay_call = None
        if not proto or not recorded:
            self._finish_playback()
            return

        self._playback_stream_id = bytes_4(randint(0x00, 0xFFFFFFFF))
        if proto is not None and self._playback_ta_from_stream and hasattr(proto, "copy_ta_stream_buffer"):
            proto.copy_ta_stream_buffer(self._playback_ta_from_stream, self._playback_stream_id)
        self._playback_ta_from_stream = b""
        logger.info(
            "(%s) *START  PLAYBACK* STREAM ID: %s SUB: %s REPEATER: %s TGID %s, TS %s, Duration: %.2f",
            self._system,
            int_id(self._playback_stream_id),
            int_id(rf_src),
            int_id(peer_id),
            int_id(dst_id),
            slot,
            call_duration,
        )
        self._playback_packets = self._prepare_playback_packets(recorded)
        self._playback_index = 0
        self._send_next_packet(proto)

    def _packet_is_voice_burst(self, data: bytes) -> bool:
        if len(data) < 16:
            return False
        bits = data[15]
        frame_type = (bits & 0x30) >> 4
        vseq = bits & 0xF
        return frame_type == HBPF_VOICE or (frame_type != HBPF_DATA_SYNC and vseq in (1, 2, 3, 4))

    def _prepare_playback_packets(self, recorded: list[bytes]) -> list[bytes]:
        """Rewrite stream ID, drop mid-call VHEADs; preserve seq; new stream segment on seq wrap."""
        out: list[bytes] = []
        vhead_template: bytes | None = None
        segment_sid = self._playback_stream_id
        last_voice_seq: int | None = None

        for i, pkt in enumerate(recorded):
            if len(pkt) < 20:
                continue
            if i > 0 and self._packet_is_vhead(pkt):
                logger.debug(
                    "(%s) Skipping mid-call VHEAD in playback (hotspot re-key)",
                    self._system,
                )
                continue
            if self._packet_is_vhead(pkt) and vhead_template is None:
                vhead_template = pkt

            if self._packet_is_voice_burst(pkt) and last_voice_seq is not None:
                curr_seq = pkt[4]
                if curr_seq < last_voice_seq and last_voice_seq >= 200:
                    segment_sid = bytes_4(randint(0x00, 0xFFFFFFFF))
                    logger.info(
                        "(%s) Playback seq wrap (%s -> %s); new stream %s at recorded index %d",
                        self._system,
                        last_voice_seq,
                        curr_seq,
                        int_id(segment_sid),
                        i,
                    )
                    if vhead_template is not None:
                        vhead = bytearray(vhead_template[:16] + segment_sid + vhead_template[20:])
                        out.append(bytes(vhead))

            out.append(pkt[:16] + segment_sid + pkt[20:])
            if self._packet_is_voice_burst(pkt):
                last_voice_seq = pkt[4]

        return out

    def _send_next_packet(self, proto: Any) -> None:
        self._packet_call = None
        if self._playback_index >= len(self._playback_packets):
            logger.info(
                "(%s) *END    PLAYBACK* STREAM ID: %s",
                self._system,
                int_id(self._playback_stream_id),
            )
            self._finish_playback()
            return
        if proto:
            proto.send_system(self._playback_packets[self._playback_index])
        else:
            logger.warning("(%s) Playback packet %d dropped (no protocol/send_system)", self._system, self._playback_index)
        self._playback_index += 1
        self._packet_call = self._call_later(_PACKET_INTERVAL_S, self._send_next_packet, proto)

    def _finish_playback(self) -> None:
        if self._delay_call is not None and self._delay_call.active():
            self._delay_call.cancel()
        if self._packet_call is not None and self._packet_call.active():
            self._packet_call.cancel()
        self._cancel_record_idle()
        self._cancel_record_max()
        self._playback_busy = False
        self._playback_packets = []
        self._playback_index = 0
        self._playback_stream_id = b""
        self._playback_ta_from_stream = b""
        self._delay_call = None
        self._packet_call = None
