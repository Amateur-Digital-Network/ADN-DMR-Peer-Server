# ADN DMR Peer Server - tests trunking site downlink
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

"""Calls to Tier III sites: every subscribed call at once, 71 bytes with one UUID per call and its own LC."""

from __future__ import annotations

import pytest
from tests.harness.deterministic import patch_routing_wall_time
from tests.harness.scenarios import talker_alias_config
from tests.support.trunk_site_stack import build_trunk_site_stack, by_stream, embedded_lc, header_lc, over, uuid_of

from adn_server.application.plugins.application.ingress import PluginIngress
from adn_server.application.server_voice import DEFAULT_SERVER_VOICE_ID
from adn_server.domain import bytes_3, bytes_4

pytestmark = pytest.mark.integration

A, A_ADDR = 214000101, ("10.0.1.1", 40001)
B, B_ADDR = 214000102, ("10.0.1.2", 40002)
C, C_ADDR = 214000201, ("10.0.2.1", 40011)
HS, HS_ADDR = 2140001, ("10.0.0.1", 62031)
HB, HB_ADDR = 2140002, ("10.0.0.2", 62031)
TG1, TG2 = 2141, 2142
H1, H2 = 2140101, 2140201
R1 = 2149001


def _assert_call(frames: list[bytes], sent: list[bytes], site_id: int, tg: int, rf_src: int) -> bytes:
    """The whole over, 71 bytes each, to this site, with one UUID and the call's own LC; returns the UUID."""
    assert [f[4] for f in frames] == [f[4] for f in sent]
    assert {len(f) for f in frames} == {71}
    assert {f[11:15] for f in frames} == {bytes_4(site_id)}
    uuids = {f[55:] for f in frames}
    assert len(uuids) == 1
    lc = bytes_3(tg) + bytes_3(rf_src)
    assert header_lc(frames[0])[3:] == lc and embedded_lc(frames)[3:] == lc
    return uuids.pop()


def test_hotspots_on_two_masters_reach_one_site_at_once() -> None:
    """A site takes every subscribed call concurrently, whatever slot they come on; dmrtc keys each on its UUID."""
    s = build_trunk_site_stack(masters=("MASTER-A", "MASTER-B"), config_overrides={"TRUNK": {"MAX_PEERS": 1}})
    ha = s.hotspot("MASTER-A", HS, HS_ADDR, options=f"TS2={TG1};".encode())
    hb = s.hotspot("MASTER-B", HB, HB_ADDR, options=f"TS2={TG2};".encode())
    site = s.site("TRUNK", A, A_ADDR)
    site.subscribe(TG1, TG2)
    call_a = ha.over(TG1, rf_src=H1, stream_id=0x0A0A0A01)
    call_b = hb.over(TG2, rf_src=H2, stream_id=0x0B0B0B01)

    s.play((ha, call_a), (hb, call_b))

    got = by_stream(site.dmrd())
    uuid_a = _assert_call(got[bytes_4(0x0A0A0A01)], call_a, A, TG1, H1)
    uuid_b = _assert_call(got[bytes_4(0x0B0B0B01)], call_b, A, TG2, H2)
    assert uuid_a != uuid_b


@pytest.mark.parametrize(
    "source", ["hotspot-to-one-site", "hotspot-to-two-sites", "same-system-site", "other-trunk-site"],
)
def test_two_slot_legs_send_each_frame_once(source: str) -> None:
    """A subscription holds a leg on each slot; a site still gets each frame once."""
    s = build_trunk_site_stack(trunk=("TRUNK", "TRUNK-2"))
    hotspot = s.hotspot("MASTER-A", HS, HS_ADDR, options=f"TS2={TG1};".encode())
    sites = [s.site("TRUNK", A, A_ADDR), s.site("TRUNK", B, B_ADDR)]
    for site in sites:
        site.subscribe(TG1)
    if source == "hotspot-to-one-site":
        sites[1].unsubscribe(TG1)
        sent = hotspot.call(hotspot.over(TG1, rf_src=H1, stream_id=0x0A0A0A01))
        receivers = sites[:1]
    elif source == "hotspot-to-two-sites":
        sent = hotspot.call(hotspot.over(TG1, rf_src=H1, stream_id=0x0A0A0A01))
        receivers = sites
    elif source == "same-system-site":
        sent = sites[0].call(sites[0].over(TG1, rf_src=R1, stream_id=0x51000001, uuid=uuid_of(1)))
        receivers = sites[1:]
    else:
        other = s.site("TRUNK-2", C, C_ADDR)
        other.subscribe(TG1)
        sent = other.call(other.over(TG1, rf_src=R1, stream_id=0x51000001, uuid=uuid_of(1)))
        receivers = sites

    for site in receivers:
        assert [f[4] for f in site.dmrd()] == [f[4] for f in sent]
    assert len(hotspot.dmrd()) == (len(sent) if source in ("same-system-site", "other-trunk-site") else 0)


