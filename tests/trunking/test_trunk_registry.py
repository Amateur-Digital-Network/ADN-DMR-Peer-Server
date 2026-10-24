# ADN DMR Peer Server - tests trunking registry
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

"""Site registry: registrations with a per-site cap and a TTL, subscriptions, call UUIDs and parrot keyers."""

from __future__ import annotations

from adn_server.application.trunking.registry import (
    CALL_UUID_KEEP_S,
    ECHO_HOLD_S,
    MAX_REGISTRATIONS_PER_SITE,
    REGISTRATION_TTL_S,
    Registration,
    Site,
    SiteRegistry,
)

A = Site("TRUNK", b"\x0c\xc1\x7a\x01")
B = Site("TRUNK", b"\x0c\xc1\x7a\x02")
C = Site("TRUNK-2", b"\x0c\xc1\x7a\x03")
MS, MS2 = 2140501, 2140502
RF_1, RF_2 = b"\x20\xa8\x55", b"\x20\xa8\x56"
T0 = 1_761_300_000.0  # on an hour boundary


def test_cap_per_site() -> None:
    """A site holds at most 1024 radios; one already there re-registers without counting twice."""
    registry = SiteRegistry()
    for ms in range(1, MAX_REGISTRATIONS_PER_SITE + 1):
        assert registry.register(ms, A, T0) == (True, None)
    assert registry.register(MS, A, T0) == (False, None)
    assert registry.register(1, A, T0 + 1) == (True, None)
    assert registry.register(MS2, B, T0) == (True, None)
    assert registry.register(MS2, A, T0) == (False, None)
    assert len(registry.at_site(A)) == MAX_REGISTRATIONS_PER_SITE
    assert [r.ms_id for r in registry.at_site(B)] == [MS2]


def test_roaming_returns_the_previous_site() -> None:
    """A radio registering at another site moves there, and the old site is named so it can be told."""
    registry = SiteRegistry()
    assert registry.register(MS, A, T0) == (True, None)
    assert registry.register(MS, B, T0 + 5) == (True, A)
    assert registry.at_site(A) == []
    assert registry.at_site(B) == [Registration(MS, B, T0 + 5)]


def test_deregistration_only_at_the_same_site() -> None:
    """A site cannot de-register a radio that has since registered elsewhere."""
    registry = SiteRegistry()
    registry.register(MS, A, T0)
    assert registry.deregister(MS, B) is False
    assert registry.at_site(A) == [Registration(MS, A, T0)]
    assert registry.deregister(MS, A) is True
    assert registry.registrations() == []
    assert registry.deregister(MS, A) is False


def test_registration_expires_after_ttl() -> None:
    """A registration nobody refreshed for 24 h is dropped and returned, so its pin can go too."""
    registry = SiteRegistry()
    registry.register(MS, A, T0)
    registry.register(MS2, B, T0 + 100)
    assert registry.expire(T0 + REGISTRATION_TTL_S) == []
    assert registry.expire(T0 + REGISTRATION_TTL_S + 1) == [Registration(MS, A, T0)]
    assert registry.registrations() == [Registration(MS2, B, T0 + 100)]
    assert registry.at_site(A) == []


def test_traffic_at_the_site_refreshes_the_registration() -> None:
    """A radio heard at its site keeps its registration; heard at another site, it does not."""
    registry = SiteRegistry()
    registry.register(MS, A, T0)
    registry.register(MS2, A, T0)
    registry.touch(MS, A, T0 + 80_000)
    registry.touch(MS2, B, T0 + 80_000)
    registry.touch(7654321, A, T0 + 80_000)
    assert registry.expire(T0 + REGISTRATION_TTL_S + 1) == [Registration(MS2, A, T0)]
    assert registry.registrations() == [Registration(MS, A, T0 + 80_000)]


def test_revision_changes_on_moves_and_hourly_refreshes() -> None:
    """The revision says when the saved file is stale: every move, and a refresh once an hour."""
    registry = SiteRegistry()
    revisions = [registry.revision]

    def changed() -> bool:
        revisions.append(registry.revision)
        return revisions[-1] != revisions[-2]

    registry.register(MS, A, T0)
    assert changed()
    registry.register(MS, A, T0 + 60)
    registry.touch(MS, A, T0 + 3599)
    assert not changed()
    registry.touch(MS, A, T0 + 3600)
    assert changed()
    registry.register(MS, A, T0 + 7200)
    assert changed()
    registry.register(MS, B, T0 + 7300)
    assert changed()
    registry.deregister(MS, A)
    registry.expire(T0 + 7400)
    assert not changed()
    registry.deregister(MS, B)
    assert changed()
    registry.register(MS, A, T0)
    assert changed()
    registry.expire(T0 + REGISTRATION_TTL_S + 1)
    assert changed()


def test_first_and_last_holder() -> None:
    """Legs are armed for a system's first holder of a talkgroup and released after its last."""
    registry = SiteRegistry()
    assert registry.subscribe(A, 91) is True
    assert registry.subscribe(B, 91) is False
    assert registry.subscribe(A, 91) is False
    assert registry.subscribe(C, 91) is True
    assert set(registry.subscribers("TRUNK", 91)) == {A.peer_id, B.peer_id}
    assert list(registry.subscribers("TRUNK-2", 91)) == [C.peer_id]
    assert list(registry.subscribers("TRUNK", 214)) == []
    assert (registry.is_subscribed(A, 91), registry.is_subscribed(A, 214), registry.tg_count(A)) == (True, False, 1)
    assert registry.unsubscribe(A, 91) is False
    assert registry.unsubscribe(A, 91) is False
    assert registry.unsubscribe(B, 91) is True
    assert registry.unsubscribe(A, 214) is False
    assert registry.held() == [("TRUNK-2", 91)]


