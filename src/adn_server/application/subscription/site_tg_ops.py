# ADN DMR Peer Server - application subscription site tg ops
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

"""A Tier III site's talkgroup as STATIC legs of its system, written by id (no store scan)."""

from __future__ import annotations

from typing import Any

from adn_server.application.ports import SubscriptionStore
from adn_server.application.subscription.subscription_table_ops import ensure_dynamic_relay_store, upsert_static_leg
from adn_server.domain.subscription import ActivationPolicy, AudioChannel, SubscriptionId, SystemId, TgId


def hold_site_tg_store(
    store: SubscriptionStore,
    tg: int,
    system: str,
    tmout: float,
    systems_cfg: dict[str, Any],
    now: float,
) -> bool:
    """STATIC ACTIVE legs on TS1 and TS2; True when the TG table had to be created."""
    created = not store.has_table(str(tg))
    if created:
        ensure_dynamic_relay_store(store, tg, system, 1, tmout, systems_cfg, now)
    for ts in (1, 2):
        upsert_static_leg(
            store,
            system=system,
            tg=tg,
            slot=ts,
            active=True,
            timeout_sec=tmout * 60.0,
            timer_at=now + tmout * 60.0,
        )
    return created


def site_tg_held(store: SubscriptionStore, tg: int, system: str) -> bool:
    """True when both legs are still STATIC and ACTIVE (a bridge reset turns them INBAND idle)."""
    for ts in (1, 2):
        leg = store.get(SubscriptionId(AudioChannel(TgId(tg), ts), SystemId(system)))
        if leg is None or leg.policy != ActivationPolicy.STATIC or not leg.is_active():
            return False
    return True
