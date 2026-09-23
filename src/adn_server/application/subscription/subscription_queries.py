# ADN DMR Peer Server - application subscription subscription queries
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

"""Read-only queries on ``SubscriptionStore`` (runtime routing authority)."""

from __future__ import annotations

from adn_server.application.ports import SubscriptionStore


def store_has_table(store: SubscriptionStore, table_key: str) -> bool:
    """True when the store has at least one leg in ``table_key``.

    Indexed: this runs per datagram, and the scan it replaces was building a tuple
    of every subscription to answer a yes/no question.
    """
    return store.has_table(table_key)


def system_has_active_leg_in_store(
    store: SubscriptionStore,
    system: str,
    slot: int,
    tgid: int,
) -> bool:
    """True when an ACTIVE subscription leg exists for ``(system, slot, tgid)``."""
    return store.has_active_target_leg(system, slot, tgid)


def active_system_slots_for_tg_in_store(
    store: SubscriptionStore,
    system: str,
    tgid: int,
) -> tuple[int, ...]:
    """Return sorted unique slots with an ACTIVE leg for ``system`` on bridge table ``tgid``."""
    slots: list[int] = []
    table_key = str(tgid)
    for sub in store.snapshot():
        if sub.table_key() != table_key:
            continue
        if sub.system.value != system:
            continue
        if not sub.is_active():
            continue
        slot = int(sub.channel.slot)
        if slot not in slots:
            slots.append(slot)
    return tuple(sorted(slots))
