# ADN DMR Peer Server - plugin bridge helpers
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

"""Shared helpers for voice/data plugin bridges."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from adn_server.domain import bytes_4, int_id

UNIT_DATA_DTYPE_LABELS: dict[int, str] = {
    3: "UNIT CSBK",
    6: "UNIT DATA HEADER",
    7: "UNIT VCSBK 1/2 DATA BLOCK",
    8: "UNIT VCSBK 3/4 DATA BLOCK",
}


def unit_data_label(dtype_vseq: int) -> str:
    return UNIT_DATA_DTYPE_LABELS.get(dtype_vseq, "UNIT DATA")


def int_byte(val: bytes | int | None) -> int | None:
    if val is None:
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, bytes) and val:
        return int.from_bytes(val, "big")
    return None


def alias_extra(
    config: dict[str, Any],
    peer_id: bytes,
    rf_src: bytes,
    dst_id: bytes,
) -> dict[str, Any]:
    peer_ids = config.get("_PEER_IDS") or {}
    sub_ids = config.get("_SUB_IDS") or {}
    tg_ids = config.get("_TG_IDS") or {}
    server_ids = config.get("_SERVER_IDS") or {}
    extra: dict[str, Any] = {}
    pid = int_id(peer_id)
    sid = int_id(rf_src)
    did = int_id(dst_id)
    if pid in peer_ids:
        extra["peer_callsign"] = peer_ids[pid]
    if sid in sub_ids:
        extra["src_callsign"] = sub_ids[sid]
    if did in tg_ids:
        extra["dst_name"] = tg_ids[did]
    srv = int_byte(config.get("GLOBAL", {}).get("SERVER_ID"))
    if srv is not None:
        server_name = server_ids.get(str(srv))
        if server_name:
            extra["server_name"] = server_name
    return extra


def stream_talker_alias(
    config: dict[str, Any],
    *,
    origin_system: str,
    stream_id: int,
    rf_src: int,
    get_dmra_blocks: Callable[[str, bytes], dict[int, bytes] | None] | None,
) -> str | None:
    """Decoded Talker Alias for a voice stream, when buffered on the origin system."""
    if get_dmra_blocks is None:
        return None
    blocks = get_dmra_blocks(origin_system, bytes_4(stream_id))
    if not blocks:
        return None
    from adn_server.application.talker_alias_use_cases import passthrough_complete
    from adn_server.domain.talker_alias import decode_ta_from_blocks

    if not passthrough_complete(blocks):
        return None
    text = decode_ta_from_blocks(blocks)
    return text or None
