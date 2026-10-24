# ADN DMR Peer Server - tests trunking site to site
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

"""Group calls between Tier III sites: of one system by REPEAT, of two systems by the talkgroup's bridges."""

from __future__ import annotations

import pytest
from tests.support.trunk_site_stack import build_trunk_site_stack, by_stream, uuid_of

from adn_server.domain import bytes_4

pytestmark = pytest.mark.integration

A, A_ADDR = 214000101, ("10.0.1.1", 40001)
B, B_ADDR = 214000102, ("10.0.1.2", 40002)
C, C_ADDR = 214000201, ("10.0.2.1", 40011)
D, D_ADDR = 214000202, ("10.0.2.2", 40012)
HS, HS_ADDR = 2140001, ("10.0.0.1", 62031)
TG1, TG2 = 2141, 2142
R1 = 2149001
UUID = uuid_of(0xA1)


def _two_sites(**kwargs):
    s = build_trunk_site_stack(**kwargs)
    hotspot = s.hotspot("MASTER-A", HS, HS_ADDR, options=f"TS2={TG1};".encode())
    a = s.site("TRUNK", A, A_ADDR)
    b = s.site("TRUNK", B, B_ADDR)
    a.subscribe(TG1)
    b.subscribe(TG1)
    return s, hotspot, a, b


def test_site_b_gets_site_a_group_call_with_a_uuid() -> None:
    s, _, a, b = _two_sites()

    sent = a.call(a.over(TG1, rf_src=R1, stream_id=0x51000001, uuid=UUID))

    got = b.dmrd()
    assert len(got) == len(sent)
    for frame, orig in zip(got, sent):
        assert frame[:11] == orig[:11] and frame[11:15] == b.peer_id and frame[15:55] == orig[15:55]
        assert frame[55:] == UUID


def test_a_site_never_gets_its_own_frames() -> None:
    s, _, a, b = _two_sites()

    a.call(a.over(TG1, rf_src=R1, stream_id=0x51000001, uuid=UUID))

    assert b.dmrd() and a.dmrd() == []


def test_with_repeat_false_no_other_site_gets_them() -> None:
    """REPEAT is site-to-site on one system; the rest of the network still hears the site."""
    s, hotspot, a, b = _two_sites(config_overrides={"TRUNK": {"REPEAT": False}})

    sent = a.call(a.over(TG1, rf_src=R1, stream_id=0x51000001, uuid=UUID))

    assert b.dmrd() == []
    assert len(hotspot.dmrd()) == len(sent)


def test_suppressed_streams_are_not_delivered() -> None:
    """A site joining a TG already live elsewhere is silently activated: its uplink reaches no other site."""
    s, hotspot, a, b = _two_sites()
    live = hotspot.over(TG1, rf_src=2140101, stream_id=0x0A0A0A01, bursts=12)

    s.play((hotspot, live[:4]))
    s.play((hotspot, live[4:]), (a, a.over(TG1, rf_src=R1, stream_id=0x51000001, uuid=UUID)))

    assert set(by_stream(b.dmrd())) == {bytes_4(0x0A0A0A01)}
    assert set(by_stream(a.dmrd())) == {bytes_4(0x0A0A0A01)}


def test_cross_trunk_system_delivery_follows_bridges() -> None:
    """Between two trunk systems a call is ordinary routing: it reaches the other system's subscribers only."""
    s = build_trunk_site_stack(trunk=("TRUNK", "TRUNK-2"))
    a = s.site("TRUNK", A, A_ADDR)
    c = s.site("TRUNK-2", C, C_ADDR)
    d = s.site("TRUNK-2", D, D_ADDR)
    a.subscribe(TG1)
    c.subscribe(TG1)
    d.subscribe(TG2)

    sent = a.call(a.over(TG1, rf_src=R1, stream_id=0x51000001, uuid=UUID))

    got = c.dmrd()
    assert [f[4] for f in got] == [f[4] for f in sent]
    assert {f[55:] for f in got} == {UUID} and {f[11:15] for f in got} == {c.peer_id}
    assert d.dmrd() == []
