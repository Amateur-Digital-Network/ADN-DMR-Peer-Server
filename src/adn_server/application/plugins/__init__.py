# ADN DMR Peer Server - plugin system package
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

"""Plugin system for adn-server (bus, loader, voice and data events)."""

from .application.manager import PluginManager
from .domain.events import (
    UnitDataEnd,
    UnitDataFrame,
    UnitDataStart,
    VoiceCallEnd,
    VoiceCallFrame,
    VoiceCallStart,
)

__all__ = [
    "PluginManager",
    "UnitDataEnd",
    "UnitDataFrame",
    "UnitDataStart",
    "VoiceCallEnd",
    "VoiceCallFrame",
    "VoiceCallStart",
]
