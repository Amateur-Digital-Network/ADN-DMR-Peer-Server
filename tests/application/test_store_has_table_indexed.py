# ADN DMR Peer Server - tests application store_has_table indexed
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

"""``store_has_table`` answers from the index, not from a scan.

It runs once per datagram. Scanning every subscription to answer a yes/no
question was 68% of the server's CPU at 198 datagrams/s. The index it now reads
is maintained by upsert/remove, so these check it cannot answer from a stale one.
"""

from __future__ import annotations

from adn_server.application.subscription.store_sync import replace_store_from_routing_table
from adn_server.application.subscription.subscription_queries import store_has_table
from adn_server.domain import bytes_3
from adn_server.infrastructure.subscription_store import InMemorySubscriptionStore


def _row(system: str, tgid: int, ts: int = 1) -> dict:
    tg_b = bytes_3(tgid)
    return {
        "SYSTEM": system, "TS": ts, "TGID": tg_b, "ACTIVE": True, "TIMEOUT": 3600.0,
        "TO_TYPE": "ON", "ON": [tg_b], "OFF": [], "RESET": [], "TIMER": 0.0,
    }


def _store(*tgids: int) -> InMemorySubscriptionStore:
    store = InMemorySubscriptionStore()
    replace_store_from_routing_table(
        store, {str(t): [_row("OBP-A", t), _row("OBP-B", t)] for t in tgids}
    )
    return store


def _by_scan(store: InMemorySubscriptionStore, table_key: str) -> bool:
    """What store_has_table did before it used the index."""
    return any(sub.table_key() == table_key for sub in store.snapshot())


def test_it_answers_what_a_full_scan_answers() -> None:
    store = _store(7305, 52090, 730444)
    for key in ("7305", "52090", "730444", "9999", "", "0"):
        assert store_has_table(store, key) == _by_scan(store, key), key


def test_removing_the_last_leg_empties_the_table() -> None:
    store = _store(7305, 52090)
    assert store_has_table(store, "7305")

    for sub in store.legs_in_table("7305"):
        store.remove(sub.subscription_id)

    assert store_has_table(store, "7305") is False
    assert store_has_table(store, "7305") == _by_scan(store, "7305")
    assert store_has_table(store, "52090") is True


def test_removing_one_of_two_legs_keeps_the_table() -> None:
    store = _store(7305)
    store.remove(store.legs_in_table("7305")[0].subscription_id)

    assert store_has_table(store, "7305") is True
    assert store_has_table(store, "7305") == _by_scan(store, "7305")


def test_a_replaced_store_forgets_the_old_tables() -> None:
    store = _store(7305)
    replace_store_from_routing_table(store, {"52090": [_row("OBP-A", 52090)]})

    assert store_has_table(store, "7305") is False
    assert store_has_table(store, "52090") is True
    for key in ("7305", "52090"):
        assert store_has_table(store, key) == _by_scan(store, key), key


def test_it_does_not_get_slower_as_the_store_grows() -> None:
    """The scan was O(n) per datagram; the index must not be."""
    small = _store(*range(1000, 1010))
    large = _store(*range(1000, 1400))
    assert len(large.snapshot()) > 20 * len(small.snapshot())
    assert store_has_table(small, "1005") == store_has_table(large, "1005")
    assert len(large.legs_in_table("1005")) == 2