def test_drop_site_keeps_registrations_and_returns_released_tgs() -> None:
    """A site that leaves loses its talkgroups; its radios stay, because dmrtc does not register them again."""
    registry = SiteRegistry()
    registry.register(MS, A, T0)
    for tg in (91, 214, 2141):
        registry.subscribe(A, tg)
    registry.subscribe(B, 91)
    assert registry.drop_site(A) == [214, 2141]
    assert registry.at_site(A) == [Registration(MS, A, T0)]
    assert registry.tg_count(A) == 0
    assert list(registry.subscribers("TRUNK", 91)) == [B.peer_id]
    assert registry.held() == [("TRUNK", 91)]
    assert registry.drop_site(A) == []


def test_drop_system() -> None:
    """A removed or flipped trunk system loses every site's talkgroups and every radio registered there."""
    registry = SiteRegistry()
    registry.register(MS, A, T0)
    registry.register(MS2, C, T0)
    registry.subscribe(A, 91)
    registry.subscribe(B, 91)
    registry.subscribe(B, 214)
    registry.subscribe(C, 91)
    revision = registry.revision
    released, dropped = registry.drop_system("TRUNK")
    assert sorted(released) == [91, 214]
    assert dropped == [Registration(MS, A, T0)]
    assert registry.registrations() == [Registration(MS2, C, T0)]
    assert registry.held() == [("TRUNK-2", 91)]
    assert registry.revision != revision


def test_call_uuids_are_kept_past_the_source_timeout() -> None:
    """A site's call UUID outlives the longest stream, then goes."""
    registry = SiteRegistry()
    registry.note_uuid(b"\x00\x00\x00\x01", RF_1, b"u" * 16, T0)
    assert registry.uuid(b"\x00\x00\x00\x02", RF_1) is None
    registry.trim(T0 + CALL_UUID_KEEP_S)
    assert registry.uuid(b"\x00\x00\x00\x01", RF_1) == b"u" * 16
    registry.trim(T0 + CALL_UUID_KEEP_S + 1)
    assert registry.uuid(b"\x00\x00\x00\x01", RF_1) is None


def test_one_stream_id_keyed_by_two_radios_keeps_two_call_uuids() -> None:
    """Two sites may pick one stream ID at once; the radio keying each call tells their UUIDs apart."""
    registry = SiteRegistry()
    registry.note_uuid(b"\x00\x00\x00\x01", RF_1, b"u" * 16, T0)
    registry.note_uuid(b"\x00\x00\x00\x01", RF_2, b"v" * 16, T0)
    assert registry.uuid(b"\x00\x00\x00\x01", RF_1) == b"u" * 16
    assert registry.uuid(b"\x00\x00\x00\x01", RF_2) == b"v" * 16


def test_echo_keyer_is_per_radio_and_held_for_the_playback() -> None:
    """Two radios keying the parrot at two sites each get their own site, until the playback window ends."""
    registry = SiteRegistry()
    registry.note_echo("TRUNK", 9990, MS, A.peer_id, T0)
    registry.note_echo("TRUNK", 9990, MS2, B.peer_id, T0 + 1)
    assert registry.echo_site("TRUNK", 9990, MS, T0 + ECHO_HOLD_S) == A.peer_id
    assert registry.echo_site("TRUNK", 9990, MS2, T0 + ECHO_HOLD_S) == B.peer_id
    assert registry.echo_site("TRUNK", 9990, 7654321, T0) is None
    assert registry.echo_site("TRUNK-2", 9990, MS, T0) is None
    assert registry.echo_site("TRUNK", 9990, MS, T0 + ECHO_HOLD_S + 1) is None
    registry.trim(T0 + ECHO_HOLD_S + 1.5)
    assert registry.echo_site("TRUNK", 9990, MS2, T0 + ECHO_HOLD_S + 1) is None


def test_restore_drops_expired_and_non_trunk() -> None:
    """Saved registrations come back only if still live and on a system that is still TRUNKING."""
    registry = SiteRegistry()
    now = T0 + REGISTRATION_TTL_S
    registry.restore(
        [
            Registration(MS, A, T0 + 10),
            Registration(MS2, A, T0 - 1),
            Registration(7654321, Site("HOTSPOT", A.peer_id), T0 + 10),
        ],
        frozenset({"TRUNK"}),
        now,
    )
    assert registry.registrations() == [Registration(MS, A, T0 + 10)]


def test_restore_keeps_the_newest_under_the_cap() -> None:
    """Over the cap the oldest go, and a radio saved twice keeps its newest site."""
    registry = SiteRegistry()
    saved = [Registration(ms, A, T0 + ms) for ms in range(1, MAX_REGISTRATIONS_PER_SITE + 2)]
    saved += [Registration(MS, A, T0), Registration(MS, B, T0 + 5000)]
    registry.restore(saved, frozenset({"TRUNK"}), T0 + 6000)
    assert {r.ms_id for r in registry.at_site(A)} == set(range(2, MAX_REGISTRATIONS_PER_SITE + 2))
    assert registry.at_site(B) == [Registration(MS, B, T0 + 5000)]
