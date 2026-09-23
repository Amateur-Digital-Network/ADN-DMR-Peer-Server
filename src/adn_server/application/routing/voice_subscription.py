# ADN DMR Peer Server - application routing voice subscription
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
#
# Derived from ADN DMR Server / FreeDMR / HBlink. Original license:
###############################################################################
# Copyright (C) 2026 Joaquin Madrid Belando, EA5GVK <ea5gvk@gmail.com>
# Copyright (C) 2020 Simon Adlem, G7RZU <g7rzu@gb7fr.org.uk>
# Copyright (C) 2016-2019 Cortney T. Buffington, N0MJS <n0mjs@me.com>
#
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

"""Subscription router helpers for the voice hot path."""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

from ...domain.value_objects import bytes_3
from ...domain.voice_routing import ForwardLeg, VoiceIngress
from ..ports import SubscriptionStore
from ..subscription.ingress import build_voice_ingress
from ..subscription.router import SubscriptionRouter

logger = logging.getLogger(__name__)

# Same set as ``build_voice_ingress``: other call types resolve to no legs.
_BRIDGE_CALL_TYPES = frozenset({"group", "vcsbk"})
_FORWARD_PLAN_CACHE_MAX = 4096


class ForwardPlan(NamedTuple):
    """Where one group voice frame goes, ready for the forwarding loop."""

    tables: tuple[str, ...]
    legs: tuple[ForwardLeg, ...]
    # (relay table key, target entry) per leg; shared between frames, read only.
    entries: tuple[tuple[str, dict[str, Any]], ...]


class _CachedPlan(NamedTuple):
    revision: int
    # Every SYSTEMS block the plan read, as the object it read: a reload
    # replaces the block, which is what makes the plan stale.
    blocks: tuple[tuple[str, Any], ...]
    plan: ForwardPlan


