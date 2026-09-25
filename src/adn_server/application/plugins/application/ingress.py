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
from ...routing.helpers import slot_voice_held_by_other_stream
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
    ) -> None:
        self._routing = routing
        self._config = config
        self._get_protocols = get_protocols
        self._send_local = send_local
        self._clock = clock

    def set_routing(self, routing: Any) -> None:
        """Bootstrap builds the plugin manager before routing; routing is set before any load."""
        self._routing = routing

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
        if slot_voice_held_by_other_stream(slot, header.stream_id, now):
            return False
        if self._route(master, pkt, now, server_id, plugin) is not True:
            self._release(slot, header.stream_id)
            return False
        is_term = header.frame_type == HBPF_DATA_SYNC and header.dtype_vseq == HBPF_SLT_VTERM
        slot["TX_TYPE"] = HBPF_SLT_VTERM if is_term else HBPF_SLT_VHEAD
        slot["TX_STREAM_ID"] = header.stream_id
        slot["TX_RFS"] = header.rf_src
        slot["TX_TGID"] = header.dst_id
        slot["TX_TIME"] = now
        self._send_local(master, pkt)
        return True

    def _route(self, master: str, pkt: bytes, now: float, server_id: bytes, plugin: str) -> bool | None:
        return inject_plugin_dmrd(self._routing, master, pkt, pkt_time=now, server_id=server_id, plugin=plugin)

    @staticmethod
    def _release(slot: dict[str, Any], stream_id: bytes) -> None:
        if slot.get("TX_STREAM_ID") == stream_id:
            slot["TX_TYPE"] = HBPF_SLT_VTERM

    def _server_id(self) -> bytes:
        return server_id_bytes(self._config.get("GLOBAL", {}).get("SERVER_ID"))[:4]
