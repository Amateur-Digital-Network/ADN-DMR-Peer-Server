# ADN DMR Peer Server - plugin voice events
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

"""Voice plugin events — pure domain types (no Twisted, no I/O)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CallLegContext:
    """Metadata for one voice leg (ingress or bridge observation)."""

    call_family: str  # "GROUP" | "PRIVATE"
    direction: str  # "RX" | "TX"
    origin_system: str
    system_mode: str  # MASTER | PEER | OPENBRIDGE
    peer_id: int
    src_id: int
    dst_id: int
    slot: int
    stream_id: int
    server_id: int
    is_synthetic: bool
    is_proxy_ingress: bool
    pkt_time: float
    obp_source_server_id: int | None = None
    obp_hops: int | None = None
    obp_source_rptr_id: int | None = None
    ber: int | None = None
    rssi: int | None = None
    forwarded_systems: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    def to_metadata_dict(self) -> dict[str, Any]:
        """JSON-serializable metadata for plugin consumers."""
        out: dict[str, Any] = {
            "call_family": self.call_family,
            "direction": self.direction,
            "origin_system": self.origin_system,
            "system_mode": self.system_mode,
            "peer_id": self.peer_id,
            "src_id": self.src_id,
            "dst_id": self.dst_id,
            "slot": self.slot,
            "stream_id": self.stream_id,
            "server_id": self.server_id,
            "is_synthetic": self.is_synthetic,
            "is_proxy_ingress": self.is_proxy_ingress,
            "pkt_time": self.pkt_time,
            "forwarded_systems": list(self.forwarded_systems),
        }
        if self.obp_source_server_id is not None:
            out["obp_source_server_id"] = self.obp_source_server_id
        if self.obp_hops is not None:
            out["obp_hops"] = self.obp_hops
        if self.obp_source_rptr_id is not None:
            out["obp_source_rptr_id"] = self.obp_source_rptr_id
        if self.ber is not None:
            out["ber"] = self.ber
        if self.rssi is not None:
            out["rssi"] = self.rssi
        if self.extra:
            out.update(self.extra)
        return out


@dataclass(frozen=True)
class VoiceCallStart:
    context: CallLegContext


@dataclass(frozen=True)
class VoiceCallFrame:
    context: CallLegContext
    dmrpkt: bytes
    frame_type: int
    dtype_vseq: int


@dataclass(frozen=True)
class VoiceCallEnd:
    context: CallLegContext
    duration_s: float
    frame_count: int = 0


@dataclass(frozen=True)
class UnitDataStart:
    context: CallLegContext
    dtype_vseq: int
    data_label: str
    seq: int
    frame_type: int


@dataclass(frozen=True)
class UnitDataFrame:
    context: CallLegContext
    dmrpkt: bytes
    raw_data: bytes
    dtype_vseq: int
    data_label: str
    seq: int
    frame_type: int
    bits: int


@dataclass(frozen=True)
class UnitDataEnd:
    context: CallLegContext
    duration_s: float
    packet_count: int = 0
