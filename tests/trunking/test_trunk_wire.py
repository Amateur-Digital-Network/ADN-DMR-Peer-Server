# ADN DMR Peer Server - tests trunking wire
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

"""Homebrew DMR trunking v0.6 wire format against the bytes dmrtc builds and reads."""

from __future__ import annotations

import random

import pytest

from adn_server.domain import bytes_3
from adn_server.domain.trunking import (
    DTCC,
    DTCCL,
    DTCK,
    DTCL,
    DTCPING,
    LOGIN_LEN,
    DmrtOp,
    DmrtRegistration,
    DmrtSubscription,
    deregistration,
    derive_call_uuid,
    is_echo_tg,
    parse_dmrt,
    registration_confirmation,
    site_dmrd,
    split_site_dmrd,
    subscribable_tg,
    subscription_confirmation,
)

MS, MS2 = 2140501, 2140502  # MS = 0x20A955
TS = 1_700_000_000
UUID = bytes(range(0xA0, 0xB0))


def _registration(op: int, ids: list[int]) -> bytes:
    return b"DMRT" + bytes((op,)) + TS.to_bytes(8, "little") + bytes((len(ids),)) + b"".join(map(bytes_3, ids))


def _subscription(op: int, tgs: list[int]) -> bytes:
    return b"DMRT" + bytes((op, len(tgs))) + b"".join(map(bytes_3, tgs))


_EXACT = [
    (_registration(0x01, [MS]), DmrtRegistration(DmrtOp.REGISTRATION, (MS,))),
    (_registration(0x02, [MS]), DmrtRegistration(DmrtOp.DEREGISTRATION, (MS,))),
    (_registration(0x01, [MS, MS2, 1]), DmrtRegistration(DmrtOp.REGISTRATION, (MS, MS2, 1))),
    (_subscription(0x03, [91, 214]), DmrtSubscription(DmrtOp.GROUP_SUBSCRIPTION, (91, 214))),
    (_subscription(0x04, [0xFFFFFF]), DmrtSubscription(DmrtOp.GROUP_UNSUBSCRIPTION, (0xFFFFFF,))),
]


def test_dmrtc_registration_and_subscription_bytes_parse() -> None:
    """The exact frames dmrtc's NetworkSignalling builds for 0x01 and 0x03 decode to their ids."""
    assert parse_dmrt(bytes.fromhex("444d52540100f15365000000000120a955")) == DmrtRegistration(
        DmrtOp.REGISTRATION, (MS,),
    )
    assert parse_dmrt(bytes.fromhex("444d5254030200005b0000d6")) == DmrtSubscription(
        DmrtOp.GROUP_SUBSCRIPTION, (91, 214),
    )


@pytest.mark.parametrize(("raw", "message"), _EXACT)
def test_each_opcode_parses_at_its_exact_length(raw: bytes, message: object) -> None:
    """0x01-0x04 decode when the length is exactly what their count says."""
    assert parse_dmrt(raw) == message


@pytest.mark.parametrize("raw", [raw for raw, _ in _EXACT])
def test_dmrt_off_by_one_is_rejected(raw: bytes) -> None:
    """One byte short or one byte too many is not that message: lengths are exact."""
    assert parse_dmrt(raw[:-1]) is None
    assert parse_dmrt(raw + b"\x00") is None


def test_the_largest_messages_parse() -> None:
    """8 registrations (one confirmation bitfield) and 64 talkgroups (dmrtc's maximum) fit."""
    ids = list(range(MS, MS + 8))
    tgs = list(range(1000, 1064))
    assert parse_dmrt(_registration(0x01, ids)).ms_ids == tuple(ids)
    assert parse_dmrt(_subscription(0x03, tgs)).tgs == tuple(tgs)


@pytest.mark.parametrize("raw", [
    _registration(0x01, []),
    _registration(0x01, [MS] * 9),
    _subscription(0x03, []),
    _subscription(0x03, [91] * 65),
])
def test_counts_outside_their_range_are_rejected(raw: bytes) -> None:
    """Registration counts 0 and 9, subscription counts 0 and 65."""
    assert parse_dmrt(raw) is None


@pytest.mark.parametrize("raw", [
    b"DMRT",
    b"DMRT\x03" + b"\x00" * 251,
    b"DMRX" + _registration(0x01, [MS])[4:],
    b"DMRT\x41" + _registration(0x01, [MS])[5:],
    b"DMRT\x43" + _subscription(0x03, [91])[5:],
    b"DMRT\x81" + b"\x00" * 12,
    b"DMRT\xc1",
    b"DMRT\x00" + b"\x00" * 12,
    b"DMRT\x0b" + b"\x00" * 30,
])
def test_other_payloads_are_rejected(raw: bytes) -> None:
    """Too short or too long, another tag, network or site-local opcodes, and an unknown opcode."""
    assert parse_dmrt(raw) is None


