# ADN DMR Peer Server - tests domain mesh engine
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

"""The OBP ingress engine: a frame in, a list of things to do out."""

from __future__ import annotations

import pytest

from adn_server.domain import bytes_3, bytes_4
from adn_server.domain.mesh_admission import AclRules, AdmissionContext
from adn_server.domain.mesh_engine import (
    BridgePolicy,
    Deliver,
    Log,
    NoteStream,
    Reject,
    RequestVersion,
    StoreTalkerAlias,
    accepts_source,
    ingest_dmrd_v1,
    ingest_dmre_v5,
    reject_v1_protocol,
    server_id_bytes,
)
from adn_server.domain.mesh_routing import MeshIngress
from adn_server.domain.mesh_session import ObpBridgeSession

_SYSTEM = "OBP-FR"
_NETWORK = bytes_4(20840)
_SERVER = bytes_4(21310)
_PEER = ("82.65.127.86", 62201)
_STREAM = bytes_4(0xAABBCCDD)
_NOW = 1_800_000_000.0


def _voice(*, src: int = 2130003, dst: int = 214, bits: int = 0x00, peer: bytes = _NETWORK) -> bytes:
    return b"".join(
        [
            b"DMRD",
            bytes([7]),
            bytes_3(src),
            bytes_3(dst),
            peer,
            bytes([bits]),
            _STREAM,
            bytes(range(33)),
        ]
    )


def _ingress(frame: bytes, *, codec: str = "obp_v1", hops: bytes = b"\x00", source_server: bytes = _SERVER) -> MeshIngress:
    return MeshIngress(
        codec=codec,
        voice_frame=frame,
        hops=hops,
        ber=b"\x02",
        rssi=b"\x03",
        source_server=source_server,
        source_rptr=bytes_4(4321),
        embedded_ver=5,
    )


def _policy(**overrides) -> BridgePolicy:
    base = {
        "system": _SYSTEM,
        "network_id": _NETWORK,
        "proto_ver": 1,
        "relax_checks": True,
        "server_id": _SERVER,
        "admission": AdmissionContext(),
    }
    base.update(overrides)
    return BridgePolicy(**base)


def _session() -> ObpBridgeSession:
    return ObpBridgeSession(system_name=_SYSTEM, configured_peer=_PEER)


def _of(effects, kind):
    return [e for e in effects if isinstance(e, kind)]


# --- policy ------------------------------------------------------------------


@pytest.mark.parametrize(("ver", "rejects"), [(1, False), (5, True), (4, True), (None, True)])
def test_a_link_above_version_1_refuses_v1_frames(ver, rejects) -> None:
    assert _policy(proto_ver=ver).rejects_v1 is rejects


def test_the_version_complaint_names_the_configured_version_and_asks_for_bcve() -> None:
    effects = reject_v1_protocol(_STREAM, policy=_policy(proto_ver=5))
    reject, version = effects
    assert isinstance(reject, Reject)
    assert reject.reason == "proto-version"
    assert reject.rejection.quench is False
    assert reject.rejection.args[1] == 5
    assert isinstance(version, RequestVersion)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(bytes_4(21310), bytes_4(21310)), (21310, bytes_4(21310)), ("21310", b"\x00\x00\x00\x00")],
)
def test_the_server_id_reaches_the_wire_as_four_bytes(value, expected) -> None:
    assert server_id_bytes(value) == expected


def test_a_frame_from_anywhere_needs_relax_checks() -> None:
    session = _session()
    strict = _policy(relax_checks=False)
    assert accepts_source(_PEER, policy=strict, session=session) is True
    assert accepts_source(("9.9.9.9", 1), policy=strict, session=session) is False
    assert accepts_source(("9.9.9.9", 1), policy=_policy(), session=session) is True


# --- DMRD v1 -----------------------------------------------------------------


def test_a_group_call_is_announced_noted_and_delivered() -> None:
    session = _session()
    effects = ingest_dmrd_v1(_ingress(_voice(bits=0x21)), _PEER, policy=_policy(), session=session, now=_NOW)
    assert [type(e) for e in effects] == [Log, NoteStream, Deliver]
    announcement = _of(effects, Log)[0]
    assert "CALL RX (OBP)" in announcement.message
    assert announcement.args == (_SYSTEM, 2130003, 214, 1)


def test_delivery_carries_the_v1_defaults() -> None:
    session = _session()
    effects = ingest_dmrd_v1(_ingress(_voice()), _PEER, policy=_policy(), session=session, now=_NOW)
    deliver = _of(effects, Deliver)[0]
    assert (deliver.rf_src, deliver.dst_id, deliver.stream_id) == (bytes_3(2130003), bytes_3(214), _STREAM)
    assert (deliver.seq, deliver.slot, deliver.call_type) == (7, 1, "group")
    # v1 carries no mesh envelope: our own server id, no hops, no ber/rssi
    assert deliver.hops == b""
    assert deliver.source_server == _SERVER
    assert (deliver.ber, deliver.rssi, deliver.source_rptr) == (b"\x00", b"\x00", b"\x00\x00\x00\x00")


