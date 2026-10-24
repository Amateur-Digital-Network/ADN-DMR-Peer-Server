# ADN DMR Peer Server - domain trunking
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

"""Homebrew DMR trunking protocol v0.6 (DMRGateway TrunkingEnabled=1, dmrtc): names, frames and DMRT."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum
from hashlib import blake2b

DTCL = b"DTCL"
DTCK = b"DTCK"
DTCC = b"DTCC"
DTCCL = b"DTCCL"
DTCO = b"DTCO"
DTCG = b"DTCG"
DTCPING = b"DTCPING"
DTCACK = b"DTCACK"
DTCPONG = b"DTCPONG"
DMRT = b"DMRT"

LOGIN_LEN = {DTCL: 8, DTCK: 40, DTCC: 302, DTCPING: 11, DTCCL: 9}
SITE_DMRD_LEN = 71

_HBP_DMRD_LEN = 55
_MAX_DMRT_LEN = 255
# A site drops any DMRT whose opcode lacks this bit.
_FROM_NETWORK = 0x40
# The 0x41 confirmation answers every address with one bit of a single byte.
_MAX_REGISTRATION_IDS = 8
_MAX_SUBSCRIPTION_TGS = 64


class DmrtOp(IntEnum):
    REGISTRATION = 0x01
    DEREGISTRATION = 0x02
    GROUP_SUBSCRIPTION = 0x03
    GROUP_UNSUBSCRIPTION = 0x04


@dataclass(frozen=True)
class DmrtRegistration:
    op: DmrtOp
    ms_ids: tuple[int, ...]


@dataclass(frozen=True)
class DmrtSubscription:
    op: DmrtOp
    tgs: tuple[int, ...]


DmrtMessage = DmrtRegistration | DmrtSubscription


def split_site_dmrd(data: bytes) -> tuple[bytes, bytes] | None:
    """The HBP frame and the call UUID of a site's DMRD; None unless it is exactly 71 bytes."""
    if len(data) != SITE_DMRD_LEN:
        return None
    return data[:_HBP_DMRD_LEN], data[_HBP_DMRD_LEN:]


def site_dmrd(packet: bytes, peer_id: bytes, uuid: bytes) -> bytes:
    """A 53- or 55-byte HBP frame as the 71 bytes a site takes, addressed to ``peer_id``."""
    return packet[:11] + peer_id + packet[15:_HBP_DMRD_LEN].ljust(40, b"\x00") + uuid


def derive_call_uuid(stream_id: bytes) -> bytes:
    """A stable call UUID for a stream that did not come from a site (dmrtc keys its calls on it)."""
    return blake2b(b"ADN-TIER3" + stream_id, digest_size=16).digest()


def is_echo_tg(tg: int) -> bool:
    return 9990 <= tg <= 9999


def subscribable_tg(tg: int) -> bool:
    """Whether a site may hold ``tg`` as a leg: not a local, service or parrot talkgroup."""
    return 5 < tg <= 0xFFFFFF and tg not in (9, 4000, 5000) and not is_echo_tg(tg)


def _addresses(data: bytes, offset: int, count: int) -> tuple[int, ...]:
    return tuple(int.from_bytes(data[i:i + 3], "big") for i in range(offset, offset + 3 * count, 3))


def _address_bytes(values: Sequence[int]) -> bytes:
    return b"".join(value.to_bytes(3, "big") for value in values)


def parse_dmrt(payload: bytes) -> DmrtMessage | None:
    """A site's DMRT message; None for anything malformed or not handled (never raises)."""
    if not 5 <= len(payload) <= _MAX_DMRT_LEN or payload[:4] != DMRT:
        return None
    op = payload[4]
    if op in (DmrtOp.REGISTRATION, DmrtOp.DEREGISTRATION):
        count = payload[13] if len(payload) > 13 else 0
        if not 1 <= count <= _MAX_REGISTRATION_IDS or len(payload) != 14 + 3 * count:
            return None
        return DmrtRegistration(DmrtOp(op), _addresses(payload, 14, count))
    if op in (DmrtOp.GROUP_SUBSCRIPTION, DmrtOp.GROUP_UNSUBSCRIPTION):
        count = payload[5] if len(payload) > 5 else 0
        if not 1 <= count <= _MAX_SUBSCRIPTION_TGS or len(payload) != 6 + 3 * count:
            return None
        return DmrtSubscription(DmrtOp(op), _addresses(payload, 6, count))
    return None


def registration_confirmation(accepted: Sequence[bool], ms_ids: Sequence[int], ts: int) -> bytes:
    """0x41: bit i of byte 13 accepts address i."""
    bits = sum(1 << i for i, ok in enumerate(accepted) if ok)
    return (
        DMRT
        + bytes((DmrtOp.REGISTRATION | _FROM_NETWORK,))
        + ts.to_bytes(8, "little", signed=True)
        + bytes((bits, len(ms_ids)))
        + _address_bytes(ms_ids)
    )


def deregistration(ms_ids: Sequence[int], ts: int) -> bytes:
    """0x42, laid out as the site's 0x02."""
    return (
        DMRT
        + bytes((DmrtOp.DEREGISTRATION | _FROM_NETWORK,))
        + ts.to_bytes(8, "little", signed=True)
        + bytes((len(ms_ids),))
        + _address_bytes(ms_ids)
    )


def subscription_confirmation(op: DmrtOp, tgs: Sequence[int]) -> bytes:
    """0x43 or 0x44 for ``op``; a refused talkgroup goes back as 0."""
    return DMRT + bytes((op | _FROM_NETWORK, len(tgs))) + _address_bytes(tgs)