def test_random_payloads_never_raise() -> None:
    """Whatever a site sends, parsing answers a message or None."""
    rng = random.Random(3)
    for _ in range(10_000):
        body = rng.randbytes(rng.randrange(0, 300))
        for raw in (body, b"DMRT" + body):
            message = parse_dmrt(raw)
            assert message is None or isinstance(message, (DmrtRegistration, DmrtSubscription))


def test_registration_confirmation_is_what_dmrtc_reads() -> None:
    """0x41 for one accepted radio, byte for byte as dmrtc's parser expects it."""
    assert registration_confirmation([True], [MS], TS) == bytes.fromhex("444d52544100f1536500000000010120a955")


def test_registration_confirmation_bitfield_is_lsb_first() -> None:
    """Bit i of byte 13 answers address i; the count and the ids follow."""
    raw = registration_confirmation([True, False, True], [MS, MS2, 1], TS)
    assert raw[4] == 0x41 and raw[13] == 0b101 and raw[14] == 3
    assert raw[15:] == bytes_3(MS) + bytes_3(MS2) + bytes_3(1)


def test_deregistration_round_trips_as_the_sites_layout() -> None:
    """0x42 carries the 0x02 layout: without the network bit it parses back to the same ids."""
    raw = deregistration([MS, MS2], TS)
    assert raw[4] == 0x42
    assert parse_dmrt(raw[:4] + b"\x02" + raw[5:]) == DmrtRegistration(DmrtOp.DEREGISTRATION, (MS, MS2))


@pytest.mark.parametrize("op", [DmrtOp.GROUP_SUBSCRIPTION, DmrtOp.GROUP_UNSUBSCRIPTION])
def test_subscription_confirmation_round_trips_with_refused_as_zero(op: DmrtOp) -> None:
    """0x43/0x44 keep the order and send a refused talkgroup as 0."""
    raw = subscription_confirmation(op, [91, 0])
    assert raw == b"DMRT" + bytes((op | 0x40, 2)) + bytes_3(91) + bytes_3(0)
    assert parse_dmrt(raw[:4] + bytes((op,)) + raw[5:]) == DmrtSubscription(op, (91, 0))


@pytest.mark.parametrize("size", [55, 56, 63, 70, 72, 100])
def test_only_71_byte_site_dmrd_splits(size: int) -> None:
    """A site's DMRD is the 55-byte frame plus the 16-byte UUID, nothing else."""
    assert split_site_dmrd(bytes(size)) is None


def test_a_71_byte_site_dmrd_splits_into_frame_and_uuid() -> None:
    """Routing gets the 55-byte frame; the UUID stays with the site."""
    frame = bytes(range(55))
    assert split_site_dmrd(frame + UUID) == (frame, UUID)


@pytest.mark.parametrize("size", [53, 55])
def test_site_dmrd_is_71_bytes_with_the_site_peer_and_uuid(size: int) -> None:
    """53-byte (OpenBridge) and 55-byte frames both leave as 71 bytes addressed to the site."""
    packet = bytes(range(1, size + 1))
    out = site_dmrd(packet, b"\x0c\xc1\x7a\x01", UUID)
    assert len(out) == 71
    assert out[:11] == packet[:11] and out[11:15] == b"\x0c\xc1\x7a\x01"
    assert out[15:size] == packet[15:] and out[size:55] == bytes(55 - size)
    assert out[55:] == UUID


def test_derived_call_uuid_is_stable_per_stream_and_distinct_across_streams() -> None:
    """Every frame of one call carries one UUID, and two calls never share it (dmrtc keys calls on it)."""
    a = derive_call_uuid(b"\x0a\x0b\x0c\x0d")
    assert len(a) == 16 and a == derive_call_uuid(b"\x0a\x0b\x0c\x0d")
    assert a != derive_call_uuid(b"\x0a\x0b\x0c\x0e")


@pytest.mark.parametrize(("tg", "ok"), [
    (0, False), (1, False), (5, False), (6, True), (9, False), (10, True), (91, True),
    (4000, False), (5000, False), (9989, True), (9990, False), (9999, False), (10000, True),
    (0xFFFFFF, True), (0x1000000, False),
])
def test_subscribable_talkgroups(tg: int, ok: bool) -> None:
    """Local, service and parrot talkgroups, and anything above 24 bits, are never a site's leg."""
    assert subscribable_tg(tg) is ok


def test_echo_talkgroups_are_9990_to_9999() -> None:
    """The parrot range, answered only to the site that keyed it."""
    assert is_echo_tg(9990) and is_echo_tg(9999)
    assert not is_echo_tg(9989) and not is_echo_tg(10000)


def test_login_lengths_are_the_spec_frames() -> None:
    """Each login frame is its name, the 4-byte peer id and its fixed fields."""
    assert LOGIN_LEN == {
        DTCL: len(DTCL) + 4,
        DTCK: len(DTCK) + 4 + 32,
        DTCC: len(DTCC) + 4 + 294,
        DTCPING: len(DTCPING) + 4,
        DTCCL: len(DTCCL) + 4,
    }
