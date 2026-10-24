# ADN DMR Peer Server - tests trunking signalling
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

"""DMRT 0x01-0x04 from a site: ACLs, the per-site caps, SUB_MAP pins, roaming and the legs behind subscriptions."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from adn_server.application.subscription.site_tg_ops import site_tg_held
from adn_server.application.trunking.legs import SiteLegs
from adn_server.application.trunking.ports import SiteLink
from adn_server.application.trunking.registry import MAX_REGISTRATIONS_PER_SITE, MAX_TGS_PER_SITE, Site, SiteRegistry
from adn_server.application.trunking.signalling import DmrtSignalling
from adn_server.domain import ID_MAX, bytes_3, bytes_4
from adn_server.infrastructure.acl_router import InMemoryAclRouter
from adn_server.infrastructure.config_loader import acl_build
from adn_server.infrastructure.subscription_store import InMemorySubscriptionStore
from tests.support.dmrtc_wire import (
    dmrt_registration,
    dmrt_subscription,
    read_deregistration,
    read_registration_confirmation,
    read_subscription_confirmation,
)

A = Site("TRUNK", bytes_4(214000101))
B = Site("TRUNK", bytes_4(214000102))
C = Site("TRUNK-2", bytes_4(214000201))
GONE = Site("TRUNK", bytes_4(214000109))
HS = bytes_4(2140001)
MS, MS2 = 2140501, 2140502
NOW = 1_761_300_000.0
SIGNALLING_LOGGER = "adn_server.application.trunking.signalling"


def _acls(*, use_acl: bool = True, sub: str = "PERMIT:ALL", tg1: str = "PERMIT:ALL", tg2: str = "PERMIT:ALL") -> dict:
    return {
        "USE_ACL": use_acl,
        "SUB_ACL": acl_build(sub, ID_MAX),
        "TG1_ACL": acl_build(tg1, ID_MAX),
        "TG2_ACL": acl_build(tg2, ID_MAX),
    }


class _Link(SiteLink):
    """Every DMRT the server sent, and to which site; only connected sites take it."""

    def __init__(self, connected: tuple[Site, ...]) -> None:
        self._connected = set(connected)
        self.sent: list[tuple[Site, bytes]] = []

    def send(self, site: Site, payload: bytes) -> bool:
        self.sent.append((site, payload))
        return site in self._connected

    def connected(self, site: Site) -> bool:
        return site in self._connected

    def to(self, site: Site) -> list[bytes]:
        return [payload for to, payload in self.sent if to == site]


class _Stack:
    def __init__(self, *, glob: dict | None = None, trunk: dict | None = None) -> None:
        self.config: dict[str, Any] = {
            "GLOBAL": glob if glob is not None else _acls(),
            "SYSTEMS": {
                "TRUNK": {"MODE": "MASTER", "TRUNKING": True, **(trunk if trunk is not None else _acls())},
                "TRUNK-2": {"MODE": "MASTER", "TRUNKING": True, **_acls()},
                "HOTSPOTS": {"MODE": "MASTER"},
            },
            "_SUB_MAP": {},
        }
        self.store = InMemorySubscriptionStore()
        self.registry = SiteRegistry()
        self.link = _Link((A, B, C))
        self.created: list[int] = []
        self.changed = 0
        legs = SiteLegs(self.store, self.config, self.created.append, self._changed)
        self.signalling = DmrtSignalling(self.config, self.registry, legs, self.link, InMemoryAclRouter())

    def _changed(self) -> None:
        self.changed += 1

    def register(self, site: Site, *ms: int, now: float = NOW) -> list[tuple[int, bool]]:
        self.signalling.received(site, dmrt_registration(0x01, ms, int(now)), now)
        ts, statuses = read_registration_confirmation(self.link.to(site)[-1])
        assert ts == int(now)
        return statuses

    def deregister(self, site: Site, *ms: int) -> list[int]:
        self.signalling.received(site, dmrt_registration(0x02, ms, int(NOW)), NOW)
        return read_deregistration(self.link.to(site)[-1])

    def subscribe(self, site: Site, *tgs: int, op: int = 0x03) -> list[int]:
        self.signalling.received(site, dmrt_subscription(op, tgs), NOW)
        reply_op, confirmed = read_subscription_confirmation(self.link.to(site)[-1])
        assert reply_op == op | 0x40
        return confirmed

    def pin(self, ms: int) -> tuple | None:
        return self.config["_SUB_MAP"].get(bytes_3(ms))


def test_registration_is_confirmed_and_pinned() -> None:
    """An accepted radio is held at its site and pinned in SUB_MAP, so private calls and unit data reach it there."""
    s = _Stack()
    assert s.register(A, MS) == [(MS, True)]
    assert [r.ms_id for r in s.registry.at_site(A)] == [MS]
    assert s.pin(MS) == ("TRUNK", 1, NOW, A.peer_id)
    assert len(s.link.sent) == 1


@pytest.mark.parametrize(
    ("glob", "trunk"),
    [
        pytest.param(_acls(sub="DENY:2140501"), _acls(), id="global"),
        pytest.param(_acls(), _acls(sub="DENY:2140000-2149999"), id="system"),
        pytest.param(_acls(sub="PERMIT:3100000-3199999"), _acls(), id="global-permit-list"),
    ],
)
def test_registration_denied_by_sub_acl(glob: dict, trunk: dict) -> None:
    """GLOBAL and the system's SUB_ACL both apply, as they do to DMRD: a denied radio is refused and not pinned."""
    s = _Stack(glob=glob, trunk=trunk)
    assert s.register(A, MS) == [(MS, False)]
    assert s.registry.registrations() == [] and s.pin(MS) is None


