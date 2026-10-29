# ADN DMR Peer Server - tests trunking site hotspots unchanged
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

"""Hotspots get the same bytes whether or not a Tier III system shares the server."""

from __future__ import annotations

import pytest
from tests.support.trunk_site_stack import build_trunk_site_stack

pytestmark = pytest.mark.integration

MASTERS = ("MASTER-A", "MASTER-B", "MASTER-C")


def _hotspot_writes(trunk: tuple[str, ...]) -> dict[str, list[tuple[bytes, tuple[str, int]]]]:
    """Two hotspots key two TGs at once towards a third that listens to both: contention picks one."""
    s = build_trunk_site_stack(trunk=trunk, masters=MASTERS)
    ha = s.hotspot("MASTER-A", 2140001, ("10.0.0.1", 62031), options=b"TS2=2141;")
    s.hotspot("MASTER-B", 2140002, ("10.0.0.2", 62031), options=b"TS2=2141,2142;")
    hc = s.hotspot("MASTER-C", 2140003, ("10.0.0.3", 62031), options=b"TS2=2142;")
    if trunk:
        s.site("TRUNK", 214000101, ("10.0.1.1", 40001)).subscribe(2149)
    for master in MASTERS:
        s.transports[master].clear()

    s.play(
        (ha, ha.over(2141, rf_src=2140101, stream_id=0x0A0A0A01, bursts=12)),
        (hc, hc.over(2142, rf_src=2140301, stream_id=0x0C0C0C01, bursts=12)),
    )

    return {master: list(s.transports[master].sent) for master in MASTERS}


def test_hotspot_writes_are_byte_identical_with_a_trunk_system_present() -> None:
    without = _hotspot_writes(())

    assert _hotspot_writes(("TRUNK",)) == without
    assert without["MASTER-B"]
