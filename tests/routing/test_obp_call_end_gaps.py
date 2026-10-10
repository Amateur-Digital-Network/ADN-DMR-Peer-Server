# ADN DMR Peer Server - tests routing OBP call end loss and gaps
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

"""OBP *CALL END*: Loss counts every packet a sequence jump skipped, and Max gap shows the
longest silence (frames held back upstream and released in a burst keep their sequence)."""

from __future__ import annotations

import logging
import re

import pytest
from tests.harness.deterministic import (
    DeterministicScenario,
    PacketSpec,
    active_routing_table,
    add_openbridge_system,
    minimal_config,
    patch_routing_wall_time,
)

_TG = 3340
_STREAM = 0x0C0C0C0C


def _call_end(caplog: pytest.LogCaptureFixture, schedule: list[tuple[float, int]]) -> str:
    """Play an OBP over: voice header, then (delay before the frame, seq) for each burst, then VTERM."""
    config = minimal_config(("MASTER-A",))
    add_openbridge_system(config, "OBP-1")
    sc = DeterministicScenario(config=config)
    sc.seed_routing_table(active_routing_table(_TG, (("MASTER-A", 2), ("OBP-1", 1))))
    spec = PacketSpec(dst_id=_TG, stream_id=_STREAM, rf_src=3340062, slot=1)
    with caplog.at_level(logging.INFO), patch_routing_wall_time(sc.clock):
        sc.inject_obp("OBP-1", DeterministicScenario.voice_head_spec(spec))
        seq = 0
        for delay, seq in schedule:
            sc.clock.advance(delay)
            sc.inject_obp("OBP-1", DeterministicScenario.voice_burst_spec(spec, seq=seq, dtype_vseq=(seq - 1) % 6 + 1))
        sc.clock.advance(0.06)
        sc.inject_obp("OBP-1", DeterministicScenario.voice_term_spec(spec, seq=seq + 1))
    lines = [r.getMessage() for r in caplog.records if "*CALL END*" in r.getMessage()]
    assert len(lines) == 1
    return lines[0]


def _field(line: str, name: str) -> float:
    m = re.search(name + r": ([\d.]+)", line)
    assert m, line
    return float(m[1])




def test_every_skipped_packet_counts_as_loss(caplog: pytest.LogCaptureFixture) -> None:
    # seq 1..10, then 21..30: 10 packets missing, not 1
    schedule = [(0.06, s) for s in range(1, 11)] + [(0.66, s) for s in (21,)] + [(0.06, s) for s in range(22, 31)]
    line = _call_end(caplog, schedule)
    received = 20 + 1  # "packets" counts the frames after the header: bursts + terminator
    assert _field(line, "Loss") == pytest.approx(100 * 10 / received, abs=0.01)


def test_max_gap_shows_a_silence_without_missing_sequence(caplog: pytest.LogCaptureFixture) -> None:
    # nothing missing, but 2 s with no frames in the middle
    schedule = [(0.06, s) for s in range(1, 11)] + [(2.0, 11)] + [(0.06, s) for s in range(12, 21)]
    line = _call_end(caplog, schedule)
    assert _field(line, "Loss") == 0.0
    assert _field(line, "Max gap") == pytest.approx(2.0, abs=0.01)


def test_a_clean_over_reports_the_frame_spacing(caplog: pytest.LogCaptureFixture) -> None:
    line = _call_end(caplog, [(0.06, s) for s in range(1, 21)])
    assert _field(line, "Loss") == 0.0
    assert _field(line, "Max gap") == pytest.approx(0.06, abs=0.01)
