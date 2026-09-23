# ADN DMR Peer Server - infrastructure subscription store
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

"""In-memory subscription store (Phase 2 runtime routing authority)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from adn_server.application.ports import SubscriptionStore
from adn_server.domain.subscription import (
    AudioChannel,
    Subscription,
    SubscriptionId,
    SubscriptionPhase,
    SystemId,
)

_IndexKey = tuple[str, int, int]


class InMemorySubscriptionStore(SubscriptionStore):
    """Hold subscriptions keyed by ``SubscriptionId``; maintains hot-path indexes."""

    def __init__(self) -> None:
        self._items: dict[SubscriptionId, Subscription] = {}
        self._by_table: dict[str, list[Subscription]] = defaultdict(list)
        self._source_tables: dict[_IndexKey, set[str]] = {}
        self._active_target_counts: dict[_IndexKey, int] = {}
        # What each leg was indexed under. Callers change a leg in place and then
        # upsert it, so by the time it is unindexed it may no longer say where it is.
        self._indexed: dict[SubscriptionId, tuple[str, _IndexKey | None]] = {}
        self._revision = 0

    @property
    def revision(self) -> int:
        """Bumped on every write, so readers can keep what they derived until it moves."""
        return self._revision

    def get(self, sub_id: SubscriptionId) -> Subscription | None:
        return self._items.get(sub_id)

    def upsert(self, subscription: Subscription) -> None:
        old = self._items.get(subscription.subscription_id)
        if old is not None:
            self._unindex(old)
        self._items[subscription.subscription_id] = subscription
        self._index(subscription)
        self._revision += 1

    def remove(self, sub_id: SubscriptionId) -> bool:
        old = self._items.pop(sub_id, None)
        if old is None:
            return False
        self._unindex(old)
        self._revision += 1
        return True

    def clear(self) -> None:
        self._items.clear()
        self._by_table.clear()
        self._source_tables.clear()
        self._active_target_counts.clear()
        self._indexed.clear()
        self._revision += 1

    def replace_all(self, subscriptions: Sequence[Subscription]) -> None:
        self.clear()
        for sub in subscriptions:
            self._items[sub.subscription_id] = sub
            self._index(sub)
        self._revision += 1

    def snapshot(self) -> tuple[Subscription, ...]:
        return tuple(self._items.values())

    def list_by_channel(self, channel: AudioChannel) -> tuple[Subscription, ...]:
        return tuple(sub for sub in self._items.values() if sub.channel == channel)

    def list_by_system(self, system: SystemId) -> tuple[Subscription, ...]:
        return tuple(sub for sub in self._items.values() if sub.system == system)

    def list_active(self) -> tuple[Subscription, ...]:
        return tuple(sub for sub in self._items.values() if sub.is_active())

    def list_by_phase(self, phase: SubscriptionPhase) -> tuple[Subscription, ...]:
        return tuple(sub for sub in self._items.values() if sub.state.phase == phase)

    def relay_tables_with_active_source(
        self,
        system: str,
        slot: int,
        dst_tgid: int,
    ) -> tuple[str, ...]:
        """O(1) lookup: table keys with an ACTIVE source on (system, slot, dst_tgid)."""
        keys = self._source_tables.get((system, int(slot), int(dst_tgid)))
        if not keys:
            return ()
        return tuple(sorted(keys))

    def has_table(self, table_key: str) -> bool:
        """O(1): the table index instead of a scan of every subscription."""
        return bool(self._by_table.get(table_key))

    def legs_in_table(self, table_key: str) -> tuple[Subscription, ...]:
        """All legs for a relay table key (indexed)."""
        return tuple(self._by_table.get(table_key, ()))

    def has_active_target_leg(self, system: str, slot: int, tgid: int) -> bool:
        """True when any ACTIVE leg exists for ``(system, slot, target_tgid)``."""
        return self._active_target_counts.get((system, int(slot), int(tgid)), 0) > 0

    def _index_key(self, sub: Subscription) -> _IndexKey:
        return (sub.system.value, int(sub.channel.slot), int(sub.target_tgid))

    def _index(self, sub: Subscription) -> None:
        table_key = sub.table_key()
        self._by_table[table_key].append(sub)
        key = None
        if sub.is_active():
            key = self._index_key(sub)
            self._source_tables.setdefault(key, set()).add(table_key)
            self._active_target_counts[key] = self._active_target_counts.get(key, 0) + 1
        self._indexed[sub.subscription_id] = (table_key, key)

    def _unindex(self, sub: Subscription) -> None:
        indexed = self._indexed.pop(sub.subscription_id, None)
        if indexed is None:
            return
        table_key, key = indexed
        legs = self._by_table.get(table_key)
        if legs:
            try:
                legs.remove(sub)
            except ValueError:
                pass
            if not legs:
                del self._by_table[table_key]
        if key is not None:
            keys = self._source_tables.get(key)
            if keys is not None:
                keys.discard(table_key)
                if not keys:
                    del self._source_tables[key]
            count = self._active_target_counts.get(key, 0) - 1
            if count <= 0:
                self._active_target_counts.pop(key, None)
            else:
                self._active_target_counts[key] = count
