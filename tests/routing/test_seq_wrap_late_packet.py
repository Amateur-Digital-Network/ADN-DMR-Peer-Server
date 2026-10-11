# ADN DMR Peer Server - tests routing sequence wrap
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

"""HBP ingress: a packet that arrives late right after the 8-bit sequence wraps (255 -> 0)
must be dropped without becoming the reference, or the rest of the over is lost."""

from __future__ import annotations

from tests.harness.deterministic import DeterministicScenario, PacketSpec, active_routing_table, parse_dmr_fields

_WRAP_LATE = [s for s in range(240, 256) if s != 254] + [0, 1, 2, 3, 254] + list(range(4, 20))


def test_a_late_packet_after_the_wrap_does_not_drop_the_rest_of_the_over() -> None:
    scenario = DeterministicScenario(routing_table=active_routing_table(91, (("MASTER-A", 2), ("MASTER-B", 2))))
    base = PacketSpec(dst_id=91, stream_id=0x0E0E0E0E, rf_src=2130035, peer_id=1001)
    t = scenario.clock.time()
    scenario.inject_hbp("MASTER-A", DeterministicScenario.voice_head_spec(base), ingress_pkt_time=t)
    for i, seq in enumerate(_WRAP_LATE, start=1):
        spec = DeterministicScenario.voice_burst_spec(base, seq=seq, dtype_vseq=(seq - 1) % 6 + 1)
        scenario.inject_hbp("MASTER-A", spec, ingress_pkt_time=t + 0.06 * i)
    scenario.inject_hbp("MASTER-A", DeterministicScenario.voice_term_spec(base, seq=20),
                        ingress_pkt_time=t + 0.06 * (len(_WRAP_LATE) + 1))

    sent = [parse_dmr_fields(p.packet)["seq"] for p in scenario.capture.for_system("MASTER-B")]
    assert sent[-17:] == list(range(4, 21)), "the frames after the late packet and the terminator are forwarded"
    assert 254 not in sent
