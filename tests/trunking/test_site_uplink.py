# ADN DMR Peer Server - tests trunking site uplink
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

"""Calls from a Tier III site: 71-byte frames, many at once on one slot bit, each routed as its own stream."""

from __future__ import annotations

import logging

import pytest
from tests.support.dmrtc_wire import dtcping
from tests.support.trunk_site_stack import (
    DmrtcSite,
    build_trunk_site_stack,
    by_stream,
    embedded_lc,
    header_lc,
    uuid_of,
)

from adn_server.application.routing_use_cases import RoutingUseCases
from adn_server.application.subscription.site_tg_ops import site_tg_held
from adn_server.domain import bytes_3, bytes_4
from adn_server.domain.subscription import AudioChannel, SubscriptionId, SystemId, TgId

pytestmark = pytest.mark.integration

A, A_ADDR = 214000101, ("10.0.1.1", 40001)
B, B_ADDR = 214000102, ("10.0.1.2", 40002)
C, C_ADDR = 214000103, ("10.0.1.3", 40003)
D, D_ADDR = 214000201, ("10.0.2.1", 40001)
HS, HS_ADDR = 2140001, ("10.0.0.1", 62031)
HB, HB_ADDR = 2140002, ("10.0.0.2", 62031)
HC, HC_ADDR = 2140003, ("10.0.0.3", 62031)
TG1, TG2, TG3 = 2141, 2142, 2143
R1, R2, R3 = 2149001, 2149002, 2149003
H_RADIO = 2140101
MS_B = 2149050


def test_non_71_byte_dmrd_is_dropped() -> None:
    """Only exactly 71 bytes is a site frame: a short or long one routes nothing and leaves no trace."""
    s = build_trunk_site_stack(obp=("OBP-1",), tables={TG1: (("OBP-1", 1),)})
    hotspot = s.hotspot("MASTER-A", HS, HS_ADDR, options=f"TS2={TG1};".encode())
    site = s.site("TRUNK", A, A_ADDR)
    other = s.site("TRUNK", B, B_ADDR)
    site.subscribe(TG1)
    other.subscribe(TG1)
    head = site.over(TG1, rf_src=R1, stream_id=0x51000001, uuid=uuid_of(1))[0]

    for length in (55, 56, 63, 70, 72, 100):
        site.send(head[:length] if length < 71 else head + bytes(length - 71))

    assert hotspot.dmrd() == [] and other.dmrd() == [] and s.captured == {}
    assert s.proto("TRUNK")._rows == {}
    assert bytes_3(R1) not in s.config["_SUB_MAP"]


def test_hotspots_and_obp_never_get_71_bytes() -> None:
    """The UUID is the site's: routing gets the 55-byte frame, so hotspots get 55 bytes and OpenBridges 53."""
    s = build_trunk_site_stack(obp=("OBP-1",), tables={TG1: (("OBP-1", 1),)})
    hotspot = s.hotspot("MASTER-A", HS, HS_ADDR, options=f"TS2={TG1};".encode())
    site = s.site("TRUNK", A, A_ADDR)
    site.subscribe(TG1)
    hotspot.call(hotspot.over(TG1, rf_src=H_RADIO, stream_id=0x0A0A0A01))
    s.advance(10)
    s.captured.clear()

    group = site.call(site.over(TG1, rf_src=R1, stream_id=0x51000001, uuid=uuid_of(1)))
    s.advance(10)
    private = site.call(site.over(H_RADIO, rf_src=R2, stream_id=0x51000002, uuid=uuid_of(2), unit=True))
    s.advance(10)
    data = site.call(site.unit_data(H_RADIO, rf_src=R3, stream_id=0x51000003, uuid=uuid_of(3)))

    got = by_stream(hotspot.dmrd())
    assert [len(got[bytes_4(sid)]) for sid in (0x51000001, 0x51000002)] == [len(group), len(private)]
    assert {f[4] for f in got[bytes_4(0x51000003)]} == {f[4] for f in data}
    assert {len(f) for f in hotspot.dmrd()} == {55}
    assert len(s.captured["OBP-1"]) == len(group) and {len(p) for p in s.captured["OBP-1"]} == {53}


