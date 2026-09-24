# ADN DMR Peer Server - tests infrastructure subscription store index
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

"""Hot-path indexes on InMemorySubscriptionStore."""

from __future__ import annotations

from adn_server.application.subscription.router import SubscriptionRouter
from adn_server.application.subscription.routing_table_import import subscriptions_from_routing_table
from adn_server.domain import bytes_3
from adn_server.domain.subscription import SubscriptionPhase
from adn_server.domain.value_objects import TgId
from adn_server.domain.voice_routing import VoiceIngress
from adn_server.infrastructure.subscription_store import InMemorySubscriptionStore


def _row(*, system: str, ts: int, tgid: int, active: bool) -> dict:
    return {
        "SYSTEM": system,
        "TS": ts,
        "TGID": bytes_3(tgid),
        "ACTIVE": active,
        "TIMEOUT": 600.0,
        "TO_TYPE": "OFF" if active else "ON",
        "ON": [bytes_3(tgid)],
        "OFF": [],
        "RESET": [],
        "TIMER": 0.0,
    }


def test_relay_tables_index_matches_full_scan() -> None:
    bridges = {
        "7147": [
            _row(system="OBP-CL", ts=1, tgid=7147, active=True),
            _row(system="SYSTEM", ts=2, tgid=7147, active=True),
        ]
    }
    store = InMemorySubscriptionStore()
    store.replace_all(subscriptions_from_routing_table(bridges))
    router = SubscriptionRouter(store)
    assert store.relay_tables_with_active_source("OBP-CL", 1, 7147) == ("7147",)
    assert router.relay_tables_with_active_source("OBP-CL", 1, 7147) == ("7147",)


def test_has_active_target_leg_tracks_upsert() -> None:
    store = InMemorySubscriptionStore()
    store.replace_all(subscriptions_from_routing_table({"7147": [_row(system="SYSTEM", ts=2, tgid=7147, active=True)]}))
    assert store.has_active_target_leg("SYSTEM", 2, 7147) is True
    sub = store.snapshot()[0]
    from dataclasses import replace

    idle = replace(sub, state=replace(sub.state, phase=SubscriptionPhase.IDLE))
    store.upsert(idle)
    assert store.has_active_target_leg("SYSTEM", 2, 7147) is False


def test_resolve_uses_table_index() -> None:
    bridges = {
        "7147": [
            _row(system="OBP-CL", ts=1, tgid=7147, active=True),
            _row(system="SYSTEM", ts=2, tgid=7147, active=True),
        ]
    }
    store = InMemorySubscriptionStore()
    store.replace_all(subscriptions_from_routing_table(bridges))
    legs = SubscriptionRouter(store).resolve(
        VoiceIngress(source_system="OBP-CL", slot=1, dst_tgid=TgId(7147), source_is_obp=True)
    )
    assert len(legs) == 1
    assert legs[0].target_system == "SYSTEM"


# Timers, in-band signalling and resets change a leg in place and then upsert
# the same object, so the store can no longer read the old state off it.


def test_in_place_deactivation_leaves_the_active_indexes() -> None:
    store = InMemorySubscriptionStore()
    store.replace_all(subscriptions_from_routing_table({"7147": [_row(system="SYSTEM", ts=2, tgid=7147, active=True)]}))
    sub = store.snapshot()[0]
    sub.state.phase = SubscriptionPhase.IDLE
    store.upsert(sub)
    assert store.relay_tables_with_active_source("SYSTEM", 2, 7147) == ()
    assert store.has_active_target_leg("SYSTEM", 2, 7147) is False
    assert store.legs_in_table("7147") == (sub,)


def test_in_place_activation_keeps_other_active_legs_counted() -> None:
    # Two legs of SYSTEM delivering on TS2 TG 7147: its own table and a
    # translated one (table 9000 rewritten to 7147) share the active index key.
    bridges = {
        "7147": [_row(system="SYSTEM", ts=2, tgid=7147, active=True)],
        "9000": [_row(system="SYSTEM", ts=2, tgid=7147, active=False)],
    }
    store = InMemorySubscriptionStore()
    store.replace_all(subscriptions_from_routing_table(bridges))
    translated = next(s for s in store.snapshot() if s.table_key() == "9000")
    translated.state.phase = SubscriptionPhase.ACTIVE
    store.upsert(translated)
    assert store.relay_tables_with_active_source("SYSTEM", 2, 7147) == ("7147", "9000")

    main = next(s for s in store.snapshot() if s.table_key() == "7147")
    main.state.phase = SubscriptionPhase.IDLE
    store.upsert(main)
    assert store.relay_tables_with_active_source("SYSTEM", 2, 7147) == ("9000",)
    assert store.has_active_target_leg("SYSTEM", 2, 7147) is True


def test_remove_after_an_in_place_change_clears_every_index() -> None:
    store = InMemorySubscriptionStore()
    store.replace_all(subscriptions_from_routing_table({"7147": [_row(system="SYSTEM", ts=2, tgid=7147, active=True)]}))
    sub = store.snapshot()[0]
    sub.state.phase = SubscriptionPhase.IDLE
    assert store.remove(sub.subscription_id) is True
    assert store.relay_tables_with_active_source("SYSTEM", 2, 7147) == ()
    assert store.has_active_target_leg("SYSTEM", 2, 7147) is False
    assert store.legs_in_table("7147") == ()
