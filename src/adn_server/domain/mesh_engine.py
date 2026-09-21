# ADN DMR Peer Server - domain mesh engine
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

"""What an OpenBridge leg does with a verified frame, as effects.

The engine takes a decoded frame, the link's session and the policy that
applies to it, and answers with a list of things to do: deliver this to
routing, quench that stream, log this line. It reads no configuration, touches
no socket and calls no logger; the adapter that owns those executes what comes
back, in order.

Two consequences worth the move. A datagram can be replayed through the engine
outside the server — from a capture, in a test, on a laptop — and the answer is
the same list. And every drop carries a ``reason``, so the bridge can count and
trace what it refuses instead of leaving it in the log text.

The engine updates the session it is handed (that is the link's state, and a
frame is what moves it); everything that leaves the process is an effect.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .mesh_admission import (
    AdmissionContext,
    ObpFrame,
    Rejection,
    admit_dmrd_v1,
    admit_dmre_v5,
    call_attributes,
    check_network_id,
)
from .mesh_admission import MeshEnvelope as AdmissionEnvelope
from .hbp_protocol import HBPF_DATA_SYNC, HBPF_SLT_VHEAD
from .mesh_routing import MeshIngress
from .mesh_session import ObpBridgeSession
from .value_objects import bytes_4, int_id

TALKER_ALIAS_VSEQ = (1, 2, 3, 4)


# --- effects -----------------------------------------------------------------


@dataclass(frozen=True)
class Reject:
    """Drop this frame: log it (once per stream), quench the peer if asked."""

    rejection: Rejection
    dst_id: bytes
    stream_id: bytes

    @property
    def reason(self) -> str:
        return self.rejection.reason


@dataclass(frozen=True)
class Log:
    """One log line, already carrying its arguments."""

    level: int
    message: str
    args: tuple[Any, ...] = ()


@dataclass(frozen=True)
class RequestVersion:
    """Tell the peer which protocol version we speak (BCVE)."""


@dataclass(frozen=True)
class NoteStream:
    """Record that this peer is carrying this stream."""

    peer_id: bytes
    rf_src: bytes
    stream_id: bytes


@dataclass(frozen=True)
class StoreTalkerAlias:
    """Keep the talker-alias burst embedded in a voice frame."""

    peer_id: bytes
    rf_src: bytes
    stream_id: bytes
    dtype_vseq: int
    burst: bytes


@dataclass(frozen=True)
class Deliver:
    """Hand the frame to routing, with the mesh fields it needs."""

    peer_id: bytes
    rf_src: bytes
    dst_id: bytes
    seq: int
    slot: int
    call_type: str
    frame_type: int
    dtype_vseq: int
    stream_id: bytes
    frame: bytes
    hops: bytes = b""
    source_server: bytes = b"\x00\x00\x00\x00"
    ber: bytes = b"\x00"
    rssi: bytes = b"\x00"
    source_rptr: bytes = b"\x00\x00\x00\x00"


Effect = Reject | Log | RequestVersion | NoteStream | StoreTalkerAlias | Deliver


@dataclass(frozen=True)
class BridgePolicy:
    """Everything about this bridge the engine needs, read once per frame."""

    system: str
    network_id: bytes = b""
    proto_ver: Any = 5
    relax_checks: bool = True
    server_id: bytes = b"\x00\x00\x00\x00"
    admission: AdmissionContext = field(default_factory=AdmissionContext)

    @property
    def rejects_v1(self) -> bool:
        """True when this link is configured above protocol version 1."""
        ver = 5 if self.proto_ver is None else self.proto_ver
        return ver > 1


def server_id_bytes(value: Any) -> bytes:
    """GLOBAL SERVER_ID as the four bytes the mesh puts on the wire."""
    if isinstance(value, bytes) and len(value) >= 4:
        return value
    if isinstance(value, int):
        return bytes_4(value & 0xFFFFFFFF)
    return b"\x00\x00\x00\x00"


# --- ingress -----------------------------------------------------------------


def reject_v1_protocol(stream_id: bytes, *, policy: BridgePolicy) -> list[Effect]:
    """A v1 frame on a link configured for a later protocol version."""
    return [
        Reject(
            Rejection(
                reason="proto-version",
                message="(%s) *ProtoControl* Version 1 protocol prohibited by PROTO_VER, Ver: %s",
                args=(policy.system, policy.proto_ver),
                level=logging.WARNING,
                quench=False,
            ),
            dst_id=b"",
            stream_id=stream_id,
        ),
        RequestVersion(),
    ]


def accepts_source(
    addr: tuple[str, int] | None, *, policy: BridgePolicy, session: ObpBridgeSession
) -> bool:
    """A frame counts as ours when it comes from the peer.

    RELAX_CHECKS widens that to any address, which is how a peer on a dynamic IP
    keeps working. It does not widen it when DNS owns the peer: there the name is
    the identity and only a re-resolution may move it, so a second host holding
    the same passphrase is not mistaken for the peer.
    """
    if addr == session.peer:
        return True
    return bool(policy.relax_checks) and not session.dns_anchored


def _delivery_effects(
    frame: ObpFrame,
    data: bytes,
    *,
    peer_id: bytes,
    frame_type: int,
    dtype_vseq: int,
    deliver: Deliver,
) -> list[Effect]:
    effects: list[Effect] = []
    if frame.call_type == "group" and frame_type == HBPF_DATA_SYNC and dtype_vseq == HBPF_SLT_VHEAD:
        effects.append(
            Log(
                logging.INFO,
                "(%s) CALL RX (OBP) src %s -> TG %s slot %s",
                (frame.system, int_id(frame.rf_src), int_id(frame.dst_id), frame.slot),
            )
        )
    effects.append(NoteStream(peer_id=peer_id, rf_src=frame.rf_src, stream_id=frame.stream_id))
    if (
        frame.call_type in ("group", "vcsbk")
        and frame_type != HBPF_DATA_SYNC
        and dtype_vseq in TALKER_ALIAS_VSEQ
        and len(data) >= 53
    ):
        effects.append(
            StoreTalkerAlias(
                peer_id=peer_id,
                rf_src=frame.rf_src,
                stream_id=frame.stream_id,
                dtype_vseq=dtype_vseq,
                burst=data[20:53],
            )
        )
    effects.append(deliver)
    return effects


def ingest_dmrd_v1(
    ingress: MeshIngress,
    addr: tuple[str, int] | None,
    *,
    policy: BridgePolicy,
    session: ObpBridgeSession,
    now: float,
) -> list[Effect] | None:
    """A verified DMRD v1 frame. ``None`` means the source was not accepted."""
    if not accepts_source(addr, policy=policy, session=session):
        return None
    data = ingress.voice_frame
    stream_id = data[16:20]
    dst_id = data[8:11]
    peer_id = data[11:15]

    rejection = check_network_id(
        policy.system, stream_id, expected=policy.network_id, received=peer_id
    )
    if rejection is not None:
        return [Reject(rejection, dst_id, stream_id)]

    attrs = call_attributes(data[15])
    frame = ObpFrame(
        system=policy.system,
        stream_id=stream_id,
        rf_src=data[5:8],
        dst_id=dst_id,
        slot=attrs.slot,
        call_type=attrs.call_type,
    )
    rejection = admit_dmrd_v1(frame, policy.admission)
    if rejection is not None:
        return [Reject(rejection, dst_id, stream_id)]

    effects = _delivery_effects(
        frame,
        data,
        peer_id=peer_id,
        frame_type=attrs.frame_type,
        dtype_vseq=attrs.dtype_vseq,
        deliver=Deliver(
            peer_id=peer_id,
            rf_src=frame.rf_src,
            dst_id=dst_id,
            seq=data[4],
            slot=attrs.slot,
            call_type=attrs.call_type,
            frame_type=attrs.frame_type,
            dtype_vseq=attrs.dtype_vseq,
            stream_id=stream_id,
            frame=data,
            hops=b"",
            source_server=policy.server_id,
        ),
    )
    session.note_keepalive(now)
    return effects


def ingest_dmre_v5(
    ingress: MeshIngress,
    addr: tuple[str, int] | None,
    *,
    policy: BridgePolicy,
    session: ObpBridgeSession,
    timestamp_ns: int,
    now: float,
) -> list[Effect] | None:
    """A verified DMRE v5 frame. ``None`` means the source was not accepted."""
    if not accepts_source(addr, policy=policy, session=session):
        return None
    data = ingress.voice_frame
    stream_id = data[16:20]
    dst_id = data[8:11]
    peer_id = data[11:15]

    rejection = check_network_id(
        policy.system, stream_id, expected=policy.network_id, received=peer_id, dmre=True
    )
    if rejection is not None:
        return [Reject(rejection, dst_id, stream_id)]

    attrs = call_attributes(data[15])
    frame = ObpFrame(
        system=policy.system,
        stream_id=stream_id,
        rf_src=data[5:8],
        dst_id=dst_id,
        # OpenBridge streams are TS1: DMRD v1 rejects anything else and DMRE
        # can still carry TS2 in its bits, so normalize before routing sees it.
        slot=1,
        call_type=attrs.call_type,
    )
    hops = ingress.hops if isinstance(ingress.hops, int) else int.from_bytes(ingress.hops, "big")
    envelope = AdmissionEnvelope(
        source_server=int.from_bytes(ingress.source_server, "big"),
        hops=hops,
        timestamp_ns=timestamp_ns,
        source_server_id=ingress.source_server,
    )
    rejection = admit_dmre_v5(frame, envelope, policy.admission, now=now)
    if rejection is not None:
        return [Reject(rejection, dst_id, stream_id)]

    effects = _delivery_effects(
        frame,
        data,
        peer_id=peer_id,
        frame_type=attrs.frame_type,
        dtype_vseq=attrs.dtype_vseq,
        deliver=Deliver(
            peer_id=peer_id,
            rf_src=frame.rf_src,
            dst_id=dst_id,
            seq=data[4],
            slot=1,
            call_type=attrs.call_type,
            frame_type=attrs.frame_type,
            dtype_vseq=attrs.dtype_vseq,
            stream_id=stream_id,
            frame=b"DMRD" + data[4:],
            hops=(hops + 1).to_bytes(1, "big"),
            source_server=ingress.source_server,
            ber=ingress.ber,
            rssi=ingress.rssi,
            source_rptr=ingress.source_rptr,
        ),
    )
    session.note_keepalive(now)
    return effects


__all__ = [
    "BridgePolicy",
    "accepts_source",
    "Deliver",
    "Effect",
    "Log",
    "NoteStream",
    "Reject",
    "RequestVersion",
    "StoreTalkerAlias",
    "ingest_dmrd_v1",
    "ingest_dmre_v5",
    "reject_v1_protocol",
    "server_id_bytes",
]