def test_a_voice_burst_keeps_its_talker_alias() -> None:
    session = _session()
    effects = ingest_dmrd_v1(_ingress(_voice(bits=0x01)), _PEER, policy=_policy(), session=session, now=_NOW)
    alias = _of(effects, StoreTalkerAlias)[0]
    assert alias.dtype_vseq == 1
    assert alias.burst == bytes(range(33))


def test_a_frame_from_another_network_is_refused() -> None:
    session = _session()
    effects = ingest_dmrd_v1(
        _ingress(_voice(peer=bytes_4(26811))), _PEER, policy=_policy(), session=session, now=_NOW
    )
    assert [type(e) for e in effects] == [Reject]
    assert effects[0].reason == "network-id-mismatch"


def test_a_talkgroup_that_must_stay_home_is_refused_and_quenched() -> None:
    session = _session()
    effects = ingest_dmrd_v1(_ingress(_voice(dst=9)), _PEER, policy=_policy(), session=session, now=_NOW)
    assert effects[0].reason == "tg-filter"
    assert effects[0].rejection.quench is True
    assert effects[0].dst_id == bytes_3(9)


def test_an_unaccepted_source_is_not_the_engine_s_business() -> None:
    session = _session()
    effects = ingest_dmrd_v1(
        _ingress(_voice()), ("9.9.9.9", 40000), policy=_policy(relax_checks=False), session=session, now=_NOW
    )
    assert effects is None


def test_a_delivered_frame_counts_as_a_keepalive() -> None:
    session = _session()
    ingest_dmrd_v1(_ingress(_voice()), _PEER, policy=_policy(), session=session, now=_NOW)
    assert session.last_keepalive == _NOW


def test_a_refused_frame_is_not_a_keepalive() -> None:
    session = _session()
    ingest_dmrd_v1(_ingress(_voice(dst=9)), _PEER, policy=_policy(), session=session, now=_NOW)
    assert session.keepalive_seen is False


def test_the_acls_reach_the_engine_through_the_policy() -> None:
    session = _session()
    admission = AdmissionContext(
        acl_check=lambda target, _acl: target != bytes_3(2130003),
        system_rules=AclRules(enabled=True),
    )
    effects = ingest_dmrd_v1(
        _ingress(_voice()), _PEER, policy=_policy(admission=admission), session=session, now=_NOW
    )
    assert effects[0].reason == "system-sub-acl"


# --- DMRE v5 -----------------------------------------------------------------


def _v5(session, *, frame: bytes | None = None, hops: bytes = b"\x02", age: float = 0.0, **policy_kw):
    return ingest_dmre_v5(
        _ingress(frame or _voice(), codec="dmre_v5", hops=hops, source_server=bytes_4(2084)),
        _PEER,
        policy=_policy(proto_ver=5, **policy_kw),
        session=session,
        timestamp_ns=int((_NOW - age) * 1_000_000_000),
        now=_NOW,
    )


def test_a_v5_frame_is_delivered_with_its_envelope() -> None:
    session = _session()
    deliver = _of(_v5(session), Deliver)[0]
    assert deliver.frame[:4] == b"DMRD"  # routing speaks DMRD, the mesh header is unwrapped
    assert deliver.hops == b"\x03"  # one more hop than it arrived with
    assert deliver.source_server == bytes_4(2084)
    assert (deliver.ber, deliver.rssi, deliver.source_rptr) == (b"\x02", b"\x03", bytes_4(4321))


def test_a_v5_frame_is_always_routed_as_slot_1() -> None:
    """OpenBridge streams are TS1; DMRE can still carry TS2 in its bits byte."""
    session = _session()
    deliver = _of(_v5(session, frame=_voice(bits=0x80)), Deliver)[0]
    assert deliver.slot == 1


def test_a_late_v5_frame_is_refused() -> None:
    session = _session()
    effects = _v5(session, age=9.0)
    assert effects[0].reason == "stale-packet"


def test_a_looping_v5_frame_is_refused() -> None:
    session = _session()
    effects = _v5(session, hops=b"\x0a")
    assert effects[0].reason == "max-hops"


def test_an_unaccepted_v5_source_is_not_the_engine_s_business() -> None:
    session = _session()
    effects = ingest_dmre_v5(
        _ingress(_voice(), codec="dmre_v5"),
        ("9.9.9.9", 40000),
        policy=_policy(proto_ver=5, relax_checks=False),
        session=session,
        timestamp_ns=int(_NOW * 1_000_000_000),
        now=_NOW,
    )
    assert effects is None


def test_a_v5_frame_from_another_network_is_refused_by_name() -> None:
    session = _session()
    effects = _v5(session, frame=_voice(peer=bytes_4(26811)))
    assert effects[0].reason == "network-id-mismatch"
    assert "DMRE" in effects[0].rejection.message