def test_a_scope_without_use_acl_is_not_checked() -> None:
    """A SUB_ACL applies only where USE_ACL is set, as for DMRD."""
    s = _Stack(glob=_acls(use_acl=False, sub="DENY:ALL"))
    assert s.register(A, MS) == [(MS, True)]


def test_registration_of_id_0_and_over_the_cap_is_refused() -> None:
    """Id 0 is never a radio, and a full site refuses a new one; each refusal clears only its own bit."""
    s = _Stack()
    for ms in range(1, MAX_REGISTRATIONS_PER_SITE + 1):
        s.registry.register(ms, A, NOW)
    assert s.register(A, 0, MS, 7) == [(0, False), (MS, False), (7, True)]
    assert s.register(B, 0, MS) == [(0, False), (MS, True)]
    assert s.link.to(A)[-1][13] == 0b100


def test_roaming_deregisters_at_the_old_site() -> None:
    """A radio registering elsewhere is de-registered at its old site with 0x42, sent only if that site is connected."""
    s = _Stack()
    s.register(A, MS)
    s.registry.register(MS2, GONE, NOW)

    assert s.register(C, MS, MS2, now=NOW + 5) == [(MS, True), (MS2, True)]

    assert [read_deregistration(p) for p in s.link.to(A)[1:]] == [[MS]]
    assert s.link.to(GONE) == []
    assert s.pin(MS) == ("TRUNK-2", 1, NOW + 5, C.peer_id)
    assert {r.ms_id for r in s.registry.at_site(C)} == {MS, MS2}


def test_deregistration_forgets_the_registration_and_its_pin() -> None:
    """0x02 at the radio's site drops it and its pin, and is answered with 0x42."""
    s = _Stack()
    s.register(A, MS)
    assert s.deregister(A, MS) == [MS]
    assert s.registry.registrations() == [] and s.pin(MS) is None


def test_deregistration_at_another_site_is_ignored() -> None:
    """A site cannot de-register a radio registered at another one; it is still answered."""
    s = _Stack()
    s.register(A, MS)
    assert s.deregister(B, MS) == [MS]
    assert [r.ms_id for r in s.registry.at_site(A)] == [MS]
    assert s.pin(MS) == ("TRUNK", 1, NOW, A.peer_id)


def test_deregistration_keeps_a_pin_heard_elsewhere() -> None:
    """Traffic heard elsewhere since the registration owns the pin; de-registering the radio leaves it."""
    s = _Stack()
    s.register(A, MS)
    s.config["_SUB_MAP"][bytes_3(MS)] = ("HOTSPOTS", 2, NOW + 5, HS)
    s.deregister(A, MS)
    assert s.registry.registrations() == []
    assert s.pin(MS) == ("HOTSPOTS", 2, NOW + 5, HS)


def test_deregistration_keeps_a_pin_on_another_system_with_the_same_peer_id() -> None:
    s = _Stack()
    s.register(A, MS)
    s.config["_SUB_MAP"][bytes_3(MS)] = ("TRUNK-2", 2, NOW + 5, A.peer_id)
    s.deregister(A, MS)
    assert s.pin(MS) == ("TRUNK-2", 2, NOW + 5, A.peer_id)


def test_subscription_holds_both_slots_once_per_message() -> None:
    """Accepted talkgroups are held as legs on both slots, with one routing-table push per message."""
    s = _Stack()
    assert s.subscribe(A, 214, 91) == [214, 91]
    assert site_tg_held(s.store, 214, "TRUNK") and site_tg_held(s.store, 91, "TRUNK")
    assert set(s.registry.subscribers("TRUNK", 214)) == {A.peer_id}
    assert s.created == [214, 91]
    assert s.changed == 1


