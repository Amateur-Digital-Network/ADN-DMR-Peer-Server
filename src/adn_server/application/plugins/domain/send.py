# ADN DMR Peer Server - plugin send rules
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

"""What a plugin may send, and the per-plugin permission read from ``PLUGINS.send``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NamedTuple

from ....domain import HBPF_DATA_SYNC

# CSBK, data header, rate 1/2 and rate 3/4 data blocks: ARS, LRRP, SMS and the like.
PLUGIN_SENDABLE_DTYPES = frozenset({3, 6, 7, 8})
DEFAULT_MAX_FRAMES_PER_S = 40.0


class DmrdHeader(NamedTuple):
    seq: int
    rf_src: bytes
    dst_id: bytes
    peer_id: bytes
    slot: int
    call_type: str
    frame_type: int
    dtype_vseq: int
    stream_id: bytes


def parse_dmrd_header(pkt: bytes) -> DmrdHeader | None:
    """The HBP DMRD header fields of any call type (group, vcsbk or unit)."""
    if len(pkt) < 53 or pkt[:4] != b"DMRD":
        return None
    bits = pkt[15]
    if bits & 0x40:
        call_type = "unit"
    elif (bits & 0x23) == 0x23:
        call_type = "vcsbk"
    else:
        call_type = "group"
    return DmrdHeader(
        seq=pkt[4],
        rf_src=pkt[5:8],
        dst_id=pkt[8:11],
        peer_id=pkt[11:15],
        slot=2 if bits & 0x80 else 1,
        call_type=call_type,
        frame_type=(bits & 0x30) >> 4,
        dtype_vseq=bits & 0xF,
        stream_id=pkt[16:20],
    )


def is_plugin_sendable(call_type: str, frame_type: int, dtype_vseq: int) -> bool:
    """Plugins send unit data only (no voice) in this version."""
    return call_type == "unit" and frame_type == HBPF_DATA_SYNC and dtype_vseq in PLUGIN_SENDABLE_DTYPES


@dataclass(frozen=True)
class SendPermission:
    allowed_src_ids: frozenset[int]
    max_frames_per_s: float


def send_permission(server_config: dict[str, Any], plugin: str) -> SendPermission | None:
    """The plugin's entry in ``PLUGINS.send``, or None when it may not send.

    An entry without source IDs grants nothing: the allowlist is what stops a
    plugin from sending as a radio.
    """
    plugins_cfg = server_config.get("PLUGINS") or {}
    if plugins_cfg.get("master_kill"):
        return None
    entry = (plugins_cfg.get("send") or {}).get(plugin)
    if not isinstance(entry, dict):
        return None
    try:
        ids = frozenset(int(i) for i in entry.get("allowed_src_ids") or ())
        rate = float(entry.get("max_frames_per_s", DEFAULT_MAX_FRAMES_PER_S))
    except (TypeError, ValueError):
        return None
    if not ids or rate <= 0:
        return None
    return SendPermission(ids, rate)
