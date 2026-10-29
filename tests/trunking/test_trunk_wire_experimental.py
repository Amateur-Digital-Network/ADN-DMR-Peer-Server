# ADN DMR Peer Server - tests trunking wire experimental
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

"""EXPERIMENTAL DMRT 0x05-0x0A against the bytes dmrtc's NetworkSignalling builds."""

from __future__ import annotations

import random

import pytest

from adn_server.domain import bytes_3
from adn_server.domain.trunking import (
    DmrtCallSetup,
    DmrtOp,
    DmrtRegistration,
    DmrtReply,
    DmrtStatus,
    DmrtSubscription,
    DmrtUdt,
    call_reject,
    parse_dmrt,
    to_network,
)
from tests.support.dmrtc_wire import dmrt_call_setup, dmrt_reply, dmrt_status, dmrt_udt

MS, MS2 = 2140501, 2140502
UUID = bytes(range(0xA0, 0xB0))

_EXACT = [
    (dmrt_udt(UUID, MS, MS2, b"hello"), DmrtUdt(UUID, False, MS, MS2)),
    (dmrt_udt(UUID, MS, 91, b"x" * 46, group=True), DmrtUdt(UUID, True, MS, 91)),
    (dmrt_reply(0x06, UUID, MS2, MS), DmrtReply(DmrtOp.UDT_ACCEPT, UUID, MS2, MS)),
    (dmrt_call_setup(UUID, MS, MS2, 0x20), DmrtCallSetup(UUID, MS, MS2)),
    (dmrt_reply(0x08, UUID, MS2, MS), DmrtReply(DmrtOp.CALL_ACCEPT, UUID, MS2, MS)),
    (dmrt_reply(0x09, UUID, MS2, MS), DmrtReply(DmrtOp.CALL_REJECT, UUID, MS2, MS)),
    (dmrt_status(UUID, MS, 91, 5, group=True), DmrtStatus(UUID, True, MS, 91)),
    (dmrt_status(UUID, MS, MS2, 127), DmrtStatus(UUID, False, MS, MS2)),
]


def test_dmrtc_private_udt_bytes_parse() -> None:
    """A private short message laid out as createUDTTransferMessage builds it (type 0x44, size, src, dst, text)."""
    raw = bytes.fromhex("444d525405") + UUID + bytes.fromhex("440220a95520a956") + b"hi"
    assert parse_dmrt(raw) == DmrtUdt(UUID, False, MS, MS2)


@pytest.mark.parametrize(("raw", "message"), _EXACT)
def test_each_experimental_opcode_parses_at_its_exact_length(raw: bytes, message: object) -> None:
    """0x05 at 29 + its size, 0x06/0x08/0x09 at 27, 0x07/0x0A at 28."""
    assert parse_dmrt(raw) == message


@pytest.mark.parametrize("raw", [raw for raw, _ in _EXACT])
def test_experimental_off_by_one_is_rejected(raw: bytes) -> None:
    """One byte short or one byte too many is not that message."""
    assert parse_dmrt(raw[:-1]) is None
    assert parse_dmrt(raw + b"\x00") is None


def test_udt_longer_than_46_bytes_is_rejected() -> None:
    """dmrtc never builds a UDT above 46 bytes of text, whatever the size byte says."""
    assert parse_dmrt(dmrt_udt(UUID, MS, MS2, b"x" * 47)) is None


def test_random_payloads_never_raise() -> None:
    """Whatever a site sends, parsing answers a message or None."""
    rng = random.Random(5)
    for _ in range(10_000):
        size = rng.choice((27, 28, 29 + rng.randrange(0, 50), rng.randrange(0, 300)))
        body = bytes((rng.randrange(1, 0x0B),)) + rng.randbytes(max(size - 5, 0))
        message = parse_dmrt(b"DMRT" + body)
        assert message is None or isinstance(
            message, (DmrtRegistration, DmrtSubscription, DmrtUdt, DmrtCallSetup, DmrtReply, DmrtStatus),
        )


@pytest.mark.parametrize("raw", [raw for raw, _ in _EXACT])
def test_to_network_sets_only_the_network_bit(raw: bytes) -> None:
    """A forwarded message is the site's bytes with 0x40 added to the opcode, as dmrtc requires."""
    out = to_network(raw)
    assert out[4] == raw[4] | 0x40 and out[:4] == raw[:4] and out[5:] == raw[5:]


def test_call_reject_is_what_dmrtc_reads() -> None:
    """0x49: UUID, src and dst of the setup it refuses (27 bytes)."""
    raw = call_reject(UUID, MS, MS2)
    assert raw == b"DMRT\x49" + UUID + bytes_3(MS) + bytes_3(MS2)
    assert parse_dmrt(raw[:4] + b"\x09" + raw[5:]) == DmrtReply(DmrtOp.CALL_REJECT, UUID, MS, MS2)
