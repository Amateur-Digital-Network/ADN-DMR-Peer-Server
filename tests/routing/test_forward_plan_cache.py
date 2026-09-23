# ADN DMR Peer Server - tests routing forward plan cache
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

"""A group voice call reuses its forward plan, and the plan follows the store and the config."""

from __future__ import annotations

import copy

from tests.harness.deterministic import (
    DeterministicScenario,
    PacketSpec,
    active_routing_table,
    add_openbridge_system,
    minimal_config,
)

from adn_server.application.subscription.routing_table_import import subscriptions_from_routing_table
from adn_server.domain.subscription import SubscriptionPhase
from adn_server.infrastructure.subscription_store import InMemorySubscriptionStore

TG = 213
OBPS = ("OBP-0", "OBP-1")


def _scenario() -> DeterministicScenario:
    config = minimal_config(("MASTER-A", "MASTER-B"))
    for i, name in enumerate(OBPS):
        add_openbridge_system(config, name)
        config["SYSTEMS"][name]["NETWORK_ID"] = (100 + i).to_bytes(4, "big")
    table = active_routing_table(
        TG, (("MASTER-A", 2), ("MASTER-B", 2)) + tuple((n, 1) for n in OBPS), timeout_minutes=10**6
    )
    sc = DeterministicScenario(config=config, routing_table=table)
    sc.routing.apply_startup_subscriptions()
    return sc


def _call(sc: DeterministicScenario, stream_id: int, bursts: int = 3) -> None:
    base = PacketSpec(dst_id=TG, stream_id=stream_id, slot=2)
    sc.inject_hbp("MASTER-A", DeterministicScenario.voice_head_spec(base))
    for seq in range(1, bursts + 1):
        sc.clock.advance(0.06)
        sc.inject_hbp("MASTER-A", DeterministicScenario.voice_burst_spec(base, seq=seq, dtype_vseq=seq % 6))


def _plan(sc: DeterministicScenario):
    return sc.routing._group_voice_forward_plan(
        system_name="MASTER-A", slot=2, call_type="group", source_is_obp=False,
        bridge_match_slot=2, dst_int=TG,
    )


def _targets(sc: DeterministicScenario) -> set[str]:
    return {p.target_system for p in sc.capture.packets}


def test_frames_of_a_call_share_one_plan() -> None:
    sc = _scenario()
    first = _plan(sc)
    assert _plan(sc) is first
    assert {entry["SYSTEM"] for _, entry in first.entries} == {"MASTER-B", *OBPS}


def test_plan_is_rebuilt_after_a_store_write() -> None:
    sc = _scenario()
    _call(sc, 0x1001)
    assert _targets(sc) == {"MASTER-B", *OBPS}

    store = sc.subscription_store
    before = _plan(sc)
    leg = next(s for s in store.legs_in_table(str(TG)) if s.system.value == "OBP-1")
    leg.state.phase = SubscriptionPhase.IDLE
    store.upsert(leg)

    after = _plan(sc)
    assert after is not before
    assert "OBP-1" not in {entry["SYSTEM"] for _, entry in after.entries}
    sc.capture.packets.clear()
    sc.clock.advance(0.06)
    base = PacketSpec(dst_id=TG, stream_id=0x1001, slot=2)
    sc.inject_hbp("MASTER-A", DeterministicScenario.voice_burst_spec(base, seq=9, dtype_vseq=3))
    assert _targets(sc) == {"MASTER-B", "OBP-0"}


def test_plan_is_rebuilt_when_a_reload_replaces_a_system_block() -> None:
    sc = _scenario()
    before = _plan(sc)
    # config_reload assigns a merged copy per system; the old block is gone.
    sc.config["SYSTEMS"]["OBP-0"] = copy.deepcopy(sc.config["SYSTEMS"]["OBP-0"])
    assert _plan(sc) is not before
    assert _plan(sc) is _plan(sc)


def test_cached_plan_matches_a_fresh_one() -> None:
    sc = _scenario()
    cached = _plan(sc)
    sc.routing._forward_plan_cache.clear()
    fresh = _plan(sc)
    assert fresh is not cached
    assert fresh == cached


def test_has_table_agrees_with_a_full_scan_through_every_write() -> None:
    table = active_routing_table(TG, (("MASTER-A", 2), ("OBP-0", 1)))
    table |= active_routing_table(91, (("MASTER-A", 1),))
    subs = subscriptions_from_routing_table(table)
    store = InMemorySubscriptionStore()

    def agrees() -> None:
        for key in (str(TG), "91", "4000"):
            scan = any(s.table_key() == key for s in store.snapshot())
            assert store.has_table(key) is scan, key

    revisions = [store.revision]
    store.replace_all(subs)
    agrees()
    revisions.append(store.revision)
    store.remove(subs[0].subscription_id)
    agrees()
    revisions.append(store.revision)
    store.upsert(subs[0])
    agrees()
    revisions.append(store.revision)
    for sub in store.legs_in_table("91"):
        store.remove(sub.subscription_id)
    agrees()
    revisions.append(store.revision)
    store.clear()
    agrees()
    revisions.append(store.revision)
    assert revisions == sorted(set(revisions)), "every write moves the revision"


def test_removing_a_missing_subscription_keeps_the_revision() -> None:
    store = InMemorySubscriptionStore()
    subs = subscriptions_from_routing_table(active_routing_table(TG, (("MASTER-A", 2),)))
    before = store.revision
    assert store.remove(subs[0].subscription_id) is False
    assert store.revision == before
