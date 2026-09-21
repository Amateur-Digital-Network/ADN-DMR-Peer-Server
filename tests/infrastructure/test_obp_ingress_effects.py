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
from tests.harness.obp_ingress import (
    CASES,
    FIXTURE,
    PEER,
    as_json,
    capture_enabled,
    load,
    observe,
    record,
)


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


@pytest.mark.parametrize(
    "case",
    [c for c in CASES if c["kind"] == "bcka" and not c.get("no_peer")],
    ids=lambda c: c["name"],
)
def test_a_keepalive_never_moves_egress_off_the_peer(case: dict) -> None:
    """A keepalive carries no NETWORK_ID, so anyone holding the passphrase can send
    one: a second instance of the peer, or another bridge on a shared-passphrase
    mesh. It must not decide where this bridge transmits. Recorded above as well,
    but asserted here so regenerating the corpus cannot drop it.
    """
    for _size, addr in observe(case)["egress"]:
        assert tuple(addr) == PEER


def test_a_keepalive_bootstraps_a_bridge_with_no_configured_peer() -> None:
    """The other half: with no TARGET_IP there is nothing to protect and nothing to
    steal, and the keepalive is the only way an inbound-only bridge learns where to
    answer. Bootstrapping an unknown peer stays allowed.
    """
    case = next(c for c in CASES if c.get("no_peer"))
    for _size, addr in observe(case)["egress"]:
        assert tuple(addr) == PEER
