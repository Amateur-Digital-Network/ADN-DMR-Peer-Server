# ADN DMR Peer Server - tests domain mesh admission
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

"""OpenBridge admission rules: one frame in, one decision out."""

from __future__ import annotations

import logging

import pytest

from adn_server.domain import bytes_3, bytes_4
from adn_server.domain.mesh_admission import (
    AclRules,
    AdmissionContext,
    MeshEnvelope,
    ObpFrame,
    admit_dmrd_v1,
    admit_dmre_v5,
    call_attributes,
    check_acl_chain,
    check_hops,
    check_network_id,
    check_packet_age,
    check_slot,
    check_source_server,
    check_stun,
    check_tg_filter_v1,
    check_tg_filter_v5,
    server_prefix,
)

_SYSTEM = "OBP-FR"
_STREAM = bytes_4(0xAABBCCDD)
_SRC = bytes_3(2130001)
_NOW = 1_800_000_000.0


def _frame(dst: int = 214, *, slot: int = 1, call_type: str = "group") -> ObpFrame:
    return ObpFrame(
        system=_SYSTEM,
        stream_id=_STREAM,
        rf_src=_SRC,
        dst_id=bytes_3(dst),
        slot=slot,
        call_type=call_type,
    )


def _envelope(*, source_server: int = 20840, hops: int = 0, fresh: bool = True) -> MeshEnvelope:
    return MeshEnvelope(
        source_server=source_server,
        hops=hops,
        timestamp_ns=int(_NOW * 1_000_000_000) if fresh else 0,
        source_server_id=bytes_4(source_server),
    )


def _ctx(**kwargs) -> AdmissionContext:
    return AdmissionContext(**kwargs)


# --- bits byte ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("bits", "slot", "call_type"),
    [
        (0x00, 1, "group"),
        (0x80, 2, "group"),
        (0x40, 1, "unit"),
        (0xC0, 2, "unit"),
        (0x23, 1, "vcsbk"),
        (0xE3, 2, "unit"),  # the private bit wins over the CSBK pattern
    ],
)
def test_call_attributes_classifies_the_bits_byte(bits: int, slot: int, call_type: str) -> None:
    attrs = call_attributes(bits)
    assert (attrs.slot, attrs.call_type) == (slot, call_type)


def test_call_attributes_splits_frame_type_and_sequence() -> None:
    attrs = call_attributes(0x16)
    assert attrs.frame_type == 1
    assert attrs.dtype_vseq == 6


# --- identity ----------------------------------------------------------------


def test_network_id_match_is_admitted() -> None:
    assert check_network_id(_SYSTEM, _STREAM, expected=bytes_4(20840), received=bytes_4(20840)) is None


def test_network_id_mismatch_is_logged_but_not_quenched() -> None:
    rejection = check_network_id(_SYSTEM, _STREAM, expected=bytes_4(20840), received=bytes_4(26811))
    assert rejection is not None
    assert rejection.reason == "network-id-mismatch"
    assert rejection.level == logging.ERROR
    assert rejection.quench is False
    assert "OpenBridge packet discarded" in rejection.message


def test_network_id_mismatch_names_dmre_when_asked() -> None:
    rejection = check_network_id(
        _SYSTEM, _STREAM, expected=bytes_4(20840), received=bytes_4(26811), dmre=True
    )
    assert rejection is not None
    assert "OpenBridge DMRE discarded" in rejection.message


# --- slot, stun --------------------------------------------------------------


def test_slot_1_is_admitted_and_slot_2_is_not() -> None:
    assert check_slot(_frame()) is None
    rejection = check_slot(_frame(slot=2))
    assert rejection is not None
    assert rejection.reason == "not-slot-1"
    assert rejection.quench is False
    assert rejection.log_once is False  # legacy logs this one on every frame


def test_stunned_bridge_drops_without_quenching() -> None:
    assert check_stun(_frame(), stunned=False) is None
    rejection = check_stun(_frame(), stunned=True)
    assert rejection is not None
    assert rejection.reason == "stunned"
    assert rejection.quench is False


# --- DMRE envelope -----------------------------------------------------------


def test_fresh_packet_passes_and_old_one_is_dropped() -> None:
    assert check_packet_age(_frame(), _envelope(), now=_NOW) is None
    rejection = check_packet_age(_frame(), _envelope(), now=_NOW + 6)
    assert rejection is not None
    assert rejection.reason == "stale-packet"


def test_packet_without_timestamp_counts_as_stale() -> None:
    """A DMRE frame with no trailer has no timestamp, and legacy drops it."""
    rejection = check_packet_age(_frame(), _envelope(fresh=False), now=_NOW)
    assert rejection is not None
    assert rejection.reason == "stale-packet"


@pytest.mark.parametrize("source_server", [123, 12345678])
def test_source_server_must_be_4_to_7_digits(source_server: int) -> None:
    rejection = check_source_server(_frame(), _envelope(source_server=source_server), _ctx())
    assert rejection is not None
    assert rejection.reason == "source-server-length"


