# ADN DMR Peer Server - tests routing mesh announcements on one MASTER slot
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

"""Another server's announcements reach a MASTER like any mesh voice.

Seen on 2131 at the top of the hour: one server sends its announcements for TG 9140 and
TG 7144 at once, both as 1000001. Both are bridged to TS2 of a MASTER with several
hotspots. Taking them for this server's own broadcast applied the global slot hold, so
each took the slot from the other and a hotspot heard 52 of 267 frames of one of them.
"""

from __future__ import annotations

import collections

import pytest
from tests.harness.deterministic import (
    DeterministicScenario,
    PacketSpec,
    active_routing_table,
    add_openbridge_system,
    minimal_config,
    parse_dmr_fields,
    patch_routing_wall_time,
)
from tests.routing.unit_data_helpers import idle_hbp_slot

_FRAMES = 60


def _sent_to_master(rf_src_a: int, rf_src_b: int) -> dict[int, int]:
    config = minimal_config(("SYSTEM",))
    config["SYSTEMS"]["SYSTEM"]["PEERS"] = {
        b"\x00\x00\x03\xe9": {"CALLSIGN": "HS1", "CONNECTION": "YES"},
        b"\x00\x00\x03\xea": {"CALLSIGN": "HS2", "CONNECTION": "YES"},
    }
    add_openbridge_system(config, "OBP-1")
    table = active_routing_table(9140, (("SYSTEM", 2), ("OBP-1", 1)), timeout_minutes=10**6)
    table.update(active_routing_table(7144, (("SYSTEM", 2), ("OBP-1", 1)), timeout_minutes=10**6))
    sc = DeterministicScenario(config=config, routing_table=table)
    sc.routing.apply_startup_subscriptions()
    sc.protocols["SYSTEM"].STATUS[2] = idle_hbp_slot()
    a = PacketSpec(rf_src=rf_src_a, dst_id=9140, peer_id=26811, slot=1, stream_id=0x0A0A0A0A)
    b = PacketSpec(rf_src=rf_src_b, dst_id=7144, peer_id=26811, slot=1, stream_id=0x0B0B0B0B)
    with patch_routing_wall_time(sc.clock):
        sc.inject_obp("OBP-1", DeterministicScenario.voice_head_spec(a))
        sc.clock.advance(0.03)
        sc.inject_obp("OBP-1", DeterministicScenario.voice_head_spec(b))
        for i in range(1, _FRAMES):
            for spec in (a, b):
                sc.clock.advance(0.03)
                sc.inject_obp("OBP-1", DeterministicScenario.voice_burst_spec(spec, seq=i, dtype_vseq=(i - 1) % 6 + 1))
    got = collections.Counter(
        int.from_bytes(parse_dmr_fields(p.packet)["stream_id"], "big") for p in sc.capture.for_system("SYSTEM")
    )
    return dict(got)


@pytest.mark.parametrize(("src_a", "src_b"), [(1000001, 1000001), (3340062, 7140099)])
def test_two_mesh_calls_on_one_master_slot_are_both_delivered_whole(src_a: int, src_b: int) -> None:
    """Each hotspot keeps the stream it hears first; routing must not starve either."""
    assert _sent_to_master(src_a, src_b) == {0x0A0A0A0A: _FRAMES, 0x0B0B0B0B: _FRAMES}
