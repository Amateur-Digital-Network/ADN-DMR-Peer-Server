# ADN DMR Peer Server - tests trunking legs
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

"""A Tier III system's talkgroup legs: STATIC on both slots, written by id, idled on release, re-armed by ensure."""

from __future__ import annotations

import logging

import pytest

from adn_server.application.subscription.rule_timer_ops import apply_rule_timer_store
from adn_server.application.subscription.site_tg_ops import site_tg_held
from adn_server.application.subscription.subscription_reset_ops import deactivate_system_legs_store
from adn_server.application.subscription.subscription_table_ops import ensure_dynamic_relay_store
from adn_server.application.trunking.legs import SiteLegs
from adn_server.domain import bytes_3
from adn_server.domain.subscription import (
    ActivationPolicy,
    AudioChannel,
    Subscription,
    SubscriptionId,
    SubscriptionPhase,
    SystemId,
    TgId,
)
from adn_server.infrastructure.subscription_store import InMemorySubscriptionStore

NOW = 1_761_300_000.0
TMOUT_S = 10 * 60.0
LEGS_LOGGER = "adn_server.application.trunking.legs"


class _Legs:
    def __init__(self) -> None:
        self.store = InMemorySubscriptionStore()
        self.config = {
            "SYSTEMS": {
                "TRUNK": {"MODE": "MASTER", "TRUNKING": True, "DEFAULT_UA_TIMER": 10},
                "HOTSPOTS": {"MODE": "MASTER"},
                "OBP-1": {"MODE": "OPENBRIDGE"},
            }
        }
        self.created: list[int] = []
        self.changed = 0
        self.legs = SiteLegs(self.store, self.config, self.created.append, self._changed)

    def _changed(self) -> None:
        self.changed += 1

    def leg(self, tg: int, slot: int, system: str = "TRUNK") -> Subscription | None:
        return self.store.get(SubscriptionId(AudioChannel(TgId(tg), slot), SystemId(system)))


def _assert_held(s: _Legs, tg: int) -> None:
    for slot in (1, 2):
        leg = s.leg(tg, slot)
        assert leg is not None
        assert leg.policy == ActivationPolicy.STATIC and leg.is_active()
        assert s.store.relay_tables_with_active_source("TRUNK", slot, tg) == (str(tg),)
    assert site_tg_held(s.store, tg, "TRUNK")


def test_hold_arms_both_slots() -> None:
    """A site has no timeslots: its talkgroup is a STATIC ACTIVE leg on TS1 and TS2, whatever slot bit it sends."""
    s = _Legs()
    s.legs.hold("TRUNK", [214, 91], NOW)

    _assert_held(s, 214)
    _assert_held(s, 91)
    assert s.leg(214, 1).state.timer_expires_at == NOW + TMOUT_S


def test_hold_creates_a_missing_table_once_and_reports_only_that() -> None:
    """The table is built as routing builds it for a new TG, and only then is on_table_created called."""
    s = _Legs()
    ensure_dynamic_relay_store(s.store, 91, "HOTSPOTS", 2, 10.0, s.config["SYSTEMS"], NOW)

    s.legs.hold("TRUNK", [214, 91], NOW)
    s.legs.hold("TRUNK", [214], NOW + 1)

    assert s.created == [214]
    assert s.changed == 2
    assert s.leg(91, 2, "HOTSPOTS").is_active()
    assert not s.leg(214, 1, "HOTSPOTS").is_active()
    assert s.store.has_table("214")


def test_last_holder_release_idles_both_slots() -> None:
    """Released legs become the INBAND idle legs a hotspot MASTER has; the table and other systems stay."""
    s = _Legs()
    ensure_dynamic_relay_store(s.store, 214, "HOTSPOTS", 2, 10.0, s.config["SYSTEMS"], NOW)
    s.legs.hold("TRUNK", [214], NOW)

    s.legs.release("TRUNK", [214], NOW + 5)

    for slot in (1, 2):
        leg = s.leg(214, slot)
        assert leg.policy == ActivationPolicy.INBAND and leg.state.phase == SubscriptionPhase.IDLE
        assert leg.triggers.on == (bytes_3(214),)
        assert leg.state.timer_expires_at == NOW + 5 + TMOUT_S
    assert not site_tg_held(s.store, 214, "TRUNK")
    assert s.leg(214, 2, "HOTSPOTS").is_active()
    assert s.changed == 2