def test_short_source_server_must_be_a_known_server() -> None:
    envelope = _envelope(source_server=2084)
    ctx = _ctx(validate_server_ids=True, known_server_prefixes={"2131"})
    rejection = check_source_server(_frame(), envelope, ctx)
    assert rejection is not None
    assert rejection.reason == "source-server-unknown"
    ctx_known = _ctx(validate_server_ids=True, known_server_prefixes={"2084"})
    assert check_source_server(_frame(), envelope, ctx_known) is None


def test_unknown_short_source_server_passes_when_validation_is_off() -> None:
    envelope = _envelope(source_server=2084)
    assert check_source_server(_frame(), envelope, _ctx(known_server_prefixes={"2131"})) is None


def test_long_source_server_must_resolve_to_a_dmr_id() -> None:
    envelope = _envelope(source_server=2130001)
    rejection = check_source_server(_frame(), envelope, _ctx(resolve_server_id=lambda _id: False))
    assert rejection is not None
    assert rejection.reason == "source-server-invalid"
    assert check_source_server(_frame(), envelope, _ctx(resolve_server_id=lambda _id: "C31AG")) is None


def test_long_source_server_is_looked_up_by_its_wire_bytes() -> None:
    seen: list[bytes] = []
    envelope = _envelope(source_server=2130001)
    check_source_server(_frame(), envelope, _ctx(resolve_server_id=lambda sid: seen.append(sid) or True))
    assert seen == [bytes_4(2130001)]


def test_hops_are_counted_and_capped() -> None:
    assert check_hops(_frame(), _envelope(hops=8)) is None
    rejection = check_hops(_frame(), _envelope(hops=10))
    assert rejection is not None
    assert rejection.reason == "max-hops"
    assert rejection.level == logging.DEBUG
    assert rejection.log_once is False  # legacy quenches every looping frame


# --- talkgroup filters -------------------------------------------------------


@pytest.mark.parametrize("dst", [9, 79, 92, 199, 9990, 9999, 900999])
def test_tg_filter_v1_keeps_local_talkgroups_off_the_mesh(dst: int) -> None:
    rejection = check_tg_filter_v1(_frame(dst))
    assert rejection is not None
    assert rejection.reason == "tg-filter"
    assert rejection.quench is True


@pytest.mark.parametrize("dst", [80, 91, 200, 214, 9989, 10000])
def test_tg_filter_v1_lets_ordinary_talkgroups_through(dst: int) -> None:
    assert check_tg_filter_v1(_frame(dst)) is None


def test_tg_filter_v1_ignores_private_calls() -> None:
    assert check_tg_filter_v1(_frame(9, call_type="unit")) is None


@pytest.mark.parametrize(
    ("dst", "reason"),
    [
        (9, "tg-filter-repeater"),
        (79, "tg-filter-repeater"),
        (9990, "tg-filter-server"),
        (900999, "tg-filter-server"),
    ],
)
def test_tg_filter_v5_drops_talkgroups_that_never_leave_home(dst: int, reason: str) -> None:
    rejection = check_tg_filter_v5(_frame(dst), _envelope(), _ctx(server_id=2131))
    assert rejection is not None
    assert rejection.reason == reason


def test_tg_filter_v5_allows_a_server_local_tg_from_that_server() -> None:
    """92-199 belong to one server: only that server may bridge them."""
    ctx = _ctx(server_id=2084)
    assert check_tg_filter_v5(_frame(100), _envelope(source_server=20840), ctx) is None
    rejection = check_tg_filter_v5(_frame(100), _envelope(source_server=21310), ctx)
    assert rejection is not None
    assert rejection.reason == "tg-filter-server-main"


def test_tg_filter_v5_allows_an_mcc_tg_from_the_same_mcc() -> None:
    ctx = _ctx(server_id=2084)
    assert check_tg_filter_v5(_frame(85), _envelope(source_server=20851), ctx) is None
    rejection = check_tg_filter_v5(_frame(850), _envelope(source_server=21310), ctx)
    assert rejection is not None
    assert rejection.reason == "tg-filter-mcc"


def test_tg_filter_v5_lets_ordinary_talkgroups_through() -> None:
    assert check_tg_filter_v5(_frame(214), _envelope(), _ctx(server_id=2131)) is None


def test_tg_filter_v5_ignores_private_calls() -> None:
    assert check_tg_filter_v5(_frame(9, call_type="unit"), _envelope(), _ctx(server_id=2131)) is None


@pytest.mark.parametrize(("value", "prefix"), [(21310, 2131), (bytes_4(21310), 2131), (0, 0), (None, 0)])
def test_server_prefix_reads_int_or_bytes(value: object, prefix: int) -> None:
    assert server_prefix(value) == prefix


# --- ACLs --------------------------------------------------------------------


def _deny(*denied: bytes):
    def _check(target: bytes, _acl: object) -> bool:
        return target not in denied

    return _check


