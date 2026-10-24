# ADN DMR Peer Server - tests trunking site echo
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

"""The parrot (9990) at Tier III sites: each playback goes back to the site its caller keyed it from."""

from __future__ import annotations

import pytest
from tests.support.trunk_site_stack import build_trunk_site_stack, by_stream, uuid_of

from adn_server.domain import bytes_4

pytestmark = pytest.mark.integration

A, A_ADDR = 214000101, ("10.0.1.1", 40001)
B, B_ADDR = 214000102, ("10.0.1.2", 40002)
R1, R2, R3 = 2149001, 2149002, 2149003


def test_each_site_hears_only_its_own_playback() -> None:
    """Two callers at two sites key the parrot at once; one playback must not go to both sites."""
    s = build_trunk_site_stack(echo=True)
    a = s.site("TRUNK", A, A_ADDR)
    b = s.site("TRUNK", B, B_ADDR)
    s.play(
        (a, a.over(9990, rf_src=R1, stream_id=0x51000001, uuid=uuid_of(1))),
        (b, b.over(9990, rf_src=R2, stream_id=0x52000001, uuid=uuid_of(2))),
    )
    assert s.captured["ECHO"]
    s.advance(2)

    s.echo_playback(R1, 0x0E000001)
    s.echo_playback(R2, 0x0E000002)
    s.echo_playback(R3, 0x0E000003)

    assert {sid: len(frames) for sid, frames in by_stream(a.dmrd()).items()} == {bytes_4(0x0E000001): 5}
    assert {sid: len(frames) for sid, frames in by_stream(b.dmrd()).items()} == {bytes_4(0x0E000002): 5}
