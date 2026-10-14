# ADN DMR Peer Server - domain mesh admission
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

"""Admission rules for OpenBridge ingress, as pure decisions.

Every rule answers one question — may this frame continue? — and returns a
``Rejection`` describing what to log and whether to quench the peer, or ``None``
to let the frame through. Nothing here touches sockets, the reactor, the clock
or the live SYSTEMS config: the caller passes what the rule needs and applies
the outcome. That is what makes the gauntlet testable with plain values, and
what lets a later engine reuse the same decisions without the Twisted adapter.

The wire formats live in ``infrastructure.mesh`` (obp_v1, dmre_v5); this module
only decides what to do with a frame once it has been decoded.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from .value_objects import int_id

# Talkgroups a mesh peer must never hand us: hblink's hard-coded table, kept as
# named constants so the ranges can be read (and one day configured) on sight.
TG_LOCAL_TO_REPEATER_MAX = 79
TG_LOCAL_TO_SERVER = (9990, 9999)
TG_LOCAL_TO_SERVER_MAIN = (92, 199)
TG_LOCAL_TO_MCC = ((80, 89), (800, 899))
TG_DATA_GATEWAY = 900999

MAX_HOPS = 10
MAX_PACKET_AGE_S = 5.0

_AclCheck = Callable[[bytes, Any], bool]


@dataclass(frozen=True)
class CallAttributes:
    """Slot and call classification carried by the DMRD bits byte."""

    slot: int
    call_type: str
    frame_type: int
    dtype_vseq: int


def call_attributes(bits: int) -> CallAttributes:
    """Decode the bits byte of a DMRD/DMRE frame.

    ``vcsbk`` (a CSBK preamble) shares the data-sync pattern with voice, so it is
    recognised before the group fallback — mis-classifying it is what turns a
    private data preamble into a dynamic talkgroup on the far side.
    """
    if bits & 0x40:
        call_type = "unit"
    elif (bits & 0x23) == 0x23:
        call_type = "vcsbk"
    else:
        call_type = "group"
    return CallAttributes(
        slot=2 if (bits & 0x80) else 1,
        call_type=call_type,
        frame_type=(bits & 0x30) >> 4,
        dtype_vseq=bits & 0xF,
    )


@dataclass(frozen=True)
class ObpFrame:
    """The identity of one ingress frame, as the admission rules see it."""

    system: str
    stream_id: bytes
    rf_src: bytes
    dst_id: bytes
    slot: int
    call_type: str

    @property
    def dst(self) -> int:
        return int(int_id(self.dst_id))


@dataclass(frozen=True)
class Rejection:
    """Why a frame was dropped, and what the caller should do about it.

    ``message``/``args`` are kept apart so the caller can hand them straight to
    ``logger.log`` and keep lazy formatting. ``reason`` is the stable handle: log
    text may be reworded, ``reason`` is what metrics and tests match on.
    """

    reason: str
    message: str
    args: tuple[Any, ...] = ()
    level: int = logging.INFO
    quench: bool = True
    log_once: bool = True


@dataclass(frozen=True)
class AclRules:
    """One ACL scope (GLOBAL or the system's own)."""

    enabled: bool = False
    sub_acl: Any = (True, [])
    tg1_acl: Any = (True, [])


@dataclass(frozen=True)
class MeshEnvelope:
    """DMRE v5 envelope fields the admission rules look at.

    ``source_server_id`` is the same value as ``source_server``, kept in its wire
    form because the alias lookup that validates it takes bytes.
    """

    source_server: int
    hops: int
    timestamp_ns: int = 0
    source_server_id: bytes = b""


@dataclass(frozen=True)
class AdmissionContext:
    """Everything outside the frame that the rules depend on."""

    stunned: bool = False
    acl_check: _AclCheck | None = None
    global_rules: AclRules = field(default_factory=AclRules)
    system_rules: AclRules = field(default_factory=AclRules)
    server_id: int = 0
    validate_server_ids: bool = False
    known_server_prefixes: Iterable[str] = ()
    resolve_server_id: Callable[[bytes], Any] | None = None  # alias lookup, bytes in


def server_prefix(server_id: Any) -> int:
    """First four digits of our SERVER_ID, whether it is stored as int or bytes."""
    if isinstance(server_id, bytes):
        server_id = int.from_bytes(server_id, "big")
    try:
        return int(str(int(server_id))[:4])
    except (TypeError, ValueError):
        return 0


def check_network_id(
    system: str,
    stream_id: bytes,
    *,
    expected: bytes,
    received: bytes,
    dmre: bool = False,
) -> Rejection | None:
    """The peer must send the NETWORK_ID we have configured for this bridge."""
    if expected == received:
        return None
    label = "OpenBridge DMRE discarded" if dmre else "OpenBridge packet discarded"
    return Rejection(
        reason="network-id-mismatch",
        message="(%s) " + label + " because NETWORK_ID: %s Does not match sent Peer ID: %s",
        args=(system, int_id(expected or b""), int_id(received)),
        level=logging.ERROR,
        quench=False,
    )


def check_slot(frame: ObpFrame) -> Rejection | None:
    """DMRD v1 over OpenBridge is TS1 only."""
    if frame.slot == 1:
        return None
    return Rejection(
        reason="not-slot-1",
        message="(%s) OpenBridge packet discarded because it was not received on slot 1. SID: %s, TGID %s",
        args=(frame.system, int_id(frame.rf_src), int_id(frame.dst_id)),
        level=logging.ERROR,
        quench=False,
        log_once=False,
    )


def check_stun(frame: ObpFrame, *, stunned: bool) -> Rejection | None:
    """A STUNned bridge accepts nothing until the operator lifts it."""
    if not stunned:
        return None
    return Rejection(
        reason="stunned",
        message="(%s) Bridge STUNned, discarding",
        args=(frame.system,),
        level=logging.WARNING,
        quench=False,
    )


def check_packet_age(frame: ObpFrame, envelope: MeshEnvelope, *, now: float) -> Rejection | None:
    """DMRE carries a timestamp; a late frame is a replay or a stalled path."""
    if envelope.timestamp_ns / 1_000_000_000 >= (now - MAX_PACKET_AGE_S):
        return None
    return Rejection(
        reason="stale-packet",
        message="(%s) Packet from server %s more than 5s old!, discarding",
        args=(frame.system, envelope.source_server),
        level=logging.WARNING,
    )


def check_source_server(
    frame: ObpFrame,
    envelope: MeshEnvelope,
    ctx: AdmissionContext,
) -> Rejection | None:
    """A DMRE source server is a 4-7 digit ID, known to us or a valid DMR ID."""
    digits = str(envelope.source_server)
    if len(digits) < 4 or len(digits) > 7:
        return Rejection(
            reason="source-server-length",
            message="(%s) Source Server should be between 4 and 7 digits, discarding Src: %s",
            args=(frame.system, envelope.source_server),
            level=logging.WARNING,
        )
    if ctx.validate_server_ids and len(digits) in (4, 5) and digits[:4] not in ctx.known_server_prefixes:
        return Rejection(
            reason="source-server-unknown",
            message="(%s) Source Server ID is 4 or 5 digits but not in list: %s",
            args=(frame.system, envelope.source_server),
            level=logging.WARNING,
        )
    if len(digits) > 5 and ctx.resolve_server_id is not None:
        if not ctx.resolve_server_id(envelope.source_server_id):
            return Rejection(
                reason="source-server-invalid",
                message="(%s) Source Server 6 or 7 digits but not a valid DMR ID, discarding Src: %s",
                args=(frame.system, envelope.source_server),
                level=logging.WARNING,
            )
    return None


def check_hops(frame: ObpFrame, envelope: MeshEnvelope) -> Rejection | None:
    """Every mesh hop bumps the counter; past MAX_HOPS the frame is looping."""
    hops = envelope.hops + 1
    if hops <= MAX_HOPS:
        return None
    return Rejection(
        reason="max-hops",
        message="(%s) MAX HOPS exceed, dropping. Hops: %s, DST: %s, SRC: %s",
        args=(frame.system, hops, frame.dst, envelope.source_server),
        level=logging.DEBUG,
        log_once=False,
    )


def _tg_filter(frame: ObpFrame, reason: str, scope: str) -> Rejection:
    return Rejection(
        reason=reason,
        message="(%s) CALL DROPPED WITH STREAM ID %s ON TG %s BY GLOBAL TG FILTER (%s)",
        args=(frame.system, int_id(frame.stream_id), frame.dst, scope),
    )


def check_tg_filter_v1(frame: ObpFrame) -> Rejection | None:
    """DMRD v1 filter: one rule for every talkgroup that must stay local."""
    if frame.call_type == "unit":
        return None
    dst = frame.dst
    if (
        dst <= TG_LOCAL_TO_REPEATER_MAX
        or TG_LOCAL_TO_SERVER[0] <= dst <= TG_LOCAL_TO_SERVER[1]
        or TG_LOCAL_TO_SERVER_MAIN[0] <= dst <= TG_LOCAL_TO_SERVER_MAIN[1]
        or dst == TG_DATA_GATEWAY
    ):
        return Rejection(
            reason="tg-filter",
            message="(%s) CALL DROPPED WITH STREAM ID %s FROM SUBSCRIBER %s BY GLOBAL TG FILTER",
            args=(frame.system, int_id(frame.stream_id), dst),
        )
    return None


def check_tg_filter_v5(
    frame: ObpFrame,
    envelope: MeshEnvelope,
    ctx: AdmissionContext,
) -> Rejection | None:
    """DMRE v5 filter: same idea, but the last two rules depend on who sent it.

    A talkgroup local to a server, or to an MCC, is only refused when the source
    server is *not* part of that server or that MCC.
    """
    if frame.call_type == "unit":
        return None
    dst = frame.dst
    if dst <= TG_LOCAL_TO_REPEATER_MAX:
        return _tg_filter(frame, "tg-filter-repeater", "local to repeater")
    if TG_LOCAL_TO_SERVER[0] <= dst <= TG_LOCAL_TO_SERVER[1] or dst == TG_DATA_GATEWAY:
        return _tg_filter(frame, "tg-filter-server", "local to server")
    source = str(envelope.source_server)
    if TG_LOCAL_TO_SERVER_MAIN[0] <= dst <= TG_LOCAL_TO_SERVER_MAIN[1]:
        if int(source[:4]) != ctx.server_id:
            return _tg_filter(frame, "tg-filter-server-main", "local to server main ID")
        return None
    for low, high in TG_LOCAL_TO_MCC:
        if low <= dst <= high and int(source[:3]) != int(str(ctx.server_id)[:3]):
            return _tg_filter(frame, "tg-filter-mcc", "local to MCC")
    return None


def check_acl_chain(frame: ObpFrame, ctx: AdmissionContext) -> Rejection | None:
    """Subscriber and talkgroup ACLs, GLOBAL first and then the system's own."""
    acl_check = ctx.acl_check
    if acl_check is None:
        return None
    if ctx.global_rules.enabled:
        if not acl_check(frame.rf_src, ctx.global_rules.sub_acl):
            return Rejection(
                reason="global-sub-acl",
                message="(%s) CALL DROPPED WITH STREAM ID %s ON TGID %s BY GLOBAL TS1 ACL",
                args=(frame.system, int_id(frame.stream_id), int_id(frame.rf_src)),
            )
        if frame.slot == 1 and not acl_check(frame.dst_id, ctx.global_rules.tg1_acl):
            return Rejection(
                reason="global-tg1-acl",
                message="(%s) CALL DROPPED WITH STREAM ID %s ON TGID %s BY GLOBAL TS1 ACL",
                args=(frame.system, int_id(frame.stream_id), int_id(frame.dst_id)),
            )
    if ctx.system_rules.enabled:
        if not acl_check(frame.rf_src, ctx.system_rules.sub_acl):
            return Rejection(
                reason="system-sub-acl",
                message="(%s) CALL DROPPED WITH STREAM ID %s FROM SUBSCRIBER %s BY SYSTEM ACL",
                args=(frame.system, int_id(frame.stream_id), int_id(frame.rf_src)),
            )
        if not acl_check(frame.dst_id, ctx.system_rules.tg1_acl):
            return Rejection(
                reason="system-tg1-acl",
                message="(%s) CALL DROPPED WITH STREAM ID %s ON TGID %s BY SYSTEM ACL",
                args=(frame.system, int_id(frame.stream_id), int_id(frame.dst_id)),
            )
    return None


def admit_dmrd_v1(frame: ObpFrame, ctx: AdmissionContext) -> Rejection | None:
    """Full DMRD v1 gauntlet, in the order the legacy handler applied it."""
    return (
        check_slot(frame)
        or check_stun(frame, stunned=ctx.stunned)
        or check_tg_filter_v1(frame)
        or check_acl_chain(frame, ctx)
    )


def admit_dmre_v5(
    frame: ObpFrame,
    envelope: MeshEnvelope,
    ctx: AdmissionContext,
    *,
    now: float,
) -> Rejection | None:
    """Full DMRE v5 gauntlet, in the order the legacy handler applied it."""
    return (
        check_stun(frame, stunned=ctx.stunned)
        or check_packet_age(frame, envelope, now=now)
        or check_source_server(frame, envelope, ctx)
        or check_hops(frame, envelope)
        or check_tg_filter_v5(frame, envelope, ctx)
        or check_acl_chain(frame, ctx)
    )


__all__ = [
    "AclRules",
    "AdmissionContext",
    "CallAttributes",
    "MAX_HOPS",
    "MAX_PACKET_AGE_S",
    "MeshEnvelope",
    "ObpFrame",
    "Rejection",
    "TG_DATA_GATEWAY",
    "TG_LOCAL_TO_MCC",
    "TG_LOCAL_TO_REPEATER_MAX",
    "TG_LOCAL_TO_SERVER",
    "TG_LOCAL_TO_SERVER_MAIN",
    "admit_dmrd_v1",
    "admit_dmre_v5",
    "call_attributes",
    "check_acl_chain",
    "check_hops",
    "check_network_id",
    "check_packet_age",
    "check_slot",
    "check_source_server",
    "check_stun",
    "check_tg_filter_v1",
    "check_tg_filter_v5",
    "server_prefix",
]
