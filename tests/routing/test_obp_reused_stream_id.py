# ADN DMR Peer Server - tests routing obp reused stream id
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

"""A peer that reuses a stream_id for another destination.

Seen in production: one bridge sent stream 128 to three destinations in a day and
another reused one id across five. STATUS is keyed by stream_id alone and the
trimmer only drops a row after 180s idle, so the second destination landed on the
first one's row, failed the TGID check in loop control and was discarded frame by
frame — 116 frames to one talkgroup that never got a single call through.
to_target already evicts a stale row on a forward leg; ingress did not.
"""

from __future__ import annotations

import pytest
from tests.harness.assertions import assert_forwarded, packets_to
from tests.harness.deterministic import (
    DeterministicScenario,
    PacketSpec,
    add_openbridge_system,
    minimal_config,
    patch_routing_wall_time,
)

from adn_server.application.routing.obp_forward import OBP_REUSED_STREAM_IDLE_S

_STREAM = 128  # as the peer really sends it
_TG_FIRST = 71401
_TG_SECOND = 71411


def _bridge_row(system: str, tgid: int) -> dict:
    from adn_server.domain import bytes_3

    tg_b = bytes_3(tgid)
    return {
        "SYSTEM": system, "TS": 1, "TGID": tg_b, "ACTIVE": True,
        "TIMEOUT": 3600.0, "TO_TYPE": "ON", "ON": [tg_b], "OFF": [], "RESET": [], "TIMER": 0.0,
    }


def _scenario() -> DeterministicScenario:
    config = minimal_config(())
    add_openbridge_system(config, "OBP-IN")
    add_openbridge_system(config, "OBP-OUT")
    bridges = {
        str(_TG_FIRST): [_bridge_row("OBP-IN", _TG_FIRST), _bridge_row("OBP-OUT", _TG_FIRST)],
        str(_TG_SECOND): [_bridge_row("OBP-IN", _TG_SECOND), _bridge_row("OBP-OUT", _TG_SECOND)],
    }
    scenario = DeterministicScenario(config=config, routing_table=bridges)
    scenario.routing._finalize_routing_state()
    return scenario


@pytest.mark.behavior
def test_a_reused_stream_id_reaches_its_new_destination() -> None:
    """The production timeline: TG 71401 goes quiet, and 45s later TG 71411 arrives
    on the same stream id. It used to be dropped for the rest of the 180s window.
    """
    scenario = _scenario()
    with patch_routing_wall_time(scenario.clock):
        first = PacketSpec(dst_id=_TG_FIRST, stream_id=_STREAM, slot=1)
        scenario.inject_obp("OBP-IN", DeterministicScenario.voice_head_spec(first))
        assert_forwarded(scenario, "OBP-OUT", count=1, dst_id=_TG_FIRST)

        scenario.clock.advance(45.0)
        second = PacketSpec(dst_id=_TG_SECOND, stream_id=_STREAM, slot=1)
        scenario.inject_obp("OBP-IN", DeterministicScenario.voice_head_spec(second))

    got = packets_to(scenario, "OBP-OUT")
    from adn_server.domain import int_id

    assert [int_id(p.fields["dst_id"]) for p in got] == [_TG_FIRST, _TG_SECOND]


@pytest.mark.behavior
def test_an_interleaved_stream_id_does_not_evict_the_live_call() -> None:
    """Without the idle rule each frame would drop the other call's row, and both
    would lose their packet counters, LC and loss tracking.
    """
    scenario = _scenario()
    with patch_routing_wall_time(scenario.clock):
        first = PacketSpec(dst_id=_TG_FIRST, stream_id=_STREAM, slot=1)
        scenario.inject_obp("OBP-IN", DeterministicScenario.voice_head_spec(first))

        second = PacketSpec(dst_id=_TG_SECOND, stream_id=_STREAM, slot=1)
        for _ in range(8):
            scenario.clock.advance(0.06)  # one voice frame apart
            scenario.inject_obp("OBP-IN", DeterministicScenario.voice_head_spec(second))

    status = scenario.protocols["OBP-IN"].STATUS
    from adn_server.domain import bytes_4, int_id

    row = status.get(bytes_4(_STREAM))
    assert row is not None
    assert int_id(row["TGID"]) == _TG_FIRST


def test_the_idle_threshold_is_well_above_one_voice_frame() -> None:
    assert OBP_REUSED_STREAM_IDLE_S > 0.06