def test_dmrd_from_an_unknown_address_or_a_site_not_logged_in_is_dropped() -> None:
    s = build_trunk_site_stack()
    hotspot = s.hotspot("MASTER-A", HS, HS_ADDR, options=f"TS2={TG1};".encode())
    site = s.site("TRUNK", A, A_ADDR)
    site.subscribe(TG1)
    elsewhere = DmrtcSite(s, "TRUNK", A, ("10.9.9.9", 40009))
    waiting = DmrtcSite(s, "TRUNK", B, B_ADDR)
    waiting.send(waiting.gateway.received(waiting.send(waiting.gateway.login())[0]))
    unknown = DmrtcSite(s, "TRUNK", 214000103, ("10.0.1.3", 40003))

    elsewhere.call(site.over(TG1, rf_src=R1, stream_id=0x51000001, uuid=uuid_of(1)))
    waiting.call(waiting.over(TG1, rf_src=R2, stream_id=0x51000002, uuid=uuid_of(2)))
    unknown.call(unknown.over(TG1, rf_src=R3, stream_id=0x51000003, uuid=uuid_of(3)))

    assert s.proto("TRUNK")._peers[waiting.peer_id]["CONNECTION"] == "WAITING_CONFIG"
    assert hotspot.dmrd() == [] and s.proto("TRUNK")._rows == {}


def test_a_null_stream_is_dropped(caplog) -> None:
    s = build_trunk_site_stack()
    hotspot = s.hotspot("MASTER-A", HS, HS_ADDR, options=f"TS2={TG1};".encode())
    site = s.site("TRUNK", A, A_ADDR)
    site.subscribe(TG1)

    with caplog.at_level(logging.WARNING):
        site.call(site.over(TG1, rf_src=R1, stream_id=0, uuid=uuid_of(1)))

    assert hotspot.dmrd() == [] and s.proto("TRUNK")._rows == {}
    assert "CALL DROPPED AS STREAM ID IS NULL" in caplog.text


def test_the_system_acl_drops_a_site_call_before_anything_learns_it() -> None:
    s = build_trunk_site_stack(config_overrides={"TRUNK": {"USE_ACL": True, "SUB_ACL": (False, [(R1, R1)])}})
    hotspot = s.hotspot("MASTER-A", HS, HS_ADDR, options=f"TS2={TG1};".encode())
    site = s.site("TRUNK", A, A_ADDR)
    site.subscribe(TG1)

    site.call(site.over(TG1, rf_src=R1, stream_id=0x51000001, uuid=uuid_of(1)))
    assert hotspot.dmrd() == [] and s.proto("TRUNK")._rows == {}
    assert bytes_3(R1) not in s.config["_SUB_MAP"]

    frames = site.call(site.over(TG1, rf_src=R2, stream_id=0x51000002, uuid=uuid_of(2)))
    assert len(hotspot.dmrd()) == len(frames)


def test_the_end_of_a_site_call_releases_its_talker_alias_state(monkeypatch) -> None:
    ended: list[tuple[str, bytes]] = []
    monkeypatch.setattr(RoutingUseCases, "clear_talker_alias_stream", lambda _self, *key: ended.append(key))
    s = build_trunk_site_stack()
    site = s.site("TRUNK", A, A_ADDR)

    site.call(site.over(TG1, rf_src=R1, stream_id=0x51000001, uuid=uuid_of(1)))

    assert ended == [("TRUNK", bytes_4(0x51000001))]


def test_tg4000_from_a_site_leaves_its_legs_active() -> None:
    """TG 4000 resets a hotspot's dynamic talkgroups; at a site it would idle every subscription."""
    s = build_trunk_site_stack()
    hotspot = s.hotspot("MASTER-A", HS, HS_ADDR, options=f"TS2={TG1};".encode())
    site = s.site("TRUNK", A, A_ADDR)
    site.subscribe(TG1)

    for slot in (1, 2):
        site.call(site.over(4000, rf_src=R1, stream_id=0x51000040 + slot, uuid=uuid_of(slot), slot=slot))
    s.advance(10)

    assert site_tg_held(s.store, TG1, "TRUNK")
    frames = hotspot.call(hotspot.over(TG1, rf_src=H_RADIO, stream_id=0x0A0A0A01))
    assert len(site.dmrd()) == len(frames)


