# ADN DMR Peer Server - tests support dmrtc wire
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

"""What a Tier III site sends, in dmrtc's NetworkSignalling byte layout, and readers for the server's answers."""

from __future__ import annotations

from collections.abc import Sequence


def _addresses(values: Sequence[int]) -> bytes:
    return b"".join(value.to_bytes(3, "big") for value in values)


def _read_addresses(msg: bytes, offset: int, count: int) -> list[int]:
    return [int.from_bytes(msg[i:i + 3], "big") for i in range(offset, offset + 3 * count, 3)]


def dmrt_registration(op: int, ids: Sequence[int], ts: int) -> bytes:
    """0x01 or 0x02: unix time (uint64 LE) at [5..12], count at [13], ids from [14]."""
    return b"DMRT" + bytes((op,)) + ts.to_bytes(8, "little") + bytes((len(ids),)) + _addresses(ids)


def dmrt_subscription(op: int, tgs: Sequence[int]) -> bytes:
    """0x03 or 0x04: count at [5], talkgroups from [6]."""
    return b"DMRT" + bytes((op, len(tgs))) + _addresses(tgs)


def read_registration_confirmation(msg: bytes) -> tuple[int, list[tuple[int, bool]]]:
    """0x41: its time and (id, accepted) per address; dmrtc reads bit i of [13] for address i."""
    count = msg[14]
    assert msg[:5] == b"DMRT\x41" and len(msg) == 15 + 3 * count
    accepted = [bool(msg[13] >> i & 1) for i in range(count)]
    return int.from_bytes(msg[5:13], "little"), list(zip(_read_addresses(msg, 15, count), accepted))


def read_deregistration(msg: bytes) -> list[int]:
    """0x42, laid out as a site's 0x02."""
    count = msg[13]
    assert msg[:5] == b"DMRT\x42" and len(msg) == 14 + 3 * count
    return _read_addresses(msg, 14, count)


def read_subscription_confirmation(msg: bytes) -> tuple[int, list[int]]:
    """0x43 or 0x44: the opcode, and the talkgroups with 0 where one was refused."""
    count = msg[5]
    assert msg[:4] == b"DMRT" and len(msg) == 6 + 3 * count
    return msg[4], _read_addresses(msg, 6, count)
