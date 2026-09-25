# ADN DMR Peer Server - announcement synthetic PTT ingress
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
#
# Derived from ADN DMR Server / FreeDMR / HBlink. Original license:
###############################################################################
# Copyright (C) 2026 Joaquin Madrid Belando, EA5GVK <ea5gvk@gmail.com>
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

"""Inject scheduled announcements as a synthetic hotspot PTT on the proxy MASTER."""

from __future__ import annotations

from typing import Any

from ..plugins.domain.send import parse_dmrd_header
from ..proxy.deployment import proxy_target_system
from .helpers import parse_dmrd_burst_fields


def announcement_ptt_system(config: dict[str, Any]) -> str | None:
    """MASTER used for synthetic PTT ingress (same as integrated hotspot proxy target)."""
    target = proxy_target_system(config)
    systems_cfg = config.get("SYSTEMS", {})
    if target:
        sys_cfg = systems_cfg.get(target, {})
        if sys_cfg.get("MODE") == "MASTER" and sys_cfg.get("ENABLED", True):
            return target
    for name, sys_cfg in systems_cfg.items():
        if sys_cfg.get("MODE") != "MASTER":
            continue
        if not sys_cfg.get("ENABLED", True):
            continue
        if sys_cfg.get("PEERS"):
            return name
    return None


def inject_announcement_ptt(
    routing: Any,
    master_system: str,
    pkt: bytes,
    *,
    pkt_time: float,
    server_id: bytes,
) -> bool | None:
    """Feed one DMRD frame through ``dmrd_received`` (HBP path, same as proxy inject)."""
    burst = parse_dmrd_burst_fields(pkt)
    if burst is None:
        return False
    slot, frame_type, dtype_vseq, stream_id, dst_id, call_type = burst
    seq = pkt[4] if len(pkt) > 4 else 0
    rf_src = pkt[5:8] if len(pkt) > 7 else b"\x00\x00\x00"
    peer_id = pkt[11:15] if len(pkt) >= 15 else server_id
    return routing.dmrd_received(
        master_system,
        peer_id,
        rf_src,
        dst_id,
        seq,
        slot,
        call_type,
        frame_type,
        dtype_vseq,
        stream_id,
        pkt,
        ingress_pkt_time=pkt_time,
        synthetic_announcement=True,
    )


def inject_plugin_dmrd(
    routing: Any,
    master_system: str,
    pkt: bytes,
    *,
    pkt_time: float,
    server_id: bytes,
    plugin: str,
) -> bool | None:
    """Feed one plugin frame through ``dmrd_received`` on the same MASTER as announcements.

    The peer field is always the SERVER_ID: a plugin never speaks as a connected peer.
    Group voice is marked ``synthetic_announcement`` exactly like announcements are.
    """
    header = parse_dmrd_header(pkt)
    if header is None:
        return False
    return routing.dmrd_received(
        master_system,
        server_id,
        header.rf_src,
        header.dst_id,
        header.seq,
        header.slot,
        header.call_type,
        header.frame_type,
        header.dtype_vseq,
        header.stream_id,
        pkt,
        ingress_pkt_time=pkt_time,
        synthetic_announcement=header.call_type == "group",
        plugin_origin=plugin,
    )
