# ADN DMR Peer Server - tests trunking sites
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

"""TrunkSites: which sites take a frame, call UUIDs, the parrot per site, the tick, re-login pins and persistence."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from adn_server.application.subscription.site_tg_ops import site_tg_held
from adn_server.application.subscription.subscription_reset_ops import deactivate_system_legs_store
from adn_server.application.trunking.legs import SiteLegs
from adn_server.application.trunking.ports import SiteLink, TrunkRegistrationStore
from adn_server.application.trunking.registry import (
    ECHO_HOLD_S,
    REGISTRATION_TTL_S,
    Registration,
    Site,
    SiteRegistry,
)
from adn_server.application.trunking.signalling import DmrtSignalling
from adn_server.application.trunking.sites import TrunkSites
from adn_server.domain import bytes_3, bytes_4
from adn_server.domain.trunking import derive_call_uuid
from adn_server.infrastructure.acl_router import InMemoryAclRouter
from adn_server.infrastructure.subscription_store import InMemorySubscriptionStore
from tests.support.dmrtc_wire import dmrt_registration, dmrt_subscription

A = Site("TRUNK", bytes_4(214000101))
B = Site("TRUNK", bytes_4(214000102))
C = Site("TRUNK-2", bytes_4(214000201))
HS = bytes_4(2140001)
MS, MS2, MS3 = 2140501, 2140502, 2140503
NOW = 1_761_300_000.0
STREAM_1, STREAM_2 = b"\x00\x00\x00\x01", b"\x00\x00\x00\x02"
UUID_1 = bytes(range(0xA0, 0xB0))
PATH = "trunk_registrations.json"


class _Link(SiteLink):
    def send(self, site: Site, payload: bytes) -> bool:
        return True

    def connected(self, site: Site) -> bool:
        return True


class _Store(TrunkRegistrationStore):
    """Records the writes; the file it reads keeps what it held, as while a write is on its way."""

    def __init__(self) -> None:
        self.saved: list[tuple[str, list[Registration]]] = []
        self.on_disk: list[Registration] = []
        self.loads = 0
        self.failed = False

    def load(self, path: str) -> list[Registration]:
        self.loads += 1
        return list(self.on_disk)

    def save(self, path: str, registrations: Iterable[Registration]) -> None:
        self.saved.append((path, sorted(registrations)))

    def last_write_failed(self) -> bool:
        return self.failed


class _Stack:
    def __init__(self, registry: SiteRegistry | None = None) -> None:
        self.config: dict[str, Any] = {
            "GLOBAL": {},
            "SYSTEMS": {
                "TRUNK": {"MODE": "MASTER", "TRUNKING": True},
                "TRUNK-2": {"MODE": "MASTER", "TRUNKING": True},
                "HOTSPOTS": {"MODE": "MASTER"},
            },
            "_SUB_MAP": {},
        }
        self.store = InMemorySubscriptionStore()
        self.registry = registry if registry is not None else SiteRegistry()
        self.saved = _Store()
        legs = SiteLegs(self.store, self.config, lambda tg: None, lambda: None)
        signalling = DmrtSignalling(self.config, self.registry, legs, _Link(), InMemoryAclRouter())
        self.sites = TrunkSites(self.config, self.registry, legs, signalling, self.saved, PATH)

    def register(self, site: Site, *ms: int, now: float = NOW) -> None:
        self.sites.dmrt_received(site.system, site.peer_id, dmrt_registration(0x01, ms, int(now)), now)

    def subscribe(self, site: Site, *tgs: int) -> None:
        self.sites.dmrt_received(site.system, site.peer_id, dmrt_subscription(0x03, tgs), NOW)

    def group(self, tg: int, *, rf_src: int = MS, system: str = "TRUNK", sender: Site | None = None,
              now: float = NOW) -> set[bytes]:
        sender_id = sender.peer_id if sender is not None else None
        return set(self.sites.site_targets(system, bytes_3(rf_src), bytes_3(tg), False, now, sender=sender_id))

    def unit(self, dst: int, *, system: str = "TRUNK", sender: Site | None = None) -> tuple[bytes, ...]:
        sender_id = sender.peer_id if sender is not None else None
        return tuple(self.sites.site_targets(system, bytes_3(MS2), bytes_3(dst), True, NOW, sender=sender_id))

    def keyed(self, site: Site, rf_src: int, tg: int, *, stream: bytes, now: float, end: float) -> None:
        self.sites.stream_started(site.system, site.peer_id, stream, UUID_1, bytes_3(rf_src), bytes_3(tg), False, now)
        self.sites.stream_ended(site.system, site.peer_id, bytes_3(rf_src), bytes_3(tg), False, end)

    def pin(self, ms: int) -> tuple | None:
        return self.config["_SUB_MAP"].get(bytes_3(ms))


def test_group_frames_go_to_the_subscribers_but_never_back_to_the_sender() -> None:
    """A talkgroup reaches every site of the system holding it; the site it came from never gets it back."""
    s = _Stack()
    s.subscribe(A, 214)
    s.subscribe(B, 214)
    s.subscribe(C, 214)

    assert s.group(214) == {A.peer_id, B.peer_id}
    assert s.group(214, sender=A) == {B.peer_id}
    assert s.group(214, sender=Site("TRUNK", bytes_4(214000103))) == {A.peer_id, B.peer_id}
    assert s.group(214, system="TRUNK-2", sender=C) == set()
    assert s.group(91) == set()


def test_unit_frames_go_only_to_the_pinned_site() -> None:
    """A unit frame follows the radio's SUB_MAP pin, to one site of this system, and never back to its sender."""
    s = _Stack()
    s.register(A, MS)
    assert s.unit(MS) == (A.peer_id,)
    assert s.unit(MS, sender=A) == ()
    assert s.unit(MS, system="TRUNK-2") == ()

    s.config["_SUB_MAP"][bytes_3(MS)] = ("HOTSPOTS", 2, NOW + 1, HS)
    assert s.unit(MS) == ()
    s.config["_SUB_MAP"][bytes_3(MS)] = ("TRUNK", 2, NOW + 2)
    assert s.unit(MS) == ()


