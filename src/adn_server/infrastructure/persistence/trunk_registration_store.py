# ADN DMR Peer Server - infrastructure persistence trunk registration store
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

"""Tier III registrations as JSON: ``[[ms_id, system, peer_id, t], ...]`` sorted by ``ms_id``."""

from __future__ import annotations

import itertools
import json
import logging
import math
import os
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from twisted.internet.defer import Deferred, DeferredLock, succeed

from ...application.trunking.ports import TrunkRegistrationStore
from ...application.trunking.registry import Registration, Site

logger = logging.getLogger(__name__)

# Same temp naming as the SUB_MAP store: two overlapping saves never share a temp file.
_tmp_seq = itertools.count()


def _registration(entry: Any) -> Registration | None:
    if not isinstance(entry, list) or len(entry) != 4:
        return None
    ms_id, system, peer_id, t = entry
    if (
        type(ms_id) is not int or not 0 < ms_id <= 0xFFFFFF
        or not isinstance(system, str)
        or type(peer_id) is not int or not 0 <= peer_id <= 0xFFFFFFFF
        or type(t) not in (int, float) or not math.isfinite(t)
    ):
        return None
    return Registration(ms_id, Site(system, peer_id.to_bytes(4, "big")), float(t))


class JsonTrunkRegistrationStore(TrunkRegistrationStore):
    """Writes through ``defer_to_thread``: a file of 1024 radios per site would hold up the reactor."""

    def __init__(self, defer_to_thread: Callable[..., Deferred]) -> None:
        self._defer_to_thread = defer_to_thread
        # One write at a time, in order: an older list never replaces a newer file.
        self._writes = DeferredLock()
        self._failed = False

    def load(self, path: str) -> list[Registration]:
        p = Path(path)
        if not p.is_file():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                raise ValueError(f"expected a list, got {type(data).__name__}")
        except (OSError, ValueError) as e:
            logger.warning("(TRUNK) %s unreadable, starting without Tier III registrations: %s", path, e)
            return []
        registrations = [r for r in map(_registration, data) if r is not None]
        if len(registrations) < len(data):
            logger.debug("(TRUNK) %s: %d malformed registrations skipped", path, len(data) - len(registrations))
        return registrations

    def save(self, path: str, registrations: Iterable[Registration]) -> None:
        # Listed here: the worker thread must not read what the reactor goes on changing.
        listed = list(registrations)
        self._writes.run(self._defer_to_thread, _write, path, listed).addCallbacks(self._wrote, self._write_failed)

    def written(self) -> Deferred:
        """Fires once every write asked for so far is done."""
        return self._writes.run(succeed, None)

    def last_write_failed(self) -> bool:
        return self._failed

    def _wrote(self, _: Any) -> None:
        self._failed = False

    def _write_failed(self, failure: Any) -> None:
        failure.trap(OSError)
        self._failed = True
        logger.warning("(TRUNK) Cannot write Tier III registrations to file: %s", failure.value)


def _write(path: str, registrations: list[Registration]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        [r.ms_id, r.site.system, int.from_bytes(r.site.peer_id, "big"), r.t]
        for r in sorted(registrations, key=lambda r: r.ms_id)
    ]
    # Write aside and rename: a crash mid-dump must not leave a truncated file, which load() would
    # turn into no registrations.
    tmp = p.with_name(f"{p.name}.tmp.{os.getpid()}.{threading.get_ident()}.{next(_tmp_seq)}")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rows, f)
        tmp.replace(p)
    finally:
        tmp.unlink(missing_ok=True)
