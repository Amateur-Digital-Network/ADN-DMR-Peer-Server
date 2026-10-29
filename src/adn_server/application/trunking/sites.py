# ADN DMR Peer Server - application trunking sites
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

"""Tier III sites as the server sees them: which site a frame goes to, its call UUID, and site lifecycle."""

from __future__ import annotations

import logging
from collections.abc import Collection
from typing import Any

from ...domain import bytes_3, int_id
from ...domain.trunking import derive_call_uuid, is_echo_tg
from ..routing.sub_map_learning import forget_sub_map, learn_sub_map, sub_map_heard_at, sub_map_peer
from .legs import SiteLegs
from .ports import TrunkRegistrationStore
from .registry import Registration, Site, SiteRegistry
from .signalling import DmrtSignalling

logger = logging.getLogger(__name__)

TICK_S = 10.0


class TrunkSites:
    def __init__(
        self,
        config: dict[str, Any],
        registry: SiteRegistry,
        legs: SiteLegs,
        signalling: DmrtSignalling,
        store: TrunkRegistrationStore,
        path: str,
    ) -> None:
        self._config = config
        self._registry = registry
        self._legs = legs
        self._signalling = signalling
        self._store = store
        self._path = path
        self._saved = registry.revision
        self._file: list[Registration] | None = None

    def dmrt_received(self, system: str, peer_id: bytes, payload: bytes, now: float) -> None:
        # A reload that fails after binding a new TRUNKING system leaves it listening; its config never went live.
        if system not in self._config["SYSTEMS"]:
            logger.debug(
                "(%s) DMRT from Tier III site %s ignored: the system is not in the running configuration",
                system, int_id(peer_id),
            )
            return
        self._signalling.received(Site(system, peer_id), payload, now)

    def site_logged_in(self, system: str, peer_id: bytes) -> None:
        """Pin the site's radios again: the base login has just purged what SUB_MAP held for this peer."""
        for registration in self._registry.at_site(Site(system, peer_id)):
            ms = bytes_3(registration.ms_id)
            heard = sub_map_heard_at(self._config, ms)
            # A radio heard elsewhere since it registered is there, not here.
            if heard is None or heard <= registration.t:
                learn_sub_map(self._config, system, ms, 1, registration.t, peer_id)

    def site_left(self, system: str, peer_id: bytes, now: float) -> None:
        """Release the talkgroups only this site held; its radios stay registered for when it comes back."""
        self._legs.release(system, self._registry.drop_site(Site(system, peer_id)), now)

    def system_added(self, system: str, now: float) -> None:
        if self._file is None:
            # Once, and on the reactor: no site may log in before its radios are back. Later a write may be on its way.
            self._file = self._store.load(self._path)
        self._registry.restore(self._file, frozenset((system,)), now)

    def system_removed(self, system: str, now: float) -> None:
        released, dropped = self._registry.drop_system(system)
        self._legs.release(system, released, now)
        for registration in dropped:
            forget_sub_map(self._config, bytes_3(registration.ms_id), system, registration.site.peer_id)

    def stream_started(
        self,
        system: str,
        peer_id: bytes,
        stream_id: bytes,
        uuid: bytes,
        rf_src: bytes,
        dst_id: bytes,
        unit: bool,
        now: float,
    ) -> None:
        src = int_id(rf_src)
        self._registry.note_uuid(stream_id, rf_src, uuid, now)
        self._registry.touch(src, Site(system, peer_id), now)
        tg = int_id(dst_id)
        if not unit and is_echo_tg(tg):
            self._registry.note_echo(system, tg, src, peer_id, now)

    def stream_ended(
        self, system: str, peer_id: bytes, rf_src: bytes, dst_id: bytes, unit: bool, now: float
    ) -> None:
        """The parrot plays the over back once it ends, so its keyer is held from here."""
        tg = int_id(dst_id)
        if not unit and is_echo_tg(tg):
            self._registry.note_echo(system, tg, int_id(rf_src), peer_id, now)

    def site_targets(
        self,
        system: str,
        rf_src: bytes,
        dst_id: bytes,
        unit: bool,
        now: float,
        sender: bytes | None = None,
    ) -> Collection[bytes]:
        """The sites of ``system`` that take this frame: never a broadcast for unit frames or the parrot."""
        if unit:
            peer_id = sub_map_peer(self._config, dst_id, system)
            return (peer_id,) if peer_id not in (None, sender) else ()
        tg = int_id(dst_id)
        if is_echo_tg(tg):
            keyer = self._registry.echo_site(system, tg, int_id(rf_src), now)
            return (keyer,) if keyer is not None and keyer != sender else ()
        subscribers = self._registry.subscribers(system, tg)
        if sender is None or sender not in subscribers:
            return subscribers
        return [peer_id for peer_id in subscribers if peer_id != sender]

    def call_uuid(self, stream_id: bytes, rf_src: bytes, now: float) -> bytes:
        """The UUID a site gave the stream, or one derived from it that stays the same for the whole call."""
        uuid = self._registry.uuid(stream_id, rf_src)
        if uuid is None:
            uuid = derive_call_uuid(stream_id)
            self._registry.note_uuid(stream_id, rf_src, uuid, now)
        return uuid

    def tick(self, now: float) -> None:
        for ms_id, site, _ in self._registry.expire(now):
            forget_sub_map(self._config, bytes_3(ms_id), site.system, site.peer_id)
        self._registry.trim(now)
        self._legs.ensure(self._registry.held(), now)

    def persist_if_changed(self) -> bool:
        # A write fails in the store's thread, after save() returned: make it again rather than wait for a change.
        if self._registry.revision == self._saved and not self._store.last_write_failed():
            return False
        self.save()
        return True

    def save(self) -> None:
        revision = self._registry.revision
        self._file = self._registry.registrations()
        self._store.save(self._path, self._file)
        self._saved = revision