def test_an_unknown_unit_gives_nothing() -> None:
    """A radio with no pin is reached by no site, even one holding a talkgroup with the same number."""
    s = _Stack()
    s.subscribe(A, MS)
    assert s.group(MS) == {A.peer_id}
    assert s.unit(MS) == ()


def test_each_site_gets_only_its_own_parrot_playback() -> None:
    """Two radios at two sites key the parrot: each playback goes to its keyer's site, held from the end of the over."""
    s = _Stack()
    s.keyed(A, MS, 9990, stream=STREAM_1, now=NOW, end=NOW + 100)
    s.keyed(B, MS2, 9990, stream=STREAM_2, now=NOW + 1, end=NOW + 2)

    assert s.group(9990, rf_src=MS, now=NOW + 102) == {A.peer_id}
    assert s.group(9990, rf_src=MS2, now=NOW + 4) == {B.peer_id}
    assert s.group(9990, rf_src=MS3, now=NOW + 4) == set()
    assert s.group(9990, rf_src=MS, sender=A, now=NOW + 1) == set()
    assert s.group(9990, rf_src=MS, now=NOW + 100 + ECHO_HOLD_S) == {A.peer_id}
    assert s.group(9990, rf_src=MS, now=NOW + 101 + ECHO_HOLD_S) == set()


def test_call_uuid_is_the_sites_own_or_a_stable_derived_one() -> None:
    """dmrtc keys calls on the UUID: a site's stream keeps its UUID, any other stream gets one per stream."""
    s = _Stack()
    s.keyed(A, MS, 214, stream=STREAM_1, now=NOW, end=NOW + 1)

    assert s.sites.call_uuid(STREAM_1, bytes_3(MS), NOW + 1) == UUID_1
    derived = s.sites.call_uuid(STREAM_2, bytes_3(MS), NOW + 1)
    assert derived == derive_call_uuid(STREAM_2) == s.sites.call_uuid(STREAM_2, bytes_3(MS), NOW + 2)
    assert derived != UUID_1 and derived != derive_call_uuid(STREAM_1)


def test_a_stream_from_a_registered_radio_refreshes_its_registration() -> None:
    """Traffic from a radio at its own site keeps it registered past the TTL; traffic at another site does not."""
    s = _Stack()
    s.register(A, MS, MS2)
    s.sites.stream_started("TRUNK", A.peer_id, STREAM_1, UUID_1, bytes_3(MS), bytes_3(214), False, NOW + 3600)
    s.sites.stream_started("TRUNK", B.peer_id, STREAM_2, UUID_1, bytes_3(MS2), bytes_3(214), False, NOW + 3600)

    s.sites.tick(NOW + REGISTRATION_TTL_S + 1)

    assert [r.ms_id for r in s.registry.registrations()] == [MS]


