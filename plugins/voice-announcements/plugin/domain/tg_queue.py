# ADN DMR Peer Server plugin - voice-announcements talkgroup queue
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

"""One playback at a time per talkgroup; the rest wait their turn, in order."""

from __future__ import annotations

from typing import Generic, TypeVar

T = TypeVar("T")


class TalkgroupQueue(Generic[T]):
    def __init__(self) -> None:
        self._on_air: set[int] = set()
        self._waiting: list[tuple[int, T]] = []

    def on_air(self, tg: int) -> bool:
        return tg in self._on_air

    def request(self, tg: int, job: T) -> bool:
        """True: start ``job`` now (its TG is free). False: queued behind the one on air."""
        if tg in self._on_air:
            self._waiting.append((tg, job))
            return False
        self._on_air.add(tg)
        return True

    def finished(self, tg: int) -> T | None:
        """Free ``tg``; the next waiting job whose TG is free, now marked on air."""
        self._on_air.discard(tg)
        for i, (waiting_tg, job) in enumerate(self._waiting):
            if waiting_tg not in self._on_air:
                del self._waiting[i]
                self._on_air.add(waiting_tg)
                return job
        return None

    def __len__(self) -> int:
        return len(self._waiting)
