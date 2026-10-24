# ADN DMR Peer Server - tests trunking site private
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

"""Private calls and unit data to and from radios registered at Tier III sites: to their site only, once."""

from __future__ import annotations

import pytest
from tests.support.dmrtc_wire import dtccl, dtcl
from tests.support.trunk_site_stack import build_trunk_site_stack, by_stream, unit_data, uuid_of

from adn_server.domain import bytes_4

pytestmark = pytest.mark.integration

A, A_ADDR = 214000101, ("10.0.1.1", 40001)
B, B_ADDR = 214000102, ("10.0.1.2", 40002)
C, C_ADDR = 214000201, ("10.0.2.1", 40011)
HS, HS_ADDR = 2140001, ("10.0.0.1", 62031)
H_RADIO = 2140101
MS, MS_B, MS_C = 2149050, 2149060, 2149070
R1, R2 = 2149001, 2149002


def _private(hotspot, dst: int, stream_id: int = 0x0A0A0A01) -> list[bytes]:
    return hotspot.call(hotspot.over(dst, rf_src=H_RADIO, stream_id=stream_id, unit=True))


def test_a_hotspot_calls_a_registered_radio_at_its_site_only() -> None:
    s = build_trunk_site_stack()
    hotspot = s.hotspot("MASTER-A", HS, HS_ADDR)
    a = s.site("TRUNK", A, A_ADDR)
    b = s.site("TRUNK", B, B_ADDR)
    a.register(MS)
    b.register(MS_B)

    sent = _private(hotspot, MS)

    assert [f[4] for f in a.dmrd()] == [f[4] for f in sent]
    assert {len(f) for f in a.dmrd()} == {71}
    assert b.dmrd() == []


def test_an_unknown_radio_reaches_no_site() -> None:
    """A private frame is never broadcast to a system's sites: not for a radio never seen, nor one whose site left."""
    s = build_trunk_site_stack()
    hotspot = s.hotspot("MASTER-A", HS, HS_ADDR)
    a = s.site("TRUNK", A, A_ADDR)
    b = s.site("TRUNK", B, B_ADDR)
    a.register(MS)
    a.send(dtccl(a.peer_id))

    _private(hotspot, 2149099, 0x0A0A0A01)
    s.advance(10)
    _private(hotspot, MS, 0x0A0A0A02)
    s.advance(10)
    hotspot.call(unit_data(hotspot.peer_id, MS, rf_src=H_RADIO, stream_id=0x0A0A0A03, slot=2))

    assert b.dmrd() == [] and a.dmrd() == []


@pytest.mark.parametrize("trunk_slot", ["idle", "busy"])
def test_unit_data_to_a_registered_radio_arrives_once(trunk_slot: str) -> None:
    """Unit data to a 7-digit ID goes by SUB_MAP and by the private-call path; the site gets one copy."""
    s = build_trunk_site_stack()
    hotspot = s.hotspot("MASTER-A", HS, HS_ADDR)
    a = s.site("TRUNK", A, A_ADDR)
    b = s.site("TRUNK", B, B_ADDR)
    a.register(MS)
    b.subscribe(2141)
    if trunk_slot == "busy":
        s.play((b, b.over(2141, rf_src=R1, stream_id=0x52000001, uuid=uuid_of(1), terminator=False)))

    sent = hotspot.call(unit_data(hotspot.peer_id, MS, rf_src=H_RADIO, stream_id=0x0A0A0A01, slot=2))

    assert [f[4] for f in a.dmrd()] == [f[4] for f in sent]


def test_unit_data_between_sites_of_one_system_arrives_once_only_at_b() -> None:
    """Routing leaves same-MASTER unit data to its REPEAT (#153): the site of the pin gets it, once."""
    s = build_trunk_site_stack()
    a = s.site("TRUNK", A, A_ADDR)
    b = s.site("TRUNK", B, B_ADDR)
    b.register(MS_B)

    sent = a.call(a.unit_data(MS_B, rf_src=R1, stream_id=0x51000001, uuid=uuid_of(1)))

    got = b.dmrd()
    assert [f[4] for f in got] == [f[4] for f in sent]
    assert {f[55:] for f in got} == {uuid_of(1)}
    assert a.dmrd() == []


def test_a_site_radio_calls_a_hotspot_radio_through_sub_map() -> None:
    s = build_trunk_site_stack()
    hotspot = s.hotspot("MASTER-A", HS, HS_ADDR)
    a = s.site("TRUNK", A, A_ADDR)
    hotspot.call(hotspot.over(91, rf_src=H_RADIO, stream_id=0x0A0A0A01))
    s.advance(10)

    sent = a.call(a.over(H_RADIO, rf_src=R1, stream_id=0x51000001, uuid=uuid_of(1), unit=True))

    got = by_stream(hotspot.dmrd())[bytes_4(0x51000001)]
    assert [f[4] for f in got] == [f[4] for f in sent]
    assert {len(f) for f in got} == {55}


