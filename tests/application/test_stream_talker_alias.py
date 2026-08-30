# ADN DMR Peer Server - tests stream talker alias helper
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

"""Plugin bridge helper: resolve Talker Alias from buffered DMRA blocks."""

from __future__ import annotations

from adn_server.application.plugins.application.bridge_common import stream_talker_alias
from adn_server.domain import bytes_4
from adn_server.domain.talker_alias import build_dmra_packets, parse_dmra_packet


def test_stream_talker_alias_returns_none_without_callback() -> None:
    assert (
        stream_talker_alias(
            {},
            origin_system="MASTER-A",
            stream_id=1,
            rf_src=2,
            get_dmra_blocks=None,
        )
        is None
    )


def test_stream_talker_alias_decodes_buffered_blocks() -> None:
    rf_src = bytes_4(3120001)
    stream_id = 0x90909090
    blocks: dict[int, bytes] = {}
    for pkt in build_dmra_packets(rf_src, "CE5RPY Test", "utf8"):
        parsed = parse_dmra_packet(pkt)
        assert parsed is not None
        _, block_id, payload = parsed
        blocks[block_id] = payload

    def get_dmra_blocks(system: str, sid: bytes) -> dict[int, bytes] | None:
        if system == "MASTER-A" and sid == bytes_4(stream_id):
            return blocks
        return None

    text = stream_talker_alias(
        {},
        origin_system="MASTER-A",
        stream_id=stream_id,
        rf_src=3120001,
        get_dmra_blocks=get_dmra_blocks,
    )
    assert text == "CE5RPY Test"
