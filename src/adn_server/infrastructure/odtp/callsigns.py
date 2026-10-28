# ADN DMR Peer Server - ODTP gateway callsigns
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

"""DMR ID -> callsign from a RadioID subscriber_ids.json, looked up at login without loading the file."""

# Parsed whole, the file is ~200 MB of Python objects for 300k IDs; a login needs one entry, and a
# plain find over the mapped file takes ~15 ms. Lookups, found or not, are kept in a small cache.

from __future__ import annotations

import mmap
import re

_CALLSIGN = re.compile(rb'\s*"callsign":\s*"([^"]*)"')


class CallsignFile:
    _CACHE = 1024

    def __init__(self, path: str) -> None:
        self.path = path
        self._cache: dict[int, str] = {}

    def __call__(self, dmr_id: int) -> str:
        if dmr_id in self._cache:
            return self._cache[dmr_id]
        callsign = self._find(dmr_id)  # "" for an ID not in the file, kept too: no scan on every login
        if callsign is None:
            return ""
        if len(self._cache) >= self._CACHE:
            self._cache.pop(next(iter(self._cache)))
        self._cache[dmr_id] = callsign
        return callsign

    def _find(self, dmr_id: int) -> str | None:
        try:
            with open(self.path, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                for key in (b'"id":%d,' % dmr_id, b'"id": %d,' % dmr_id):
                    at = mm.find(key)
                    if at >= 0:
                        match = _CALLSIGN.match(mm[at + len(key):at + len(key) + 200])
                        return match.group(1).decode("utf-8", "replace") if match else ""
        except (OSError, ValueError):  # no file (yet): look again next time
            return None
        return ""
