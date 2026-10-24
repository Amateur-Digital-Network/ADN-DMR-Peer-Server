# ADN DMR Peer Server - tests routing OBP bridge TX leg timeout
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

"""A per-stream bridge TX leg on a MASTER slot times out when its call loses the terminator,
whatever the last voice burst was."""

from __future__ import annotations

import pytest
from tests.harness.deterministic import (
    DeterministicScenario,
    PacketSpec,
    active_routing_table,
    add_openbridge_system,
    minimal_config,
    patch_routing_wall_time,
)

import adn_server.application.routing.timers as timers_mod
from adn_server.domain import bytes_4, int_id

_TG = 3340
_STREAM = 0x0C0C0C0C


@pytest.mark.parametrize("last_vseq", [1, 2, 3])
def test_a_leg_that_lost_its_terminator_times_out(monkeypatch: pytest.MonkeyPatch, last_vseq: int) -> None:
    config = minimal_config(("MASTER-A",))
    add_openbridge_system(config, "OBP-1")
    sc = DeterministicScenario(config=config, enable_reporting=True)
    sc.seed_routing_table(active_routing_table(_TG, (("MASTER-A", 2), ("OBP-1", 1))))
    spec = PacketSpec(dst_id=_TG, stream_id=_STREAM, rf_src=3340062, slot=1)
    with patch_routing_wall_time(sc.clock):
        sc.inject_obp("OBP-1", DeterministicScenario.voice_head_spec(spec))
        for seq in range(1, last_vseq + 1):
            sc.clock.advance(0.06)
            sc.inject_obp("OBP-1", DeterministicScenario.voice_burst_spec(spec, seq=seq, dtype_vseq=seq))
    legs = sc.protocols["MASTER-A"].STATUS[2]["TX_STREAMS"]
    assert bytes_4(_STREAM) in legs

    sc.clock.advance(6)
    monkeypatch.setattr(timers_mod.time, "time", sc.clock.time)
    sc.routing.stream_trimmer_loop()

    assert bytes_4(_STREAM) not in legs
    ends = [e for e in sc.report_factory.events if e.startswith("GROUP VOICE,END,TX,MASTER-A,")]
    assert [int(e.split(",")[4]) for e in ends] == [int_id(bytes_4(_STREAM))]
