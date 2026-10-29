# ADN DMR Peer Server - tests routing hbp source without protocol
#
# Copyright (C) 2026  Rodrigo Pérez, CE5RPY <ce5rpy@qmd.cl>
# Copyright (C) 2026  Joaquin Madrid Belando, EA5GVK <ea5gvk@gmail.com>
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

"""An HBP source with no protocol has no slot row: its group call skips packet control, as legacy does."""

from __future__ import annotations

from tests.harness.deterministic import DeterministicScenario, PacketSpec, active_routing_table, minimal_config

from adn_server.domain import bytes_4


def test_a_group_call_from_a_system_without_a_protocol_is_still_routed() -> None:
    sc = DeterministicScenario(
        config=minimal_config(("MASTER-A", "MASTER-B")),
        routing_table=active_routing_table(91, (("MASTER-A", 2), ("MASTER-B", 2))),
    )
    del sc.protocols["MASTER-A"]
    spec = PacketSpec(peer_id=3140001, rf_src=3140101, dst_id=91, stream_id=0x0A0A0A01)

    for frame in (DeterministicScenario.voice_head_spec(spec), DeterministicScenario.voice_burst_spec(spec, seq=1)):
        sc.clock.advance(0.06)
        sc.inject_hbp("MASTER-A", frame)

    sid = bytes_4(spec.stream_id)
    assert [p.fields["seq"] for p in sc.capture.for_system("MASTER-B") if p.fields["stream_id"] == sid] == [0, 1]
