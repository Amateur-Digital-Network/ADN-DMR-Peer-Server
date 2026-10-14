# ADN DMR Peer Server - plugin event bus
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

"""In-process plugin event bus with time budget and circuit breaker."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ..domain.protocol import ServerPlugin

logger = logging.getLogger(__name__)

DEFAULT_BUDGET_S = 0.0001  # 100 µs per on_event


class PluginBus:
    def __init__(
        self,
        *,
        budget_s: float = DEFAULT_BUDGET_S,
        max_trips: int = 5,
    ) -> None:
        self._handlers: list[Callable[[object], None]] = []
        self._plugins: list[ServerPlugin] = []
        self._budget_s = budget_s
        self._max_trips = max_trips
        self._trip_counts: dict[str, int] = {}
        self._disabled: set[str] = set()
        self._call_later: Callable[..., Any] | None = None
        # Event types some subscriber wants: bridges skip building the rest. A plugin
        # without an ``events`` attribute, or any internal handler, wants everything.
        self._plugin_events: dict[int, frozenset[type] | None] = {}
        self._wants_all = False
        self._wanted: frozenset[type] = frozenset()
        # Deferred events of one reactor tick, delivered by a single call_later.
        self._pending: list[object] = []

    def set_call_later(self, call_later: Callable[..., Any]) -> None:
        self._call_later = call_later

    def register_plugin(self, plugin: ServerPlugin) -> None:
        self._disabled.discard(plugin.name)
        self._trip_counts.pop(plugin.name, None)
        if plugin in self._plugins:
            return
        self._plugins.append(plugin)
        events = getattr(plugin, "events", None)
        self._plugin_events[id(plugin)] = None if events is None else frozenset(events)
        self._update_interest()

    def unregister_plugin(self, plugin: ServerPlugin) -> None:
        self._plugins = [p for p in self._plugins if p is not plugin]
        self._plugin_events.pop(id(plugin), None)
        self._update_interest()

    def subscribe(self, handler: Callable[[object], None]) -> None:
        self._handlers.append(handler)
        self._update_interest()

    def has_subscribers(self) -> bool:
        return bool(self._plugins or self._handlers)

    def wants(self, event_type: type) -> bool:
        """True when some subscriber takes ``event_type``: build the event only then."""
        return self._wants_all or event_type in self._wanted

    def wants_any(self, *event_types: type) -> bool:
        return self._wants_all or not self._wanted.isdisjoint(event_types)

    def _update_interest(self) -> None:
        declared = [self._plugin_events.get(id(p)) for p in self._plugins]
        self._wants_all = bool(self._handlers) or any(events is None for events in declared)
        self._wanted = frozenset().union(*(events for events in declared if events is not None))

    def emit(self, event: object) -> None:
        if not self.has_subscribers():
            return
        for handler in self._handlers:
            try:
                handler(event)
            except Exception:
                logger.exception("(PLUGIN-BUS) handler failed")
        event_type = type(event)
        for plugin in list(self._plugins):
            if plugin.name in self._disabled:
                continue
            events = self._plugin_events.get(id(plugin))
            if events is not None and event_type not in events:
                continue
            try:
                plugin.on_event(event)
            except Exception:
                logger.exception("(PLUGIN-BUS) plugin %s failed", plugin.name)
                self._trip(plugin)

    def emit_deferred(self, event: object) -> None:
        """Emit on the next reactor tick (after forward), batched: one call_later per tick."""
        if not self.wants(type(event)):
            return
        if self._call_later is None:
            self.emit(event)
            return
        self._pending.append(event)
        if len(self._pending) == 1:
            self._call_later(0, self._flush)

    def _flush(self) -> None:
        pending, self._pending = self._pending, []
        for event in pending:
            self.emit(event)

    def _trip(self, plugin: ServerPlugin) -> None:
        count = self._trip_counts.get(plugin.name, 0) + 1
        self._trip_counts[plugin.name] = count
        if count >= self._max_trips:
            logger.error(
                "(PLUGIN-BUS) disabling plugin %s after %d exception trip(s)",
                plugin.name,
                count,
            )
            self._disabled.add(plugin.name)
            try:
                plugin.on_shutdown()
            except Exception:
                logger.exception("(PLUGIN-BUS) shutdown failed for %s", plugin.name)
            self.unregister_plugin(plugin)
