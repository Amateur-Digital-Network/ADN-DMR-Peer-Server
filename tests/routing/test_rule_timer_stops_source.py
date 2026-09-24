# ADN DMR Peer Server - tests routing rule timer stops a timed-out source
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

"""A source leg the rule timer deactivates stops being forwarded (legacy: ACTIVE per frame)."""

from __future__ import annotations

import time

import pytest

from tests.harness.deterministic import (
    DeterministicScenario,
    PacketSpec,
    active_routing_table,
    minimal_config,
)


@pytest.mark.behavior
def test_frames_after_the_source_times_out_are_not_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    config = minimal_config(("MASTER-A", "MASTER-B"))
    config["SYSTEMS"]["MASTER-B"]["SINGLE_MODE"] = False  # its leg has no timer
    sc = DeterministicScenario(
        config=config,
        routing_table=active_routing_table(213, (("MASTER-A", 2), ("MASTER-B", 2)), timeout_minutes=1),
    )
    monkeypatch.setattr(time, "time", sc.clock.time)
    sc.routing.apply_startup_subscriptions()

    base = PacketSpec(dst_id=213, stream_id=0x1234, slot=2)
    sc.inject_hbp("MASTER-A", DeterministicScenario.voice_head_spec(base))
    for seq in range(1, 4):
        sc.clock.advance(0.06)
        sc.inject_hbp("MASTER-A", DeterministicScenario.voice_burst_spec(base, seq=seq, dtype_vseq=seq))
    assert len(sc.capture.for_system("MASTER-B")) == 4

    sc.clock.advance(120)
    sc.routing.rule_timer_loop()
    assert sc.subscription_store.relay_tables_with_active_source("MASTER-A", 2, 213) == ()

    sc.capture.packets.clear()
    for seq in range(4, 8):
        sc.clock.advance(0.06)
        sc.inject_hbp("MASTER-A", DeterministicScenario.voice_burst_spec(base, seq=seq, dtype_vseq=seq % 6))
    assert sc.capture.for_system("MASTER-B") == []
