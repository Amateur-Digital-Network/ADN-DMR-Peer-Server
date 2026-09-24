# ADN DMR Peer Server - plugin frame ingress
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

"""Where a plugin's frames enter the server: the announcement MASTER, as a synthetic ingress."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from ....domain import HBPF_DATA_SYNC, HBPF_SLT_VHEAD, HBPF_SLT_VTERM
from ....domain.mesh_engine import server_id_bytes
from ...routing.announcement_ptt_inject import announcement_ptt_system, inject_plugin_dmrd
from ...routing.helpers import master_dynamic_tg_slots, slot_voice_held_by_other_stream
from ..domain.send import GROUP_VOICE, UNIT_DATA, parse_dmrd_header, plugin_frame_kind

logger = logging.getLogger(__name__)


class PluginIngress:
    """Routes plugin frames on the reactor thread and reports whether each was accepted.

    Unit data goes through the unit data path only (see ``dmrd_received(plugin_origin=)``).

    Group voice is routed like a scheduled announcement: through the bridges (OpenBridge
    legs included, as for any local ingress), and out to the hotspots of the MASTER it
    enters on. While a plugin's stream plays it holds that MASTER slot (TX_TYPE=VHEAD,
    TX_STREAM_ID, TX_RFS), so routed voice finds it busy; a radio or another stream on the
    slot makes the frame fail, which tells the plugin to stop. The terminator frees it.
    """

    def __init__(
        self,
        routing: Any,
        config: dict[str, Any],
        get_protocols: Callable[[], dict[str, Any]],
        send_local: Callable[[str, bytes], None],
        clock: Callable[[], float] = time.time,
        send_routing_event: Callable[[str], None] | None = None,
    ) -> None:
        self._routing = routing
        self._config = config
        self._get_protocols = get_protocols
        self._send_local = send_local
        self._clock = clock
        self._send_routing_event = send_routing_event
        # Plugin voice streams on air: stream_id -> (master, slot, tg, rf_src, start).
        self._on_air: dict[bytes, tuple[str, int, int, int, float]] = {}

    def set_routing(self, routing: Any) -> None:
        """Bootstrap builds the plugin manager before routing; routing is set before any load."""
        self._routing = routing

    def voice_slot_for_tg(self, tg: int) -> int | None:
        """The MASTER slot a plugin should speak ``tg`` on now, or None while every slot is busy.

        Same choice as scheduled announcements: the slot where ``tg`` is a dynamic UA
        session or has an active bridge leg on that MASTER first, then TS2, then TS1.
        """
        master = announcement_ptt_system(self._config)
        proto = self._get_protocols().get(master) if master else None
        status = getattr(proto, "STATUS", None)
        sys_cfg = self._config.get("SYSTEMS", {}).get(master or "", {})
        if not status or sys_cfg.get("MODE") != "MASTER":
            return None
        dynamic = master_dynamic_tg_slots(sys_cfg, int(tg))
        bridged = self._active_bridge_slots(int(tg), master)
        now = self._clock()
        for ts in dict.fromkeys([*sorted(dynamic, reverse=True), *sorted(bridged, reverse=True), 2, 1]):
            slot = status.get(ts)
            if slot and not self._slot_busy(slot, ts in dynamic or ts in bridged, now):
                return ts
        return None

    @staticmethod
    def _slot_busy(slot: dict[str, Any], tg_lives_here: bool, now: float) -> bool:
        if slot_voice_held_by_other_stream(slot, b"", now):
            return True  # live voice on the slot, in or out
        if slot.get("RX_TYPE") != HBPF_SLT_VTERM and slot.get("TX_TYPE") == HBPF_SLT_VTERM:
            return True  # an outside QSO still in its hang time
        if tg_lives_here:
            return False
        return not (slot.get("RX_TYPE") == HBPF_SLT_VTERM and slot.get("TX_TYPE") == HBPF_SLT_VTERM)

    def _active_bridge_slots(self, tg: int, master: str) -> set[int]:
        table = self._routing.routing_table_for_report() if self._routing is not None else {}
        return {
            int(be["TS"])
            for be in table.get(str(tg), [])
            if isinstance(be, dict) and be.get("SYSTEM") == master and be.get("ACTIVE") and be.get("TS") is not None
        }

    def deliver(self, pkt: bytes, plugin: str) -> bool:
        header = parse_dmrd_header(pkt)
        kind = plugin_frame_kind(header.call_type, header.frame_type, header.dtype_vseq) if header else None
        master = announcement_ptt_system(self._config)
        if kind is None or not master:
            if not master:
                logger.warning("(PLUGIN) %s: no MASTER to send from, frame dropped", plugin)
            return False
        server_id = self._server_id()
        now = self._clock()
        if kind == UNIT_DATA:
            return self._route(master, pkt, now, server_id, plugin) is not False
        return self._group_voice(master, header, pkt[:11] + server_id + pkt[15:], now, server_id, plugin)

    def _group_voice(self, master: str, header: Any, pkt: bytes, now: float, server_id: bytes, plugin: str) -> bool:
        proto = self._get_protocols().get(master)
        slot = getattr(proto, "STATUS", {}).get(header.slot) if proto is not None else None
        if slot is None:
            return False
        if slot_voice_held_by_other_stream(slot, header.stream_id, now) or (
            self._route(master, pkt, now, server_id, plugin) is not True
        ):
            self._release(slot, header.stream_id)
            self._off_air(header.stream_id, now)
            return False
        is_term = header.frame_type == HBPF_DATA_SYNC and header.dtype_vseq == HBPF_SLT_VTERM
        slot["TX_TYPE"] = HBPF_SLT_VTERM if is_term else HBPF_SLT_VHEAD
        slot["TX_STREAM_ID"] = header.stream_id
        slot["TX_RFS"] = header.rf_src
        slot["TX_TGID"] = header.dst_id
        slot["TX_TIME"] = now
        self._send_local(master, pkt)
        if header.stream_id not in self._on_air:
            self._on_air_start(master, header, now)
        if is_term:
            self._off_air(header.stream_id, now)
        return True

    def _on_air_start(self, master: str, header: Any, now: float) -> None:
        """Monitor TX on the MASTER itself: its hotspots hear the stream, which no bridge leg reports."""
        if len(self._on_air) >= 64:  # plugins that never sent a terminator
            for stream_id in list(self._on_air)[:32]:
                self._off_air(stream_id, now)
        tg, rf_src = int_id(header.dst_id), int_id(header.rf_src)
        self._on_air[header.stream_id] = (master, header.slot, tg, rf_src, now)
        self._report("START", master, header.stream_id, header.slot, tg, rf_src)

    def _off_air(self, stream_id: bytes, now: float) -> None:
        entry = self._on_air.pop(stream_id, None)
        if entry is not None:
            master, slot, tg, rf_src, start = entry
            self._report("END", master, stream_id, slot, tg, rf_src, now - start)

    def _report(
        self, action: str, master: str, stream_id: bytes, slot: int, tg: int, rf_src: int, duration: float | None = None
    ) -> None:
        # Same line announcements send (VoiceUseCases._emit_announcement_voice_event).
        if self._send_routing_event is None:
            return
        parts = ["GROUP VOICE", action, "TX", master, str(int_id(stream_id)), str(rf_src), str(rf_src), str(slot), str(tg)]
        if duration is not None:
            parts.append(f"{duration:.2f}")
        parts.append("1")
        self._send_routing_event(",".join(parts))

    def _route(self, master: str, pkt: bytes, now: float, server_id: bytes, plugin: str) -> bool | None:
        return inject_plugin_dmrd(self._routing, master, pkt, pkt_time=now, server_id=server_id, plugin=plugin)

    @staticmethod
    def _release(slot: dict[str, Any], stream_id: bytes) -> None:
        if slot.get("TX_STREAM_ID") == stream_id:
            slot["TX_TYPE"] = HBPF_SLT_VTERM

    def _server_id(self) -> bytes:
        return server_id_bytes(self._config.get("GLOBAL", {}).get("SERVER_ID"))[:4]