def test_an_unsubscribed_site_gets_nothing() -> None:
    s = build_trunk_site_stack()
    hotspot = s.hotspot("MASTER-A", HS, HS_ADDR, options=f"TS2={TG1};".encode())
    site = s.site("TRUNK", A, A_ADDR)
    other = s.site("TRUNK", B, B_ADDR)
    site.subscribe(TG1)
    other.subscribe(TG2)

    sent = hotspot.call(hotspot.over(TG1, rf_src=H1, stream_id=0x0A0A0A01))

    assert len(site.dmrd()) == len(sent)
    assert other.dmrd() == []


def test_an_announcement_reaches_the_sites_concurrently_with_a_call() -> None:
    """A server announcement holding the slot does not hold a site: both calls arrive whole."""
    s = build_trunk_site_stack(
        masters=("MASTER-A", "MASTER-B"), config_overrides={"PROXY": {"TARGET_SYSTEM": "MASTER-A"}},
    )
    s.hotspot("MASTER-A", HS, HS_ADDR, options=f"TS2={TG1};".encode())
    hb = s.hotspot("MASTER-B", HB, HB_ADDR, options=f"TS2={TG2};".encode())
    site = s.site("TRUNK", A, A_ADDR)
    site.subscribe(TG1, TG2)
    plugins = PluginIngress(
        s.routing, s.config, lambda: s.protocols, lambda system, pkt: s.protocols[system].send_system(pkt),
        clock=s.clock.time,
    )
    announcement = over(bytes_4(9990), TG1, rf_src=DEFAULT_SERVER_VOICE_ID, stream_id=0x0C0C0C01, slot=2)
    call = hb.over(TG2, rf_src=H2, stream_id=0x0B0B0B01)

    class _Announcer:
        def send(self, frame: bytes) -> None:
            with patch_routing_wall_time(s.clock):
                assert plugins.deliver(frame, "announce")

    s.play((_Announcer(), announcement[:3]))
    s.play((_Announcer(), announcement[3:]), (hb, call))

    got = by_stream(site.dmrd())
    _assert_call(got[bytes_4(0x0C0C0C01)], announcement, A, TG1, DEFAULT_SERVER_VOICE_ID)
    _assert_call(got[bytes_4(0x0B0B0B01)], call, A, TG2, H2)


def test_no_dmra_to_sites_with_talker_alias_on() -> None:
    """DMRGateway logs a master's DMRA as unknown; the alias reaches sites embedded, with the call's UUID kept."""
    ta = talker_alias_config()
    s = build_trunk_site_stack(
        config_overrides={
            "GLOBAL": {key: ta["GLOBAL"][key] for key in ta["GLOBAL"] if key.startswith("TALKER_ALIAS")},
            "_SUB_PROFILES": ta["_SUB_PROFILES"],
            "_SUB_IDS": ta["_SUB_IDS"],
        },
    )
    assert s.config["GLOBAL"]["TALKER_ALIAS"] is True and s.config["GLOBAL"]["TALKER_ALIAS_SEND_DMRA"] is True
    hotspot = s.hotspot("MASTER-A", HS, HS_ADDR, options=f"TS2={TG1};".encode())
    site = s.site("TRUNK", A, A_ADDR)
    site.subscribe(TG1)
    caller = next(iter(ta["_SUB_IDS"]))

    sent = hotspot.call(hotspot.over(TG1, rf_src=caller, stream_id=0x0A0A0A01, bursts=18))

    assert [pkt for pkt in site.written() if pkt[:4] == b"DMRA"] == []
    frames = site.dmrd()
    assert [f[4] for f in frames] == [f[4] for f in sent]
    assert {len(f) for f in frames} == {71} and len({f[55:] for f in frames}) == 1
