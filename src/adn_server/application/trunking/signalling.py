# ADN DMR Peer Server - application trunking signalling
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

"""Tier III radio registrations and talkgroup subscriptions, as a site asks for them over DMRT."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from ...domain import bytes_3, int_id
from ...domain.trunking import (
    DmrtOp,
    DmrtRegistration,
    DmrtSubscription,
    deregistration,
    parse_dmrt,
    registration_confirmation,
    subscribable_tg,
    subscription_confirmation,
)
from ..ports import AclRouter
from ..routing.sub_map_learning import forget_sub_map, learn_sub_map
from .legs import SiteLegs
from .ports import SiteLink
from .recent import RecentKeys
from .registry import MAX_TGS_PER_SITE, Site, SiteRegistry

logger = logging.getLogger(__name__)

_IGNORED_LOG_S = 60.0


class DmrtSignalling:
    """What the server answers to a site's DMRT (0x01-0x04)."""

    def __init__(
        self,
        config: dict[str, Any],
        registry: SiteRegistry,
        legs: SiteLegs,
        link: SiteLink,
        acl: AclRouter,
    ) -> None:
        self._config = config
        self._registry = registry
        self._legs = legs
        self._link = link
        self._acl = acl
        self._ignored = RecentKeys(_IGNORED_LOG_S)

    def received(self, site: Site, payload: bytes, now: float) -> None:
        msg = parse_dmrt(payload)
        if msg is None:
            self._ignore(site, payload, now)
        elif isinstance(msg, DmrtRegistration):
            if msg.op == DmrtOp.REGISTRATION:
                self._register(site, msg.ms_ids, now)
            else:
                self._deregister(site, msg.ms_ids, now)
        elif isinstance(msg, DmrtSubscription):
            if msg.op == DmrtOp.GROUP_SUBSCRIPTION:
                self._subscribe(site, msg.tgs, now)
            else:
                self._unsubscribe(site, msg.tgs, now)

    def _register(self, site: Site, ms_ids: Sequence[int], now: float) -> None:
        scopes = acl_scopes(self._config, site.system)
        accepted: list[bool] = []
        for ms in ms_ids:
            ok = ms != 0 and sub_permitted(self._acl, scopes, ms)
            if ok:
                ok, previous = self._registry.register(ms, site, now)
                if ok:
                    learn_sub_map(self._config, site.system, bytes_3(ms), 1, now, site.peer_id)
                    if previous is not None and self._link.connected(previous):
                        self._link.send(previous, deregistration([ms], int(now)))
            accepted.append(ok)
        self._link.send(site, registration_confirmation(accepted, ms_ids, int(now)))
        logger.info(
            "(%s) Tier III site %s registered %s, refused %s",
            site.system,
            int_id(site.peer_id),
            [ms for ms, ok in zip(ms_ids, accepted) if ok],
            [ms for ms, ok in zip(ms_ids, accepted) if not ok],
        )

    def _deregister(self, site: Site, ms_ids: Sequence[int], now: float) -> None:
        gone = [ms for ms in ms_ids if self._registry.deregister(ms, site)]
        for ms in gone:
            forget_sub_map(self._config, bytes_3(ms), site.system, site.peer_id)
        self._link.send(site, deregistration(ms_ids, int(now)))
        logger.info(
            "(%s) Tier III site %s de-registered %s, not registered there %s",
            site.system,
            int_id(site.peer_id),
            gone,
            [ms for ms in ms_ids if ms not in gone],
        )

    def _subscribe(self, site: Site, tgs: Sequence[int], now: float) -> None:
        scopes = acl_scopes(self._config, site.system)
        confirmed: list[int] = []
        to_hold: list[int] = []
        for tg in tgs:
            ok = (
                subscribable_tg(tg)
                and self._tg_permitted(scopes, tg)
                and (self._registry.is_subscribed(site, tg) or self._registry.tg_count(site) < MAX_TGS_PER_SITE)
            )
            if ok and self._registry.subscribe(site, tg):
                to_hold.append(tg)
            confirmed.append(tg if ok else 0)
        self._legs.hold(site.system, to_hold, now)
        self._link.send(site, subscription_confirmation(DmrtOp.GROUP_SUBSCRIPTION, confirmed))
        logger.info(
            "(%s) Tier III site %s subscribed %s, refused %s",
            site.system,
            int_id(site.peer_id),
            [tg for tg in confirmed if tg],
            [tg for tg, ok in zip(tgs, confirmed) if not ok],
        )

    def _unsubscribe(self, site: Site, tgs: Sequence[int], now: float) -> None:
        self._legs.release(site.system, [tg for tg in tgs if self._registry.unsubscribe(site, tg)], now)
        self._link.send(site, subscription_confirmation(DmrtOp.GROUP_UNSUBSCRIPTION, tgs))
        logger.info("(%s) Tier III site %s unsubscribed %s", site.system, int_id(site.peer_id), list(tgs))

    def _ignore(self, site: Site, payload: bytes, now: float) -> None:
        if not self._ignored.note((site, payload[4] if len(payload) > 4 else -1), now):
            return
        logger.debug(
            "(%s) DMRT of %d bytes (opcode %s) from Tier III site %s ignored: malformed or not handled",
            site.system,
            len(payload),
            payload[4:5].hex() or "none",
            int_id(site.peer_id),
        )

    def _tg_permitted(self, scopes: tuple[dict[str, Any], ...], tg: int) -> bool:
        tg_b = bytes_3(tg)
        # Both slots: dmrtc sets the slot bit of its DMRD freely, and the uplink ACL checks the TG on that slot.
        return all(
            self._acl.acl_check(tg_b, cfg.get("TG1_ACL", (True, [])))
            and self._acl.acl_check(tg_b, cfg.get("TG2_ACL", (True, [])))
            for cfg in scopes
        )


def acl_scopes(config: dict[str, Any], system: str) -> tuple[dict[str, Any], ...]:
    """GLOBAL and the system's block, each only when its USE_ACL is set (as for DMRD)."""
    scopes = (config.get("GLOBAL", {}), config["SYSTEMS"][system])
    return tuple(cfg for cfg in scopes if cfg.get("USE_ACL"))


def sub_permitted(acl: AclRouter, scopes: tuple[dict[str, Any], ...], ms: int) -> bool:
    ms_b = bytes_3(ms)
    return all(acl.acl_check(ms_b, cfg.get("SUB_ACL", (True, []))) for cfg in scopes)
