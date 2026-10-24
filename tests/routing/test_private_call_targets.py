# ADN DMR Peer Server - tests routing private call targets
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

"""Two private calls at once on different systems each keep their own targets."""

from __future__ import annotations

import pytest
from tests.harness.deterministic import DeterministicScenario, PacketSpec, minimal_config

from adn_server.domain import bytes_3

_X = 7123456  # heard on MASTER-C
_Y = 7654321  # heard on MASTER-D


def _two_calls() -> tuple[DeterministicScenario, PacketSpec, PacketSpec]:
    config = minimal_config(("MASTER-A", "MASTER-B", "MASTER-C", "MASTER-D"))
    config["_SUB_MAP"] = {
        bytes_3(_X): ("MASTER-C", 2, 1000.0),
        bytes_3(_Y): ("MASTER-D", 2, 1000.0),
    }
    scenario = DeterministicScenario(config=config)
    call_1 = PacketSpec(call_type="unit", peer_id=1001, rf_src=3120001, dst_id=_X, stream_id=0x0A0A0A0A)
    call_2 = PacketSpec(call_type="unit", peer_id=1002, rf_src=3120002, dst_id=_Y, stream_id=0x0B0B0B0B)
    scenario.inject_unit("MASTER-A", DeterministicScenario.unit_voice_head_spec(call_1))
    scenario.inject_unit("MASTER-B", DeterministicScenario.unit_voice_head_spec(call_2))
    return scenario, call_1, call_2


def _stream_ids(scenario: DeterministicScenario, system: str) -> set[bytes]:
    return {p.fields["stream_id"] for p in scenario.capture.for_system(system)}


@pytest.mark.behavior
def test_two_overlapping_private_calls_keep_their_own_targets() -> None:
    """Call 2's header must not re-target call 1's frames to call 2's destination."""
    scenario, call_1, call_2 = _two_calls()
    for seq in range(1, 4):
        scenario.inject_unit("MASTER-A", DeterministicScenario.unit_voice_burst_spec(call_1, seq=seq))
        scenario.inject_unit("MASTER-B", DeterministicScenario.unit_voice_burst_spec(call_2, seq=seq))

    assert _stream_ids(scenario, "MASTER-C") == {bytes.fromhex("0a0a0a0a")}
    assert _stream_ids(scenario, "MASTER-D") == {bytes.fromhex("0b0b0b0b")}
    assert len(scenario.capture.for_system("MASTER-C")) == 4
    assert len(scenario.capture.for_system("MASTER-D")) == 4


@pytest.mark.behavior
def test_one_calls_terminator_does_not_stop_the_other() -> None:
    """Call 2's VTERM must leave call 1 still delivered to its own destination."""
    scenario, call_1, call_2 = _two_calls()
    scenario.inject_unit("MASTER-B", DeterministicScenario.voice_term_spec(call_2, seq=1))
    sent_before = len(scenario.capture.for_system("MASTER-C"))

    for seq in range(1, 4):
        scenario.inject_unit("MASTER-A", DeterministicScenario.unit_voice_burst_spec(call_1, seq=seq))

    assert len(scenario.capture.for_system("MASTER-C")) == sent_before + 3
