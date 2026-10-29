# ADN DMR Peer Server - application trunking legs
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

"""Store legs behind Tier III subscriptions: armed by a TG's first site, idled by its last, re-armed after a reset."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from ..ports import SubscriptionStore
from ..subscription.site_tg_ops import hold_site_tg_store, site_tg_held
from ..subscription.subscription_table_ops import reset_static_tg_store

logger = logging.getLogger(__name__)


class SiteLegs:
    def __init__(
        self,
        store: SubscriptionStore,
        config: dict[str, Any],
        on_table_created: Callable[[int], None],
        on_changed: Callable[[], None],
    ) -> None:
        self._store = store
        self._config = config
        self._on_table_created = on_table_created
        self._on_changed = on_changed

    def hold(self, system: str, tgs: Sequence[int], now: float) -> None:
        if not tgs:
            return
        for tg in tgs:
            self._hold(system, tg, now)
        self._on_changed()

    def release(self, system: str, tgs: Sequence[int], now: float) -> None:
        if not tgs:
            return
        tmout = self._tmout(system)
        for tg in tgs:
            for ts in (1, 2):
                reset_static_tg_store(self._store, tg, ts, tmout, system, now)
        self._on_changed()

    def ensure(self, held: Iterable[tuple[str, int]], now: float) -> None:
        """Re-arm what sites still hold: a BRIDGERESET from a refused login can idle legs a site armed since."""
        rearmed = False
        for system, tg in held:
            if site_tg_held(self._store, tg, system):
                continue
            self._hold(system, tg, now)
            logger.info("(%s) Tier III talkgroup %s re-armed after a bridge reset", system, tg)
            rearmed = True
        if rearmed:
            self._on_changed()

    def _hold(self, system: str, tg: int, now: float) -> None:
        systems = self._config["SYSTEMS"]
        if hold_site_tg_store(self._store, tg, system, self._tmout(system), systems, now):
            self._on_table_created(tg)

    def _tmout(self, system: str) -> float:
        return float(self._config["SYSTEMS"][system].get("DEFAULT_UA_TIMER", 10))