def test_acl_chain_is_skipped_without_a_router() -> None:
    ctx = _ctx(global_rules=AclRules(enabled=True), system_rules=AclRules(enabled=True))
    assert check_acl_chain(_frame(), ctx) is None


def test_acl_chain_admits_when_every_rule_passes() -> None:
    ctx = _ctx(
        acl_check=_deny(),
        global_rules=AclRules(enabled=True),
        system_rules=AclRules(enabled=True),
    )
    assert check_acl_chain(_frame(), ctx) is None


@pytest.mark.parametrize(
    ("denied", "scope", "reason"),
    [
        (_SRC, "global", "global-sub-acl"),
        (bytes_3(214), "global", "global-tg1-acl"),
        (_SRC, "system", "system-sub-acl"),
        (bytes_3(214), "system", "system-tg1-acl"),
    ],
)
def test_acl_chain_reports_which_rule_dropped_the_call(denied: bytes, scope: str, reason: str) -> None:
    rules = AclRules(enabled=True)
    ctx = _ctx(
        acl_check=_deny(denied),
        global_rules=rules if scope == "global" else AclRules(),
        system_rules=rules if scope == "system" else AclRules(),
    )
    rejection = check_acl_chain(_frame(), ctx)
    assert rejection is not None
    assert rejection.reason == reason
    assert rejection.quench is True


def test_global_talkgroup_acl_only_applies_to_slot_1() -> None:
    ctx = _ctx(acl_check=_deny(bytes_3(214)), global_rules=AclRules(enabled=True))
    assert check_acl_chain(_frame(slot=2), ctx) is None


# --- full gauntlets ----------------------------------------------------------


def test_dmrd_v1_admits_an_ordinary_group_call() -> None:
    ctx = _ctx(acl_check=_deny(), global_rules=AclRules(enabled=True))
    assert admit_dmrd_v1(_frame(214), ctx) is None


def test_dmrd_v1_reports_the_first_rule_that_fails() -> None:
    """Order matters: a STUNned bridge is reported as such, not as a TG drop."""
    ctx = _ctx(stunned=True, acl_check=_deny(_SRC), global_rules=AclRules(enabled=True))
    rejection = admit_dmrd_v1(_frame(9), ctx)
    assert rejection is not None
    assert rejection.reason == "stunned"


def test_dmre_v5_admits_an_ordinary_group_call() -> None:
    ctx = _ctx(server_id=2131, acl_check=_deny(), system_rules=AclRules(enabled=True))
    assert admit_dmre_v5(_frame(214), _envelope(), ctx, now=_NOW) is None


def test_dmre_v5_checks_the_envelope_before_the_talkgroup() -> None:
    ctx = _ctx(server_id=2131)
    rejection = admit_dmre_v5(_frame(9), _envelope(fresh=False), ctx, now=_NOW)
    assert rejection is not None
    assert rejection.reason == "stale-packet"


def test_every_rejection_can_be_formatted() -> None:
    """A log line with the wrong number of placeholders logs an error instead of
    the drop, so each rule's message and args are checked against each other."""
    ctx_deny = _ctx(
        stunned=False,
        acl_check=_deny(_SRC, bytes_3(214)),
        global_rules=AclRules(enabled=True),
        system_rules=AclRules(enabled=True),
        server_id=2084,
        validate_server_ids=True,
        known_server_prefixes={"2131"},
        resolve_server_id=lambda _id: False,
    )
    rejections = [
        check_network_id(_SYSTEM, _STREAM, expected=bytes_4(1), received=bytes_4(2)),
        check_network_id(_SYSTEM, _STREAM, expected=bytes_4(1), received=bytes_4(2), dmre=True),
        check_slot(_frame(slot=2)),
        check_stun(_frame(), stunned=True),
        check_packet_age(_frame(), _envelope(fresh=False), now=_NOW),
        check_source_server(_frame(), _envelope(source_server=123), _ctx()),
        check_source_server(_frame(), _envelope(source_server=2084), ctx_deny),
        check_source_server(_frame(), _envelope(source_server=2130001), ctx_deny),
        check_hops(_frame(), _envelope(hops=10)),
        check_tg_filter_v1(_frame(9)),
        check_tg_filter_v5(_frame(9), _envelope(), ctx_deny),
        check_tg_filter_v5(_frame(9990), _envelope(), ctx_deny),
        check_tg_filter_v5(_frame(100), _envelope(source_server=21310), ctx_deny),
        check_tg_filter_v5(_frame(85), _envelope(source_server=21310), ctx_deny),
        check_acl_chain(_frame(), ctx_deny),
        check_acl_chain(_frame(), _ctx(acl_check=_deny(_SRC), system_rules=AclRules(enabled=True))),
    ]
    assert all(r is not None for r in rejections)
    seen = set()
    for rejection in rejections:
        assert rejection is not None
        rejection.message % rejection.args  # raises if the arity is wrong
        seen.add(rejection.reason)
    assert len(seen) == len(rejections) - 1  # the two network-id variants share a reason
