# ADN DMR Peer Server - SUB_MAP persistence
# Copyright (C) 2026  Rodrigo Pérez, CE5RPY <ce5rpy@qmd.cl>
#
# Derived from ADN DMR Server / FreeDMR  / HBlink. Original license:
###############################################################################
# Copyright (C) 2020 Simon Adlem, G7RZU <g7rzu@gb7fr.org.uk>
# Copyright (C) 2016-2019 Cortney T. Buffington, N0MJS <n0mjs@me.com>
#
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

"""Load/save SUB_MAP (pickle): bytes_3(peer) -> (callsign, slot, time)."""

from __future__ import annotations

import itertools
import os
import pickle
import threading
from collections.abc import Hashable
from pathlib import Path

from ...application.ports import SubMapStore

# A SIGHUP handler runs on the main thread, so it can interrupt a save already in
# progress there: pid and thread alone would give both writers the same temp file.
_tmp_seq = itertools.count()


class PickleSubMapStore(SubMapStore):
    """Persist SUB_MAP as pickle (legacy compatible)."""

    def load(self, path: str) -> dict[bytes, tuple[str, int, float]]:
        """Load SUB_MAP from pickle file."""
        p = Path(path)
        if not p.is_file():
            return {}
        try:
            with open(p, "rb") as f:
                return pickle.load(f)
        except (pickle.PickleError, OSError):
            return {}

    def save(self, path: str, sub_map: dict[bytes, tuple[str, int, float]]) -> None:
        """Save SUB_MAP to pickle file."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        # Write aside and rename: a crash mid-dump must not leave a truncated
        # file, which load() would turn into an empty map.
        tmp = p.with_name(f"{p.name}.tmp.{os.getpid()}.{threading.get_ident()}.{next(_tmp_seq)}")
        try:
            with open(tmp, "wb") as f:
                pickle.dump(sub_map, f)
            tmp.replace(p)
        finally:
            tmp.unlink(missing_ok=True)


# SUB_MAP is rewritten on every frame, but only the route of an entry (system,
# slot, peer) matters after a restart. The timestamp only feeds the 24h trim, so
# it counts as changed once per hour, as often as the old hourly save wrote it.
_TIME_BUCKET_S = 3600


def _route_snapshot(sub_map: dict) -> dict[bytes, Hashable]:
    return {k: (v[0], v[1], v[3] if len(v) > 3 else None, int(v[2] // _TIME_BUCKET_S)) for k, v in sub_map.items()}


class SubMapSaver:
    """Write SUB_MAP to disk when its routes changed since the last write."""

    def __init__(self, store: SubMapStore, path: str, sub_map: dict):
        self._store = store
        self._path = path
        self._sub_map = sub_map
        self._saved = _route_snapshot(sub_map)

    def save(self) -> None:
        """Write unconditionally (shutdown, SIGHUP)."""
        self._store.save(self._path, self._sub_map)
        self._saved = _route_snapshot(self._sub_map)

    def save_if_changed(self) -> bool:
        """Write only if a route changed; True when it wrote."""
        snapshot = _route_snapshot(self._sub_map)
        if snapshot == self._saved:
            return False
        self._store.save(self._path, self._sub_map)
        self._saved = snapshot
        return True
