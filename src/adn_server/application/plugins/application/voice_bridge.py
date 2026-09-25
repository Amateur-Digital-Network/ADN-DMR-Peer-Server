# ADN DMR Peer Server - plugin voice event bridge
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

"""Bridge routing → plugin bus (after forward)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from adn_server.domain import HBPF_VOICE, HBPF_VOICE_SYNC, int_id
from adn_server.application.proxy.deployment import is_proxy_inject_only

from ..application.bus import PluginBus
from ..domain.events import CallLegContext, VoiceCallEnd, VoiceCallFrame, VoiceCallStart
from .bridge_common import alias_extra, int_byte, stream_talker_alias

_VOICE_EVENTS = (VoiceCallStart, VoiceCallFrame, VoiceCallEnd)
_ENDED_KEEP = 4096
_STREAM_CTX_KEEP = 512


class VoicePluginBridge:
    def __init__(
        self,
        bus: PluginBus,
        config: dict[str, Any],
        *,
        get_dmra_blocks: Callable[[str, bytes], dict[int, bytes] | None] | None = None,
    ) -> None:
        self._bus = bus
        self._config = config
        self._get_dmra_blocks = get_dmra_blocks
        # Streams whose START / END went out, so each is emitted once. END drops the
        # START key; ended keys are kept, oldest first, only up to _ENDED_KEEP.
        self._plugin_started: set[tuple[str, int]] = set()
        self._plugin_ended: dict[tuple[str, int], None] = {}
        # Per-stream constant part of the event context, oldest first, bounded.
        self._stream_ctx: dict[tuple, dict[str, Any]] = {}

    def update_config(self, config: dict[str, Any]) -> None:
        self._config = config

    def _forget_stream(self, system_name: str, stream_id: bytes) -> None:
        for key in [k for k in self._stream_ctx if k[0] == system_name and k[1] == stream_id]:
            del self._stream_ctx[key]

    def has_subscribers(self) -> bool:
        """True when some subscriber takes voice events at all."""
        return self._bus.wants_any(*_VOICE_EVENTS)

    def _end_extra(
        self,
        *,
        origin_system: str,
        stream_id: int,
        src_id: int,
        base_extra: dict[str, Any],
    ) -> dict[str, Any]:
        extra = dict(base_extra)
        ta = stream_talker_alias(
            self._config,
            origin_system=origin_system,
            stream_id=stream_id,
            rf_src=src_id,
            get_dmra_blocks=self._get_dmra_blocks,
        )
        if ta:
            extra["talker_alias"] = ta
        return extra

    def _group_ctx_base(
        self,
        *,
        system_name: str,
        peer_id: bytes,
        rf_src: bytes,
        dst_id: bytes,
        slot: int,
        stream_id: bytes,
        pkt_time: float,
        source_is_obp: bool,
        obp_hops: bytes,
        obp_source_server: bytes | None,
        obp_ber: bytes,
        obp_rssi: bytes,
        obp_source_rptr: bytes,
        synthetic_announcement: bool,
        forwarded: tuple[str, ...] | list[str],
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # What stays the same for every frame of a stream is resolved once (IDs, mode,
        # proxy flag, aliases); only per-frame fields are recomputed.
        key = (system_name, stream_id, peer_id, rf_src, dst_id, slot, bool(synthetic_announcement))
        fixed = self._stream_ctx.get(key)
        if fixed is None:
            if len(self._stream_ctx) >= _STREAM_CTX_KEEP:
                del self._stream_ctx[next(iter(self._stream_ctx))]
            sys_cfg = self._config.get("SYSTEMS", {}).get(system_name, {})
            fixed = self._stream_ctx[key] = dict(
                call_family="GROUP",
                direction="RX",
                origin_system=system_name,
                system_mode=str(sys_cfg.get("MODE", "MASTER")),
                peer_id=int_id(peer_id),
                src_id=int_id(rf_src),
                dst_id=int_id(dst_id),
                slot=slot,
                stream_id=int_id(stream_id),
                server_id=int_byte(self._config.get("GLOBAL", {}).get("SERVER_ID")) or 0,
                is_synthetic=bool(synthetic_announcement),
                is_proxy_ingress=is_proxy_inject_only(self._config, system_name),
                extra=alias_extra(self._config, peer_id, rf_src, dst_id),
            )
        return dict(
            fixed,
            pkt_time=pkt_time,
            obp_source_server_id=int_byte(obp_source_server) if source_is_obp else None,
            obp_hops=int_byte(obp_hops) if source_is_obp else None,
            obp_source_rptr_id=int_byte(obp_source_rptr) if source_is_obp else None,
            ber=int_byte(obp_ber),
            rssi=int_byte(obp_rssi),
            forwarded_systems=tuple(forwarded),
            extra=dict(extra if extra is not None else fixed["extra"]),  # each event owns its dict
        )

    def emit_group_voice_start(
        self,
        *,
        system_name: str,
        peer_id: bytes,
        rf_src: bytes,
        dst_id: bytes,
        slot: int,
        stream_id: bytes,
        pkt_time: float,
        source_is_obp: bool,
        obp_hops: bytes = b"",
        obp_source_server: bytes | None = None,
        obp_ber: bytes = b"\x00",
        obp_rssi: bytes = b"\x00",
        obp_source_rptr: bytes = b"\x00\x00\x00\x00",
        synthetic_announcement: bool = False,
        forwarded: tuple[str, ...] | list[str] = (),
        voice_phase: str | None = None,
    ) -> bool:
        if not self.has_subscribers():
            return False
        ctx_base = self._group_ctx_base(
            system_name=system_name,
            peer_id=peer_id,
            rf_src=rf_src,
            dst_id=dst_id,
            slot=slot,
            stream_id=stream_id,
            pkt_time=pkt_time,
            source_is_obp=source_is_obp,
            obp_hops=obp_hops,
            obp_source_server=obp_source_server,
            obp_ber=obp_ber,
            obp_rssi=obp_rssi,
            obp_source_rptr=obp_source_rptr,
            synthetic_announcement=synthetic_announcement,
            forwarded=forwarded,
        )
        key = (ctx_base["origin_system"], ctx_base["stream_id"])
        if key in self._plugin_started:
            return False
        self._plugin_started.add(key)
        if voice_phase:
            ctx_base = {**ctx_base, "extra": {**ctx_base["extra"], "voice_phase": voice_phase}}
        self._bus.emit_deferred(VoiceCallStart(context=CallLegContext(**ctx_base)))
        return True

    def emit_group_voice_end(
        self,
        *,
        system_name: str,
        peer_id: bytes,
        rf_src: bytes,
        dst_id: bytes,
        slot: int,
        stream_id: bytes,
        pkt_time: float,
        duration_s: float,
        source_is_obp: bool,
        obp_hops: bytes = b"",
        obp_source_server: bytes | None = None,
        obp_ber: bytes = b"\x00",
        obp_rssi: bytes = b"\x00",
        obp_source_rptr: bytes = b"\x00\x00\x00\x00",
        synthetic_announcement: bool = False,
        forwarded: tuple[str, ...] | list[str] = (),
    ) -> bool:
        if not self.has_subscribers():
            return False
        ctx_base = self._group_ctx_base(
            system_name=system_name,
            peer_id=peer_id,
            rf_src=rf_src,
            dst_id=dst_id,
            slot=slot,
            stream_id=stream_id,
            pkt_time=pkt_time,
            source_is_obp=source_is_obp,
            obp_hops=obp_hops,
            obp_source_server=obp_source_server,
            obp_ber=obp_ber,
            obp_rssi=obp_rssi,
            obp_source_rptr=obp_source_rptr,
            synthetic_announcement=synthetic_announcement,
            forwarded=forwarded,
        )
        key = (ctx_base["origin_system"], ctx_base["stream_id"])
        if key in self._plugin_ended:
            return False
        self._plugin_ended[key] = None
        self._plugin_started.discard(key)
        self._forget_stream(system_name, stream_id)
        if len(self._plugin_ended) > _ENDED_KEEP:
            del self._plugin_ended[next(iter(self._plugin_ended))]
        end_extra = self._end_extra(
            origin_system=ctx_base["origin_system"],
            stream_id=ctx_base["stream_id"],
            src_id=ctx_base["src_id"],
            base_extra=ctx_base["extra"],
        )
        self._bus.emit_deferred(
            VoiceCallEnd(
                context=CallLegContext(**{**ctx_base, "extra": end_extra}),
                duration_s=duration_s,
            )
        )
        return True

    def notify_group_after_forward(
        self,
        *,
        system_name: str,
        peer_id: bytes,
        rf_src: bytes,
        dst_id: bytes,
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
        synthetic_announcement: bool,
        forwarded: list[str],
    ) -> None:
        # Once per voice frame: skip building the event unless someone takes it.
        if frame_type not in (HBPF_VOICE, HBPF_VOICE_SYNC) or not self._bus.wants(VoiceCallFrame):
            return
        ctx_base = self._group_ctx_base(
            system_name=system_name,
            peer_id=peer_id,
            rf_src=rf_src,
            dst_id=dst_id,
            slot=slot,
            stream_id=stream_id,
            pkt_time=pkt_time,
            source_is_obp=source_is_obp,
            obp_hops=obp_hops,
            obp_source_server=obp_source_server,
            obp_ber=obp_ber,
            obp_rssi=obp_rssi,
            obp_source_rptr=obp_source_rptr,
            synthetic_announcement=synthetic_announcement,
            forwarded=forwarded,
        )
        dmrpkt = data[20:53] if len(data) >= 53 else b""
        event = VoiceCallFrame(
            context=CallLegContext(**ctx_base),
            dmrpkt=dmrpkt,
            frame_type=frame_type,
            dtype_vseq=dtype_vseq,
        )
        self._bus.emit_deferred(event)

    def notify_private_after_forward(
        self,
        *,
        system_name: str,
        peer_id: bytes,
        rf_src: bytes,
        dst_id: bytes,
        slot: int,
        frame_type: int,
        dtype_vseq: int,
        stream_id: bytes,
        data: bytes,
        pkt_time: float,
        synthetic_announcement: bool,
        forwarded_targets: list[str],
        phase: str,
        duration_s: float = 0.0,
    ) -> None:
        wanted = self._bus.wants(VoiceCallFrame) if phase == "FRAME" else self.has_subscribers()
        if not wanted:
            return
        systems_cfg = self._config.get("SYSTEMS", {})
        mode = systems_cfg.get(system_name, {}).get("MODE", "MASTER")
        server_id = int_byte(self._config.get("GLOBAL", {}).get("SERVER_ID")) or 0
        ctx = CallLegContext(
            call_family="PRIVATE",
            direction="RX",
            origin_system=system_name,
            system_mode=str(mode),
            peer_id=int_id(peer_id),
            src_id=int_id(rf_src),
            dst_id=int_id(dst_id),
            slot=slot,
            stream_id=int_id(stream_id),
            server_id=server_id,
            is_synthetic=bool(synthetic_announcement),
            is_proxy_ingress=is_proxy_inject_only(self._config, system_name),
            pkt_time=pkt_time,
            forwarded_systems=tuple(forwarded_targets),
            extra={**alias_extra(self._config, peer_id, rf_src, dst_id), "private_phase": phase},
        )
        if phase == "START":
            self._bus.emit(VoiceCallStart(context=ctx))
        elif phase == "FRAME":
            dmrpkt = data[20:53] if len(data) >= 53 else b""
            self._bus.emit_deferred(
                VoiceCallFrame(
                    context=ctx,
                    dmrpkt=dmrpkt,
                    frame_type=frame_type,
                    dtype_vseq=dtype_vseq,
                )
            )
        elif phase == "END":
            end_extra = self._end_extra(
                origin_system=system_name,
                stream_id=int_id(stream_id),
                src_id=int_id(rf_src),
                base_extra=ctx.extra,
            )
            self._bus.emit(
                VoiceCallEnd(
                    context=replace(ctx, extra=end_extra),
                    duration_s=duration_s,
                )
            )
