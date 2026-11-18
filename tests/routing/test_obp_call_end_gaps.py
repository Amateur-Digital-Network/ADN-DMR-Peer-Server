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

"""OBP *CALL END*: Loss is the share of the expected packets that a sequence jump skipped
(never over 100 %), and Max gap shows the longest silence (frames held back upstream and released in a burst keep their sequence)."""

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
    parse_dmr_fields,
    patch_routing_wall_time,
)

from adn_server.application.routing.helpers import seq_advance

_TG = 3340
_STREAM = 0x0C0C0C0C


def _call_end(caplog: pytest.LogCaptureFixture, schedule: list[tuple[float, int]], sent: list | None = None) -> str:
    """Play an OBP over: voice header, then (delay before the frame, seq) for each burst, then VTERM.
    ``sent`` collects the sequence numbers forwarded to MASTER-A."""
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
    if sent is not None:
        sent.extend(parse_dmr_fields(p.packet)["seq"] for p in sc.capture.for_system("MASTER-A"))
    lines = [r.getMessage() for r in caplog.records if "*CALL END*" in r.getMessage()]
    assert len(lines) == 1, "no CALL END: the terminator was dropped"
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
    assert _field(line, "Loss") == pytest.approx(100 * 10 / (received + 10), abs=0.01)


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


# Sequence wraps 255 -> 0. A packet that arrives late right after the wrap (254 after 0..3)
# was taken as the new reference, so every later frame looked out of order and was dropped:
# the rest of the call never left over that link and no CALL END was logged.
_WRAP_LATE = [s for s in range(240, 256) if s != 254] + [0, 1, 2, 3, 254] + list(range(4, 20))


def test_seq_advance_wraps_and_keeps_late_packets_behind() -> None:
    assert seq_advance(False, 7) == 1
    assert seq_advance(255, 0) == 1
    assert seq_advance(253, 255) == 2
    assert seq_advance(250, 3) == 9
    assert seq_advance(3, 254) == -1  # late, after the wrap
    assert seq_advance(10, 9) == -1
    assert seq_advance(9, 9) == 0
    assert seq_advance(0, 0) == 1  # repeated voice header
    assert seq_advance(29, 0, terminator=True) == 1  # a terminator closes the call whatever its sequence
    assert seq_advance(29, 0) == -1
    assert seq_advance(50, 1) == 1  # source restarting its count (1 behind the last)
    # after a silence the stream is resuming, whatever the jump
    assert seq_advance(10, 160, silence=9.0) == 150
    assert seq_advance(10, 10, silence=20.0) == 1  # a whole lap
    assert seq_advance(10, 160, silence=0.06) == -1  # no silence: still a late packet


def test_a_late_packet_after_the_wrap_does_not_drop_the_rest_of_the_call(caplog: pytest.LogCaptureFixture) -> None:
    sent: list[int] = []
    line = _call_end(caplog, [(0.06, s) for s in _WRAP_LATE], sent)
    assert sent[-17:] == list(range(4, 21)), "the frames after the late packet and the terminator are forwarded"
    assert 254 not in sent
    # 254 skipped once (253 -> 255): 1 lost; its late copy is dropped, not lost a second time
    received = len(_WRAP_LATE) + 1  # the bursts + the terminator
    assert _field(line, "Loss") == pytest.approx(100 * 1 / received, abs=0.01)


# Seq 1..10, a 9 s pause, then 160..199 on the same stream: a jump of 150, which without the
# silence would look like a late packet and drop the rest of the call.
_PAUSE = [(0.06, s) for s in range(1, 11)] + [(9.0, 160)] + [(0.06, s) for s in range(161, 200)]


def test_a_stream_resuming_after_a_long_pause_is_forwarded(caplog: pytest.LogCaptureFixture) -> None:
    sent: list[int] = []
    line = _call_end(caplog, _PAUSE, sent)
    assert sent[-41:] == list(range(160, 201)), "all 40 frames after the pause and the terminator"
    assert _field(line, "Max gap") == pytest.approx(9.0, abs=0.01)
    # 149 skipped by the jump, 51 received: 74.5 %, it used to be 149 / 51 = 292 %
    assert _field(line, "Loss") == pytest.approx(100 * 149 / (51 + 149), abs=0.01)


def test_repeated_frames_are_not_loss(caplog: pytest.LogCaptureFixture) -> None:
    schedule = [(0.06, s) for s in range(1, 11)] + [(0.01, 10), (0.01, 10)] + [(0.06, s) for s in range(11, 21)]
    line = _call_end(caplog, schedule)
    assert _field(line, "Loss") == 0.0


def test_a_terminator_with_its_sequence_reset_ends_the_call(caplog: pytest.LogCaptureFixture) -> None:
    # Some client apps send the terminator with seq 0: dropped as late, no CALL END until the stream timeout.
    config = minimal_config(("MASTER-A",))
    add_openbridge_system(config, "OBP-1")
    sc = DeterministicScenario(config=config)
    sc.seed_routing_table(active_routing_table(_TG, (("MASTER-A", 2), ("OBP-1", 1))))
    spec = PacketSpec(dst_id=_TG, stream_id=0x0D0D0D0D, rf_src=7300391, slot=1)
    with caplog.at_level(logging.INFO), patch_routing_wall_time(sc.clock):
        sc.inject_obp("OBP-1", DeterministicScenario.voice_head_spec(spec))
        for seq in range(1, 30):
            sc.clock.advance(0.06)
            sc.inject_obp("OBP-1", DeterministicScenario.voice_burst_spec(spec, seq=seq, dtype_vseq=(seq - 1) % 6 + 1))
        sc.clock.advance(0.06)
        sc.inject_obp("OBP-1", DeterministicScenario.voice_term_spec(spec, seq=0))
    lines = [r.getMessage() for r in caplog.records if "*CALL END*" in r.getMessage()]
    assert len(lines) == 1, "no CALL END: the terminator was dropped"
    assert _field(lines[0], "Loss") == 0.0