def test_three_interleaved_site_streams_reach_their_hotspot_and_obp_targets_whole() -> None:
    """One slot bit, three calls: packet control, START/END RX and the forward each run per stream."""
    tgs = (TG1, TG2, TG3)
    s = build_trunk_site_stack(
        masters=("MASTER-A", "MASTER-B", "MASTER-C"), obp=("OBP-1",), tables={tg: (("OBP-1", 1),) for tg in tgs},
    )
    hotspots = [
        s.hotspot("MASTER-A", HS, HS_ADDR, options=f"TS2={TG1};".encode()),
        s.hotspot("MASTER-B", HB, HB_ADDR, options=f"TS2={TG2};".encode()),
        s.hotspot("MASTER-C", HC, HC_ADDR, options=f"TS2={TG3};".encode()),
    ]
    site = s.site("TRUNK", A, A_ADDR)
    site.subscribe(*tgs)
    calls = [
        site.over(tg, rf_src=rf, stream_id=0x51000001 + n, uuid=uuid_of(n))
        for n, (tg, rf) in enumerate(zip(tgs, (R1, R2, R3)))
    ]

    s.play(*((site, frames) for frames in calls))

    obp = by_stream(s.captured["OBP-1"])
    for n, (hotspot, frames) in enumerate(zip(hotspots, calls)):
        sid = bytes_4(0x51000001 + n)
        assert [f[16:20] for f in hotspot.dmrd()] == [sid] * len(frames)
        assert [f[4] for f in hotspot.dmrd()] == [f[4] for f in frames]
        assert [f[4] for f in obp[sid]] == [f[4] for f in frames]
    assert sorted(int(e[4]) for e in s.events("GROUP VOICE,START,RX,TRUNK,")) == [0x51000001, 0x51000002, 0x51000003]
    assert sorted(int(e[4]) for e in s.events("GROUP VOICE,END,RX,TRUNK,")) == [0x51000001, 0x51000002, 0x51000003]


def test_two_sites_using_one_stream_id_keep_their_own_rows() -> None:
    """Each site picks its own stream IDs: the same ID from two sites at once is two calls, each forwarded whole."""
    s = build_trunk_site_stack(masters=("MASTER-A", "MASTER-B"))
    to_a = s.hotspot("MASTER-A", HS, HS_ADDR, options=f"TS2={TG1};".encode())
    to_b = s.hotspot("MASTER-B", HB, HB_ADDR, options=f"TS2={TG2};".encode())
    a = s.site("TRUNK", A, A_ADDR)
    b = s.site("TRUNK", B, B_ADDR)
    call_a = a.over(TG1, rf_src=R1, stream_id=0x51000001, uuid=uuid_of(1))
    call_b = b.over(TG2, rf_src=R2, stream_id=0x51000001, uuid=uuid_of(2))

    s.play((a, call_a), (b, call_b))

    for hotspot, frames, tg, rf in ((to_a, call_a, TG1, R1), (to_b, call_b, TG2, R2)):
        assert [f[4] for f in hotspot.dmrd()] == [f[4] for f in frames]
        assert header_lc(hotspot.dmrd()[0])[3:] == bytes_3(tg) + bytes_3(rf)


def test_two_sites_using_one_stream_id_keep_their_own_call_uuids() -> None:
    """dmrtc keys a call on its UUID: other sites, of the system or across a bridge, get each call under its own."""
    s = build_trunk_site_stack(trunk=("TRUNK", "TRUNK-2"))
    a = s.site("TRUNK", A, A_ADDR)
    b = s.site("TRUNK", B, B_ADDR)
    c = s.site("TRUNK", C, C_ADDR)
    d = s.site("TRUNK-2", D, D_ADDR)
    for site in (a, b, c, d):
        site.subscribe(TG1, TG2)
    call_a = a.over(TG1, rf_src=R1, stream_id=0x51000001, uuid=uuid_of(1))
    call_b = b.over(TG2, rf_src=R2, stream_id=0x51000001, uuid=uuid_of(2))

    s.play((a, call_a), (b, call_b))

    for site in (c, d):
        uuids: dict[int, set[bytes]] = {}
        for frame in site.dmrd():
            uuids.setdefault(int.from_bytes(frame[8:11], "big"), set()).add(frame[55:])
        assert uuids == {TG1: {uuid_of(1)}, TG2: {uuid_of(2)}}


def test_a_frame_routing_refuses_with_none_is_neither_recorded_nor_repeated() -> None:
    """The BRIDGERESET gate answers None: a refusal as False is, so the other sites get the call from its next frame."""
    s = build_trunk_site_stack()
    a = s.site("TRUNK", A, A_ADDR)
    b = s.site("TRUNK", B, B_ADDR)
    b.subscribe(TG1)
    s.config["SYSTEMS"]["TRUNK"]["_reset"] = True
    call = a.over(TG1, rf_src=R1, stream_id=0x51000001, uuid=uuid_of(1))

    a.call(call[:1])
    assert b.dmrd() == []
    assert "RX_STREAM_ID" not in s.proto("TRUNK")._rows[(a.peer_id, bytes_4(0x51000001))]
    assert s.proto("TRUNK").STATUS[1].get("RX_STREAM_ID") != bytes_4(0x51000001)

    a.call(call[1:])
    assert [f[4] for f in b.dmrd()] == [f[4] for f in call[1:]]


