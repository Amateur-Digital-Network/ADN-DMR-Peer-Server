# ADN DMR Peer Server - tests infrastructure obp ingress effects
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

"""The OBP ingress contract, frame by frame, against a recorded corpus.

Every case is a real datagram with a valid MAC. What it produces — delivery to
routing, source quench, the address egress leaves from afterwards and the log
lines — was recorded from the handler as it behaved before the refactor and
checked against upstream ``develop``. A diff here is a behaviour change, so
read it before regenerating with ``CAPTURE=1``.
"""

from __future__ import annotations

import pytest
from tests.harness.obp_ingress import CASES, FIXTURE, as_json, capture_enabled, load, observe, record


@pytest.fixture(scope="module")
def recorded() -> dict[str, dict]:
    if capture_enabled():
        record()
    if not FIXTURE.exists():
        pytest.skip(f"no corpus at {FIXTURE}; regenerate with CAPTURE=1")
    return {row["case"]["name"]: row for row in load()}


def test_corpus_covers_every_case(recorded: dict[str, dict]) -> None:
    assert set(recorded) == {case["name"] for case in CASES}


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_ingress_effects_match_the_recording(case: dict, recorded: dict[str, dict]) -> None:
    expected = recorded[case["name"]]["effects"]
    assert as_json(observe(case)) == expected
