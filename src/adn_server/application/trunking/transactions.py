# ADN DMR Peer Server - application trunking transactions
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

"""EXPERIMENTAL DMRT short data, status and private-call setup between Tier III sites (no dmrtc field use yet)."""

from __future__ import annotations

import logging
from typing import Any

from ...domain import int_id
from ...domain.trunking import (
    DmrtCallSetup,
    DmrtMessage,
    DmrtOp,
    DmrtReply,
    DmrtStatus,
    DmrtUdt,
    call_reject,
    to_network,
)
from ...domain.value_objects import TgId
from ...domain.voice_routing import VoiceIngress
from ..ports import AclRouter
from ..subscription.router import SubscriptionRouter
from .ports import SiteLink
from .recent import RecentKeys
from .registry import Site, SiteRegistry
from .signalling import acl_scopes, sub_permitted

logger = logging.getLogger(__name__)

_DROP_LOG_S = 60.0
_ANSWERS = {DmrtOp.UDT_ACCEPT: DmrtOp.UDT, DmrtOp.CALL_ACCEPT: DmrtOp.CALL_SETUP, DmrtOp.CALL_REJECT: DmrtOp.CALL_SETUP}


class DmrtTransactions:
    """Where a site's 0x05-0x0A go: the source must be registered there, group messages follow the bridges."""

    def __init__(
        self,
        config: dict[str, Any],
        registry: SiteRegistry,
        link: SiteLink,
        acl: AclRouter,
        router: SubscriptionRouter,
    ) -> None:
        self._config = config
        self._registry = registry
        self._link = link
        self._acl = acl
        self._router = router
        self._dropped = RecentKeys(_DROP_LOG_S)

    def received(self, site: Site, msg: DmrtMessage, payload: bytes, now: float) -> None:
        if isinstance(msg, DmrtReply):
            self._answer(site, msg, payload, now)
        elif isinstance(msg, (DmrtUdt, DmrtCallSetup, DmrtStatus)):
            self._transfer(site, msg, payload, now)

    def _transfer(self, site: Site, msg: DmrtUdt | DmrtCallSetup | DmrtStatus, payload: bytes, now: float) -> None:
        op = payload[4]
        if self._registry.location(msg.src, now) != site or not sub_permitted(
            self._acl, acl_scopes(self._config, site.system), msg.src,
        ):
            self._drop(site, op, msg.src, now)
            return
        open_tx = self._registry.pending(msg.uuid, DmrtOp(op), now)
        # Every target has seen the UUID: none of them may take over the answers to it.
        if open_tx is not None and open_tx.origin != site:
            logger.debug(
                "(%s) EXPERIMENTAL DMRT 0x%02x from site %s ignored: its UUID is open from another site",
                site.system, op, int_id(site.peer_id),
            )
            return
        if isinstance(msg, DmrtCallSetup) or not msg.group:
            target = self._registry.location(msg.dst, now)
            targets = {target} if target is not None and target != site and self._link.connected(target) else set()
        else:
            targets = self._group_targets(site, msg.dst)
        if not targets:
            if isinstance(msg, DmrtCallSetup):
                self._link.send(site, call_reject(msg.uuid, msg.src, msg.dst))
                logger.info(
                    "(%s) EXPERIMENTAL DMRT call setup from site %s, SUB %s to %s: not at another site, rejected",
                    site.system, int_id(site.peer_id), msg.src, msg.dst,
                )
            else:
                logger.debug(
                    "(%s) EXPERIMENTAL DMRT 0x%02x from site %s, SUB %s to %s: no site to deliver to",
                    site.system, op, int_id(site.peer_id), msg.src, msg.dst,
                )
            return
        if not isinstance(msg, DmrtStatus) and not self._registry.note_pending(
            msg.uuid, DmrtOp(op), site, frozenset(targets), now,
        ):
            if isinstance(msg, DmrtCallSetup):
                self._link.send(site, call_reject(msg.uuid, msg.src, msg.dst))
            if self._dropped.note((site, op), now):
                logger.info(
                    "(%s) EXPERIMENTAL DMRT 0x%02x from site %s refused: too many open transactions",
                    site.system, op, int_id(site.peer_id),
                )
            return
        forwarded = to_network(payload)
        for target in targets:
            self._link.send(target, forwarded)
        logger.info(
            "(%s) EXPERIMENTAL DMRT 0x%02x from site %s, SUB %s to %s: forwarded to %s",
            site.system, op, int_id(site.peer_id), msg.src, msg.dst,
            sorted(f"{t.system}/{int_id(t.peer_id)}" for t in targets),
        )

    def _group_targets(self, site: Site, tg: int) -> set[Site]:
        """The sites the voice bridges of ``tg`` would reach from ``site``'s system, plus its own when REPEAT is on."""
        targets: set[Site] = set()
        if self._config["SYSTEMS"][site.system].get("REPEAT", True):
            targets.update(Site(site.system, p) for p in self._registry.subscribers(site.system, tg))
        for slot in (1, 2):
            for leg in self._router.resolve(VoiceIngress(source_system=site.system, slot=slot, dst_tgid=TgId(tg))):
                # The message carries its talkgroup: a leg that translates it cannot take it.
                if int(leg.target_tgid) == tg:
                    targets.update(
                        Site(leg.target_system, p) for p in self._registry.subscribers(leg.target_system, tg)
                    )
        targets.discard(site)
        return targets

    def _answer(self, site: Site, msg: DmrtReply, payload: bytes, now: float) -> None:
        pending = self._registry.pending(msg.uuid, _ANSWERS[msg.op], now)
        if pending is None or site not in pending.targets:
            logger.debug(
                "(%s) EXPERIMENTAL DMRT 0x%02x from site %s ignored: no open transaction sent there",
                site.system, payload[4], int_id(site.peer_id),
            )
            return
        self._link.send(pending.origin, to_network(payload))
        logger.info(
            "(%s) EXPERIMENTAL DMRT 0x%02x from site %s returned to %s/%s",
            site.system, payload[4], int_id(site.peer_id), pending.origin.system, int_id(pending.origin.peer_id),
        )

    def _drop(self, site: Site, op: int, src: int, now: float) -> None:
        if not self._dropped.note((site, op, src), now):
            return
        logger.info(
            "(%s) EXPERIMENTAL DMRT 0x%02x from site %s: SUB %s not registered there or denied by SUB_ACL, dropped",
            site.system, op, int_id(site.peer_id), src,
        )
