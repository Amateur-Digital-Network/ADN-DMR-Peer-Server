# ADN DMR Peer Server - application trunking recent
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

"""Keys noted within a time window: a note drops only the expired ones, oldest first, and their number is capped."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Hashable

# A flood of distinct keys forgets the oldest instead of growing.
MAX_RECENT_KEYS = 256


class RecentKeys:
    def __init__(self, window_s: float) -> None:
        self._window_s = window_s
        self._noted: OrderedDict[Hashable, float] = OrderedDict()

    def note(self, key: Hashable, now: float) -> bool:
        """False when ``key`` was already noted within the window."""
        noted = self._noted
        while noted:
            oldest, t = next(iter(noted.items()))
            if now - t < self._window_s:
                break
            del noted[oldest]
        if key in noted:
            return False
        if len(noted) >= MAX_RECENT_KEYS:
            noted.popitem(last=False)
        noted[key] = now
        return True