def test_a_site_radio_calls_a_radio_at_a_site_of_another_trunk_system() -> None:
    s = build_trunk_site_stack(trunk=("TRUNK", "TRUNK-2"))
    a = s.site("TRUNK", A, A_ADDR)
    c = s.site("TRUNK-2", C, C_ADDR)
    c.register(MS_C)

    sent = a.call(a.over(MS_C, rf_src=R1, stream_id=0x51000001, uuid=uuid_of(1), unit=True))

    got = c.dmrd()
    assert [f[4] for f in got] == [f[4] for f in sent]
    assert {f[55:] for f in got} == {uuid_of(1)} and {f[11:15] for f in got} == {c.peer_id}


def test_unit_data_to_a_radio_at_a_site_of_another_trunk_system_arrives_once() -> None:
    """SUB_MAP and the private-call path both lead to the other system's site; it gets each frame once."""
    s = build_trunk_site_stack(trunk=("TRUNK", "TRUNK-2"))
    a = s.site("TRUNK", A, A_ADDR)
    c = s.site("TRUNK-2", C, C_ADDR)
    c.register(MS_C)

    sent = a.call(a.unit_data(MS_C, rf_src=R1, stream_id=0x51000001, uuid=uuid_of(1)))

    assert [f[4] for f in c.dmrd()] == [f[4] for f in sent]


def test_registration_survives_a_site_relogin() -> None:
    """A login drops what SUB_MAP held for the site; its registered radios are pinned there again."""
    s = build_trunk_site_stack()
    hotspot = s.hotspot("MASTER-A", HS, HS_ADDR)
    a = s.site("TRUNK", A, A_ADDR)
    a.register(MS)

    a.login()
    sent = _private(hotspot, MS)

    assert [f[4] for f in a.dmrd()] == [f[4] for f in sent]


def test_a_site_logging_in_again_gets_no_frames() -> None:
    """SUB_MAP still points at the site while it logs in again; nothing goes to it before the login completes."""
    s = build_trunk_site_stack()
    hotspot = s.hotspot("MASTER-A", HS, HS_ADDR)
    a = s.site("TRUNK", A, A_ADDR)
    a.register(MS)
    a.send(dtcl(a.peer_id))

    _private(hotspot, MS)

    assert a.dmrd() == []


def test_a_deregistered_radio_is_not_called() -> None:
    s = build_trunk_site_stack()
    hotspot = s.hotspot("MASTER-A", HS, HS_ADDR)
    a = s.site("TRUNK", A, A_ADDR)
    a.register(MS)
    assert a.deregister(MS) == [MS]

    _private(hotspot, MS)

    assert a.dmrd() == []


def test_registrations_are_pinned_again_after_a_restart(tmp_path) -> None:
    """dmrtc does not register its radios again: a restarted server reads them back and pins them at login."""
    path = str(tmp_path / "trunk_registrations.json")
    before = build_trunk_site_stack(path=path)
    before.site("TRUNK", A, A_ADDR).register(MS)
    before.sites.save()

    s = build_trunk_site_stack(path=path)
    hotspot = s.hotspot("MASTER-A", HS, HS_ADDR)
    a = s.site("TRUNK", A, A_ADDR)
    sent = _private(hotspot, MS)

    assert [f[4] for f in a.dmrd()] == [f[4] for f in sent]


def test_two_private_calls_from_one_site_reach_their_own_sites() -> None:
    """Each call keeps its own targets in its own row: one call's start does not retarget the other."""
    s = build_trunk_site_stack(trunk=("TRUNK", "TRUNK-2"))
    hotspot = s.hotspot("MASTER-A", HS, HS_ADDR)
    a = s.site("TRUNK", A, A_ADDR)
    c = s.site("TRUNK-2", C, C_ADDR)
    c.register(MS_C)
    hotspot.call(hotspot.over(91, rf_src=H_RADIO, stream_id=0x0A0A0A01))
    s.advance(10)
    to_c = a.over(MS_C, rf_src=R1, stream_id=0x51000001, uuid=uuid_of(1), unit=True)
    to_hotspot = a.over(H_RADIO, rf_src=R2, stream_id=0x51000002, uuid=uuid_of(2), unit=True, slot=1)

    s.play((a, to_c), (a, to_hotspot))

    assert {sid: len(f) for sid, f in by_stream(c.dmrd()).items()} == {bytes_4(0x51000001): len(to_c)}
    got = by_stream(hotspot.dmrd())
    assert bytes_4(0x51000001) not in got and len(got[bytes_4(0x51000002)]) == len(to_hotspot)