class VoiceSubscriptionMixin:
    """Wire ``SubscriptionRouter`` into ``dmrd_received``."""

    _subscription_store: SubscriptionStore
    _subscription_router: SubscriptionRouter | None

    def _subscription_router_instance(self) -> SubscriptionRouter:
        router = getattr(self, "_subscription_router", None)
        if router is None:
            router = SubscriptionRouter(self._subscription_store)
            self._subscription_router = router
        return router

    def _build_dmrd_voice_ingress(
        self,
        *,
        system_name: str,
        peer_id: bytes,
        rf_src: bytes,
        dst_id: bytes,
        slot: int,
        call_type: str,
        stream_id: bytes,
        source_is_obp: bool,
    ) -> VoiceIngress | None:
        mode = "OPENBRIDGE" if source_is_obp else self._config.get("SYSTEMS", {}).get(system_name, {}).get("MODE", "")
        return build_voice_ingress(
            source_system=system_name,
            system_mode=mode if isinstance(mode, str) else "",
            peer_id=peer_id,
            rf_src=rf_src,
            dst_id=dst_id,
            slot=slot,
            call_type=call_type,
            stream_id=stream_id,
        )

    def _group_voice_forward_plan(
        self,
        *,
        system_name: str,
        slot: int,
        call_type: str,
        source_is_obp: bool,
        bridge_match_slot: int,
        dst_int: int,
    ) -> ForwardPlan:
        """The forward plan of a group voice frame, rebuilt only when it can differ.

        It depends on the subscription store, the source and target SYSTEMS blocks
        and on nothing in the frame beyond the key, so each frame of a call reuses
        the plan of the first until the store is written or a block is reloaded.
        Per-frame checks (ENABLED, quench, keepalive, contention) stay in the loop.
        """
        systems = self._config.get("SYSTEMS", {})
        revision = getattr(self._subscription_store, "revision", None)
        src_block = systems.get(system_name)
        mode = "OPENBRIDGE" if source_is_obp else (src_block or {}).get("MODE", "")
        routable = call_type in _BRIDGE_CALL_TYPES
        # VoiceIngress.bridge_match_slot, which is what resolve() matches on.
        match_slot = 1 if mode == "OPENBRIDGE" else (1 if int(slot) == 1 else 2)
        key = (system_name, bridge_match_slot, match_slot, dst_int, mode == "OPENBRIDGE", routable)
        cache: dict[tuple, _CachedPlan] | None = getattr(self, "_forward_plan_cache", None)
        if cache is None:
            cache = self._forward_plan_cache = {}
        hit = cache.get(key) if revision is not None else None
        if (
            hit is not None
            and hit.revision == revision
            and all(systems.get(name) is block for name, block in hit.blocks)
        ):
            return hit.plan

        tables, legs = self._voice_forward_plan(
            system_name=system_name,
            peer_id=b"",
            rf_src=b"",
            dst_id=dst_int.to_bytes(3, "big"),
            slot=slot,
            call_type=call_type,
            stream_id=b"",
            source_is_obp=source_is_obp,
            bridge_match_slot=bridge_match_slot,
            dst_int=dst_int,
        )
        # One leg per (target, translated TGID) on MASTER/PEER targets: their
        # send_peers() picks each peer's slot itself, so a second leg is the same
        # audio twice. OpenBridge targets keep per-slot legs (separate links).
        seen_hbp: set[tuple[str, int]] = set()
        deduped = []
        for leg in legs:
            if systems.get(leg.target_system, {}).get("MODE") != "OPENBRIDGE":
                hbp_key = (leg.target_system, int(leg.target_tgid))
                if hbp_key in seen_hbp:
                    continue
                seen_hbp.add(hbp_key)
            deduped.append(leg)
        table_key = tables[0] if tables else str(dst_int)
        entries = tuple(
            (
                table_key,
                {
                    "SYSTEM": leg.target_system,
                    "TS": int(leg.slot),
                    "TGID": bytes_3(int(leg.target_tgid)),
                    "ACTIVE": True,
                },
            )
            for leg in deduped
        )
        plan = ForwardPlan(tables, tuple(deduped), entries)
        if revision is not None:
            names = {system_name, *(leg.target_system for leg in legs)}
            if len(cache) >= _FORWARD_PLAN_CACHE_MAX:
                cache.clear()
            cache[key] = _CachedPlan(revision, tuple((n, systems.get(n)) for n in names), plan)
        return plan

    def _voice_relay_tables_with_active_source(
        self,
        system_name: str,
        bridge_match_slot: int,
        dst_int: int,
    ) -> tuple[str, ...]:
        tables, _ = self._voice_forward_plan(
            system_name=system_name,
            peer_id=b"",
            rf_src=b"",
            dst_id=b"\x00\x00\x00",
            slot=bridge_match_slot,
            call_type="group",
            stream_id=b"",
            source_is_obp=False,
            bridge_match_slot=bridge_match_slot,
            dst_int=dst_int,
            ingress_required=False,
        )
        return tables

    def _voice_forward_plan(
        self,
        *,
        system_name: str,
        peer_id: bytes,
        rf_src: bytes,
        dst_id: bytes,
        slot: int,
        call_type: str,
        stream_id: bytes,
        source_is_obp: bool,
        bridge_match_slot: int,
        dst_int: int,
        ingress_required: bool = True,
    ) -> tuple[tuple[str, ...], tuple[ForwardLeg, ...]]:
        """Return bridge tables and resolved forward legs from the subscription store."""
        router = self._subscription_router_instance()
        tables = router.relay_tables_with_active_source(system_name, bridge_match_slot, dst_int)
        if not ingress_required:
            return tables, ()
        ingress = self._build_dmrd_voice_ingress(
            system_name=system_name,
            peer_id=peer_id,
            rf_src=rf_src,
            dst_id=dst_id,
            slot=slot,
            call_type=call_type,
            stream_id=stream_id,
            source_is_obp=source_is_obp,
        )
        if ingress is None:
            return tables, ()
        return tables, router.resolve(ingress)