def test_vhead_service_options_reach_the_bridged_lc() -> None:
    s = build_trunk_site_stack()
    hotspot = s.hotspot("MASTER-A", HS, HS_ADDR, options=f"TS2={TG1};".encode())
    site = s.site("TRUNK", A, A_ADDR)
    site.subscribe(TG1)
    lc = b"\x00\x10\x40" + bytes_3(TG1) + bytes_3(R1)

    site.call(site.over(TG1, rf_src=R1, stream_id=0x51000001, uuid=uuid_of(1), lc=lc))

    got = hotspot.dmrd()
    assert header_lc(got[0]) == lc
    assert embedded_lc(got) == lc


def test_a_hotspot_keying_a_tg_live_at_a_site_is_silently_activated() -> None:
    """The site's call is on its system's slot row for every other system: a hotspot joining the TG is not heard."""
    s = build_trunk_site_stack()
    hotspot = s.hotspot("MASTER-A", HS, HS_ADDR, options=f"TS2={TG3};".encode())
    site = s.site("TRUNK", A, A_ADDR)
    other = s.site("TRUNK", B, B_ADDR)
    site.subscribe(TG1)
    other.subscribe(TG1)
    # A leg armed mid-call takes its slot's last TX LC and fails on a slot that never had one, as for hotspots.
    site.call(site.over(TG3, rf_src=R3, stream_id=0x51000003, uuid=uuid_of(3)))
    s.advance(10)
    site_call = site.over(TG1, rf_src=R1, stream_id=0x51000001, uuid=uuid_of(1), bursts=12)

    s.play((site, site_call[:4]))
    s.play((site, site_call[4:]), (hotspot, hotspot.over(TG1, rf_src=H_RADIO, stream_id=0x0A0A0A01)))

    assert set(by_stream(other.dmrd())) == {bytes_4(0x51000001)}
    assert site.dmrd() == []


@pytest.mark.parametrize("bursts", [2, 3], ids=["last-burst-b", "last-burst-c"])
def test_a_site_call_between_voice_bursts_still_holds_its_talkgroup(bursts: int) -> None:
    """Voice burst C carries the terminator's number: the flat row must still read it as voice."""
    s = build_trunk_site_stack()
    hotspot = s.hotspot("MASTER-A", HS, HS_ADDR)
    site = s.site("TRUNK", A, A_ADDR)
    other = s.site("TRUNK", B, B_ADDR)
    site.subscribe(TG1)
    other.subscribe(TG1)
    site.call(site.over(TG1, rf_src=R1, stream_id=0x51000001, uuid=uuid_of(1), bursts=bursts, terminator=False))

    hotspot.call(hotspot.over(TG1, rf_src=H_RADIO, stream_id=0x0A0A0A01, bursts=3))

    assert set(by_stream(other.dmrd())) == {bytes_4(0x51000001)}


def test_an_uplink_on_an_unsubscribed_tg_arms_a_ua_leg_and_is_forwarded() -> None:
    s = build_trunk_site_stack()
    hotspot = s.hotspot("MASTER-A", HS, HS_ADDR, options=f"TS2={TG3};".encode())
    site = s.site("TRUNK", A, A_ADDR)

    frames = site.call(site.over(TG3, rf_src=R1, stream_id=0x51000001, uuid=uuid_of(1)))

    assert len(hotspot.dmrd()) == len(frames)
    leg = s.store.get(SubscriptionId(AudioChannel(TgId(TG3), 1), SystemId("TRUNK")))
    assert leg is not None and leg.is_active()
    assert not site_tg_held(s.store, TG3, "TRUNK")


def test_a_lost_terminator_gives_end_rx_through_the_trimmer() -> None:
    s = build_trunk_site_stack()
    site = s.site("TRUNK", A, A_ADDR)
    site.subscribe(TG1)
    site.call(site.over(TG1, rf_src=R1, stream_id=0x51000001, uuid=uuid_of(1), terminator=False))
    assert s.events("GROUP VOICE,END,RX,TRUNK,") == []

    s.advance(6)
    s.trim_streams()

    assert [int(e[4]) for e in s.events("GROUP VOICE,END,RX,TRUNK,")] == [0x51000001]


def test_idle_rows_are_trimmed() -> None:
    s = build_trunk_site_stack()
    site = s.site("TRUNK", A, A_ADDR)
    other = s.site("TRUNK", B, B_ADDR)
    site.subscribe(TG1)
    other.register(MS_B)
    site.call(site.over(TG1, rf_src=R1, stream_id=0x51000001, uuid=uuid_of(1)))
    site.call(site.unit_data(MS_B, rf_src=R2, stream_id=0x51000002, uuid=uuid_of(2)))
    proto = s.proto("TRUNK")
    assert proto._rows

    s.advance(61)
    site.send(dtcping(site.peer_id))
    other.send(dtcping(other.peer_id))
    s.maintenance("TRUNK")

    assert proto._rows == {}
    assert proto.site_connected(site.peer_id) and proto.site_connected(other.peer_id)