def test_nothing_to_hold_or_release_changes_nothing() -> None:
    """An empty message or a site leaving with no talkgroups pushes no routing table."""
    s = _Legs()
    s.legs.hold("TRUNK", [], NOW)
    s.legs.release("TRUNK", [], NOW)
    assert s.changed == 0 and s.store.snapshot() == ()


def test_ensure_rearms_after_a_bridge_reset(caplog: pytest.LogCaptureFixture) -> None:
    """A BRIDGERESET idles every leg of the system; ensure re-arms the ones sites still hold, once."""
    s = _Legs()
    s.legs.hold("TRUNK", [214, 91], NOW)
    deactivate_system_legs_store(s.store, "TRUNK", NOW + 1)
    assert not site_tg_held(s.store, 214, "TRUNK")

    with caplog.at_level(logging.INFO, logger=LEGS_LOGGER):
        s.legs.ensure([("TRUNK", 214)], NOW + 2)
        s.legs.ensure([("TRUNK", 214)], NOW + 3)

    _assert_held(s, 214)
    assert not site_tg_held(s.store, 91, "TRUNK")
    assert s.changed == 2
    logged = [r.getMessage() for r in caplog.records if r.name == LEGS_LOGGER]
    assert logged == ["(TRUNK) Tier III talkgroup 214 re-armed after a bridge reset"]


def test_ensure_rearms_a_leg_a_call_activated_after_the_reset() -> None:
    """A call after the reset turns the idle legs ACTIVE on a UA timer; they must become STATIC before it ends."""
    s = _Legs()
    s.legs.hold("TRUNK", [214], NOW)
    deactivate_system_legs_store(s.store, "TRUNK", NOW + 1)
    for slot in (1, 2):
        leg = s.leg(214, slot)
        leg.state.phase = SubscriptionPhase.ACTIVE
        s.store.upsert(leg)

    s.legs.ensure([("TRUNK", 214)], NOW + 2)

    _assert_held(s, 214)


def test_ensure_rearms_after_the_table_is_removed() -> None:
    """A table that disappeared under a held talkgroup is built again, and reported as created."""
    s = _Legs()
    s.legs.hold("TRUNK", [214], NOW)
    for leg in s.store.legs_in_table("214"):
        s.store.remove(leg.subscription_id)

    s.legs.ensure([("TRUNK", 214)], NOW + 1)

    _assert_held(s, 214)
    assert s.created == [214, 214]


def test_the_rule_timer_never_idles_a_held_leg() -> None:
    """Long after DEFAULT_UA_TIMER, a held leg is still ACTIVE and its table is not pruned."""
    s = _Legs()
    s.legs.hold("TRUNK", [214], NOW)

    apply_rule_timer_store(s.store, s.config["SYSTEMS"], NOW + 10 * 86400)

    _assert_held(s, 214)


def test_hold_does_not_scan_the_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """A site subscribes up to 64 talkgroups per message at every login: hold, release and ensure go by id."""
    s = _Legs()
    for tg in range(1000, 1200):
        ensure_dynamic_relay_store(s.store, tg, "HOTSPOTS", 2, 10.0, s.config["SYSTEMS"], NOW)
    scans: list[str] = []
    for name in ("snapshot", "list_by_channel", "list_active", "list_by_phase"):
        real = getattr(s.store, name)
        monkeypatch.setattr(s.store, name, lambda *a, _n=name, _r=real: scans.append(_n) or _r(*a))

    s.legs.hold("TRUNK", [214, 1100], NOW)
    s.legs.release("TRUNK", [214], NOW)
    s.legs.ensure([("TRUNK", 214), ("TRUNK", 1100)], NOW)

    assert scans == []
    assert site_tg_held(s.store, 214, "TRUNK") and site_tg_held(s.store, 1100, "TRUNK")
