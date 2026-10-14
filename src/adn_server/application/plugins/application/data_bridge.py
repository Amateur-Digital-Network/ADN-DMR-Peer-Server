# ADN DMR Peer Server - plugin unit data event bridge
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

"""Bridge unit-data routing → plugin bus (after forward)."""

from __future__ import annotations

from typing import Any

from adn_server.domain import int_id
from adn_server.application.proxy.deployment import is_proxy_inject_only

from ..application.bus import PluginBus
from ..domain.events import CallLegContext, UnitDataEnd, UnitDataFrame, UnitDataStart
from .bridge_common import alias_extra, int_byte, unit_data_label

_DATA_EVENTS = (UnitDataStart, UnitDataFrame, UnitDataEnd)


class DataPluginBridge:
    """Emits unit-data events to PluginBus after routing forward."""

    def __init__(self, bus: PluginBus, config: dict[str, Any]) -> None:
        self._bus = bus
        self._config = config
        # (origin_system, slot) -> (stream_id, start_pkt_time)
        self._active_streams: dict[tuple[str, int], tuple[int, float, bool]] = {}

    def update_config(self, config: dict[str, Any]) -> None:
        self._config = config

    def notify_after_forward(
        self,
        *,
        route: str,
        system_name: str,
        peer_id: bytes,
        rf_src: bytes,
        dst_id: bytes,
        seq: int,
        slot: int,
        frame_type: int,
        dtype_vseq: int,
        stream_id: bytes,
        data: bytes,
        pkt_time: float,
        source_is_obp: bool,
        obp_hops: bytes,
        obp_source_server: bytes | None,
        obp_ber: bytes,
        obp_rssi: bytes,
        obp_source_rptr: bytes,
        forwarded: list[str],
        synthetic: bool = False,
    ) -> None:
        """``synthetic``: the frame was sent by a plugin (``ServerContext.send_dmrd``)."""
        if not self._bus.wants_any(*_DATA_EVENTS):
            return
        sid = int_id(stream_id)
        stream_key = (system_name, slot)
        prev = self._active_streams.get(stream_key)
        if prev is not None and prev[0] != sid:
            self._emit_end(system_name, slot, pkt_time, prev[0], prev[1], prev[2])
        is_new_stream = prev is None or prev[0] != sid
        if is_new_stream:
            self._active_streams[stream_key] = (sid, pkt_time, synthetic)
        starts = is_new_stream or dtype_vseq == 6
        if not (starts and self._bus.wants(UnitDataStart)) and not self._bus.wants(UnitDataFrame):
            return  # stream tracked for UnitDataEnd; nothing else to build
        systems_cfg = self._config.get("SYSTEMS", {})
        mode = systems_cfg.get(system_name, {}).get("MODE", "MASTER")
        server_id = int_byte(self._config.get("GLOBAL", {}).get("SERVER_ID")) or 0
        bits = data[15] if len(data) > 15 else 0
        dmrpkt = data[20:53] if len(data) >= 53 else b""
        ctx = CallLegContext(
            call_family="DATA",
            direction="RX",
            origin_system=system_name,
            system_mode=str(mode),
            peer_id=int_id(peer_id),
            src_id=int_id(rf_src),
            dst_id=int_id(dst_id),
            slot=slot,
            stream_id=sid,
            server_id=server_id,
            is_synthetic=synthetic,
            is_proxy_ingress=is_proxy_inject_only(self._config, system_name),
            pkt_time=pkt_time,
            obp_source_server_id=int_byte(obp_source_server) if source_is_obp else None,
            obp_hops=int_byte(obp_hops) if source_is_obp else None,
            obp_source_rptr_id=int_byte(obp_source_rptr) if source_is_obp else None,
            ber=int_byte(obp_ber),
            rssi=int_byte(obp_rssi),
            forwarded_systems=tuple(forwarded),
            extra={
                **alias_extra(self._config, peer_id, rf_src, dst_id),
                "route": route,
                "data_label": unit_data_label(dtype_vseq),
                "dtype_vseq": dtype_vseq,
            },
        )
        label = unit_data_label(dtype_vseq)
        if starts:
            self._bus.emit(
                UnitDataStart(
                    context=ctx,
                    dtype_vseq=dtype_vseq,
                    data_label=label,
                    seq=seq,
                    frame_type=frame_type,
                )
            )
        frame = UnitDataFrame(
            context=ctx,
            dmrpkt=dmrpkt,
            raw_data=data,
            dtype_vseq=dtype_vseq,
            data_label=label,
            seq=seq,
            frame_type=frame_type,
            bits=bits,
        )
        self._bus.emit_deferred(frame)

    def _emit_end(
        self,
        system_name: str,
        slot: int,
        pkt_time: float,
        stream_id: int,
        start_time: float,
        synthetic: bool = False,
    ) -> None:
        duration = max(0.0, pkt_time - start_time)
        systems_cfg = self._config.get("SYSTEMS", {})
        mode = systems_cfg.get(system_name, {}).get("MODE", "MASTER")
        server_id = int_byte(self._config.get("GLOBAL", {}).get("SERVER_ID")) or 0
        ctx = CallLegContext(
            call_family="DATA",
            direction="RX",
            origin_system=system_name,
            system_mode=str(mode),
            peer_id=0,
            src_id=0,
            dst_id=0,
            slot=slot,
            stream_id=stream_id,
            server_id=server_id,
            is_synthetic=synthetic,
            is_proxy_ingress=is_proxy_inject_only(self._config, system_name),
            pkt_time=pkt_time,
        )
        self._bus.emit(UnitDataEnd(context=ctx, duration_s=duration))