def test_tick_expires_registrations_forgets_pins_and_rearms_legs() -> None:
    """The tick drops stale registrations and only their own pins, forgets old call UUIDs and re-arms held legs."""
    s = _Stack()
    s.register(A, MS, MS2)
    s.config["_SUB_MAP"][bytes_3(MS2)] = ("HOTSPOTS", 2, NOW + 5, HS)
    s.subscribe(A, 214)
    s.keyed(A, MS, 214, stream=STREAM_1, now=NOW, end=NOW + 1)
    deactivate_system_legs_store(s.store, "TRUNK", NOW + 2)

    s.sites.tick(NOW + REGISTRATION_TTL_S + 1)

    assert s.registry.registrations() == []
    assert s.pin(MS) is None
    assert s.pin(MS2) == ("HOTSPOTS", 2, NOW + 5, HS)
    assert site_tg_held(s.store, 214, "TRUNK")
    assert s.sites.call_uuid(STREAM_1, bytes_3(MS), NOW + REGISTRATION_TTL_S + 2) == derive_call_uuid(STREAM_1)


def test_site_logged_in_repins_with_the_registration_time() -> None:
    """After the base login purge, the site's radios are pinned again as of their registration, unless heard since."""
    s = _Stack()
    s.register(A, MS, now=NOW)
    s.register(A, MS2, now=NOW + 10)
    s.register(A, MS3, now=NOW + 30)
    s.config["_SUB_MAP"].clear()
    s.config["_SUB_MAP"][bytes_3(MS2)] = ("HOTSPOTS", 2, NOW + 20, HS)
    s.config["_SUB_MAP"][bytes_3(MS3)] = ("HOTSPOTS", 2, NOW + 25, HS)

    s.sites.site_logged_in("TRUNK", A.peer_id)

    assert s.pin(MS) == ("TRUNK", 1, NOW, A.peer_id)
    assert s.pin(MS2) == ("HOTSPOTS", 2, NOW + 20, HS)
    assert s.pin(MS3) == ("TRUNK", 1, NOW + 30, A.peer_id)


def test_site_left_releases_its_talkgroups_and_keeps_its_radios() -> None:
    """A site leaving releases the talkgroups no other site holds; its registrations and pins wait for it."""
    s = _Stack()
    s.register(A, MS)
    s.subscribe(A, 214, 91)
    s.subscribe(B, 214)

    s.sites.site_left("TRUNK", A.peer_id, NOW + 1)

    assert site_tg_held(s.store, 214, "TRUNK") and not site_tg_held(s.store, 91, "TRUNK")
    assert s.group(214) == {B.peer_id}
    assert [r.ms_id for r in s.registry.at_site(A)] == [MS]
    assert s.pin(MS) == ("TRUNK", 1, NOW, A.peer_id)


def test_system_removed_drops_its_sites_and_pins() -> None:
    """A system leaving the config takes its subscriptions, registrations and pins with it, and nothing else."""
    s = _Stack()
    s.register(A, MS)
    s.subscribe(A, 214)
    s.register(C, MS2)
    s.subscribe(C, 214)

    s.sites.system_removed("TRUNK", NOW + 1)

    assert [r.ms_id for r in s.registry.registrations()] == [MS2]
    assert s.pin(MS) is None and s.pin(MS2) == ("TRUNK-2", 1, NOW, C.peer_id)
    assert not site_tg_held(s.store, 214, "TRUNK") and site_tg_held(s.store, 214, "TRUNK-2")


def test_the_file_is_read_once_and_later_systems_restore_what_was_last_saved() -> None:
    """A system created after a save gets what that save holds, although the file may not show it yet."""
    s = _Stack()
    s.saved.on_disk = [Registration(MS, A, NOW), Registration(MS2, C, NOW)]
    s.sites.system_added("TRUNK", NOW)
    s.sites.system_added("TRUNK-2", NOW)
    s.sites.system_removed("TRUNK-2", NOW)
    s.sites.save()

    s.sites.system_added("TRUNK-2", NOW + 1)

    assert s.saved.loads == 1
    assert s.registry.registrations() == [Registration(MS, A, NOW)]


def test_persist_if_changed_writes_only_on_a_revision_change() -> None:
    """Registrations restored at startup are not written back; a change is written once; save() always writes."""
    registry = SiteRegistry()
    registry.restore([Registration(MS3, A, NOW)], frozenset({"TRUNK"}), NOW + 1)
    s = _Stack(registry)
    assert s.sites.persist_if_changed() is False

    s.register(B, MS)
    assert s.sites.persist_if_changed() is True
    assert s.sites.persist_if_changed() is False
    s.sites.save()

    expected = [Registration(MS, B, NOW), Registration(MS3, A, NOW)]
    assert s.saved.saved == [(PATH, expected), (PATH, expected)]


def test_a_write_the_store_could_not_finish_is_made_again_without_a_change() -> None:
    """dmrtc does not register again after a reconnect: a failed write must not wait for the next change."""
    s = _Stack()
    s.register(A, MS)
    assert s.sites.persist_if_changed() is True
    s.saved.failed = True

    assert s.sites.persist_if_changed() is True
    s.saved.failed = False
    assert s.sites.persist_if_changed() is False
    assert len(s.saved.saved) == 2
