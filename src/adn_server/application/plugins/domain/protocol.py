# ADN DMR Peer Server - plugin contract
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

"""Plugin contract (domain port)."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ServerPlugin(Protocol):
    """Drop-in plugin loaded from plugins/<name>/plugin/."""

    name: str
    # Optional: the event classes this plugin handles, e.g. (VoiceCallFrame, VoiceCallEnd).
    # Declaring them lets the server skip building every other event; without it the
    # plugin receives all of them. Read when the plugin is registered, after on_load.
    # events: tuple[type, ...]

    def on_load(self, bus: Any, config: dict[str, Any], server_ctx: Any) -> None:
        """Subscribe to bus; read plugin config."""

    def on_event(self, event: object) -> None:
        """Reactor thread — O(1), no I/O."""

    def on_reload(self, config: dict[str, Any]) -> None:
        """Optional hot-reload of plugin config."""

    def on_shutdown(self) -> None:
        """Flush buffers; stop workers."""