def test_talkgroups_a_site_may_not_hold_are_refused_with_0() -> None:
    """Local, service, TG 4000 and parrot talkgroups go back as 0 and get no leg."""
    s = _Stack()
    assert s.subscribe(A, 4000, 9990, 214, 9, 5, 5000) == [0, 0, 214, 0, 0, 0]
    assert s.registry.held() == [("TRUNK", 214)]
    assert not s.store.has_table("4000") and not s.store.has_table("9990")


@pytest.mark.parametrize(
    ("glob", "trunk"),
    [
        pytest.param(_acls(), _acls(tg1="DENY:214"), id="system-ts1"),
        pytest.param(_acls(), _acls(tg2="DENY:214"), id="system-ts2"),
        pytest.param(_acls(tg2="DENY:214"), _acls(), id="global-ts2"),
    ],
)
def test_subscription_needs_both_slot_acls(glob: dict, trunk: dict) -> None:
    """dmrtc sets the slot bit freely, so a talkgroup either slot's ACL refuses would lose frames: it is refused."""
    s = _Stack(glob=glob, trunk=trunk)
    assert s.subscribe(A, 214, 91) == [0, 91]
    assert s.registry.held() == [("TRUNK", 91)]


def test_subscription_cap_per_site() -> None:
    """A site holds at most 512 talkgroups; one it already holds is still confirmed."""
    s = _Stack()
    tgs = list(range(10000, 10000 + MAX_TGS_PER_SITE))
    for i in range(0, MAX_TGS_PER_SITE, 64):
        s.subscribe(A, *tgs[i:i + 64])
    assert s.registry.tg_count(A) == MAX_TGS_PER_SITE

    assert s.subscribe(A, 214, tgs[0]) == [0, tgs[0]]
    assert s.subscribe(B, 214) == [214]


def test_first_holder_holds_and_last_holder_releases() -> None:
    """The legs follow the system's sites: armed by the first to subscribe, idled when the last one leaves."""
    s = _Stack()
    s.subscribe(A, 214)
    s.subscribe(B, 214)
    assert s.changed == 1

    assert s.subscribe(A, 214, op=0x04) == [214]
    assert site_tg_held(s.store, 214, "TRUNK") and s.changed == 1

    assert s.subscribe(B, 214, 777, op=0x04) == [214, 777]
    assert not site_tg_held(s.store, 214, "TRUNK") and s.changed == 2
    assert s.registry.held() == []


def test_malformed_dmrt_is_dropped_and_logged_once(caplog: pytest.LogCaptureFixture) -> None:
    """Short, wrong-length, network-bit and unknown opcodes get no answer and no state, one DEBUG line per opcode."""
    s = _Stack()
    payloads = [
        b"DMRT",
        b"DMRT",
        dmrt_registration(0x01, [MS], int(NOW))[:-1],
        dmrt_registration(0x01, [MS], int(NOW)) + b"\x00",
        dmrt_subscription(0x03, [214]) + b"\x00",
        b"DMRT\x41" + bytes(13),
        b"DMRT\x0b" + bytes(22),
        b"DMRT\x81",
    ]
    with caplog.at_level(logging.DEBUG, logger=SIGNALLING_LOGGER):
        for payload in payloads:
            s.signalling.received(A, payload, NOW)

    assert s.link.sent == [] and s.registry.registrations() == [] and s.registry.held() == []
    assert s.store.snapshot() == () and s.config["_SUB_MAP"] == {}
    logged = [r.getMessage() for r in caplog.records if r.name == SIGNALLING_LOGGER]
    assert len(logged) == 6
    assert logged[0] == (
        "(TRUNK) DMRT of 4 bytes (opcode none) from Tier III site 214000101 ignored: malformed or not handled"
    )


def test_a_malformed_dmrt_is_logged_again_after_a_minute(caplog: pytest.LogCaptureFixture) -> None:
    """What was logged is forgotten after a minute: sites logging in under new IDs cannot grow it for ever."""
    s = _Stack()
    sites = [Site("TRUNK", bytes_4(214000101 + n)) for n in range(3)]
    with caplog.at_level(logging.DEBUG, logger=SIGNALLING_LOGGER):
        for site in sites:
            s.signalling.received(site, b"DMRT", NOW)
        s.signalling.received(sites[0], b"DMRT", NOW + 30)
        s.signalling.received(sites[0], b"DMRT", NOW + 61)

    assert len([r for r in caplog.records if r.name == SIGNALLING_LOGGER]) == 4
    assert list(s.signalling._ignored._noted) == [(sites[0], -1)]
