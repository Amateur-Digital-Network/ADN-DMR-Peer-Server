# ADN DMR Peer Server - tests plugin bus
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

"""Plugin bus circuit breaker behaviour."""

from __future__ import annotations

import time
from typing import Any

from adn_server.application.plugins.application.bus import PluginBus


class _SlowPlugin:
    name = "slow"

    def on_event(self, event: object) -> None:
        time.sleep(0.002)

    def on_shutdown(self) -> None:
        pass


class _BrokenPlugin:
    name = "broken"

    def on_event(self, event: object) -> None:
        raise RuntimeError("boom")

    def on_shutdown(self) -> None:
        pass


def test_plugin_bus_does_not_disable_on_budget_overrun() -> None:
    bus = PluginBus(budget_s=0.0001, max_trips=2)
    plugin = _SlowPlugin()
    bus.register_plugin(plugin)
    for _ in range(5):
        bus.emit("evt")
    assert plugin in bus._plugins  # noqa: SLF001
    assert "slow" not in bus._disabled  # noqa: SLF001


def test_plugin_bus_disables_on_exception() -> None:
    bus = PluginBus(max_trips=2)
    plugin = _BrokenPlugin()
    bus.register_plugin(plugin)
    for _ in range(2):
        bus.emit("evt")
    assert plugin not in bus._plugins  # noqa: SLF001
    assert "broken" in bus._disabled  # noqa: SLF001


def test_plugin_bus_re_register_after_disable() -> None:
    bus = PluginBus(max_trips=1)
    plugin = _BrokenPlugin()
    bus.register_plugin(plugin)
    bus.emit("evt")
    assert "broken" in bus._disabled  # noqa: SLF001
    assert plugin not in bus._plugins  # noqa: SLF001

    plugin2 = _BrokenPlugin()
    bus.register_plugin(plugin2)
    assert "broken" not in bus._disabled  # noqa: SLF001
    assert plugin2 in bus._plugins  # noqa: SLF001
