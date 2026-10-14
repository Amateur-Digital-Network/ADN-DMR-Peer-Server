# ADN DMR Peer Server - value objects
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

"""Value objects: DMR IDs, slot, call type, etc. (mirror legacy const and types)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Legacy ID_MIN=1, ID_MAX=16776415, PEER_MAX=4294967295
ID_MIN = 1
ID_MAX = 16776415
PEER_MAX = 4294967295


@dataclass(frozen=True, slots=True)
class DmrId:
    """DMR subscriber/peer/talkgroup numeric ID."""

    value: int

    def __int__(self) -> int:
        return self.value


@dataclass(frozen=True, slots=True)
class TgId:
    """Talkgroup ID (same numeric space as DmrId for group calls)."""

    value: int

    def __int__(self) -> int:
        return self.value


# (callsign, fname, surname, talker_alias). The subscriber file holds over 300k of these:
# a dict each costs twice the memory, and a NamedTuple stays tracked by the garbage
# collector, adding ~40 ms to every full collection on the reactor thread.
SubscriberProfile = tuple[str | None, str, str, str]


Slot = Literal[1, 2]
"""Timeslot 1 or 2."""

CallType = Literal["unit", "group"]
"""Unit (private) or group call."""


def bytes_3(i: int) -> bytes:
    """Encode int as 3-byte big-endian (legacy bytes_3)."""
    return (int(i) & 0xFFFFFF).to_bytes(3, "big")


def bytes_4(i: int) -> bytes:
    """Encode int as 4-byte big-endian (legacy bytes_4, salt, peer_id in packets)."""
    return (int(i) & 0xFFFFFFFF).to_bytes(4, "big")


def int_id(val: bytes | int) -> int:
    """Decode 3- or 4-byte big-endian to int (legacy int_id)."""
    if isinstance(val, int):
        return val
    if len(val) >= 4:
        return int.from_bytes(val[:4], "big")
    if len(val) == 3:
        return int.from_bytes(val, "big")
    return 0
