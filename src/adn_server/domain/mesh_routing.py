# ADN DMR Peer Server - domain mesh routing
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

"""Immutable mesh wire messages (Phase 2)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PeerMeshConfig:
    """Peer/session parameters for mesh encode and decode."""

    passphrase: bytes
    server_id: bytes
    wire_ver: int | None = None
    embedded_ver: int = 5


@dataclass(frozen=True, slots=True)
class MeshIngress:
    """Verified voice ingress from a remote mesh peer."""

    codec: str
    voice_frame: bytes
    hops: bytes
    ber: bytes
    rssi: bytes
    source_server: bytes
    source_rptr: bytes
    embedded_ver: int | None = None
    timestamp: bytes = b"\x00" * 8


@dataclass(frozen=True, slots=True)
class MeshEgress:
    """Inner DMR voice packet to wrap for a mesh peer."""

    inner_packet: bytes
    hops: bytes = b"\x01"
    ber: bytes = b"\x00"
    rssi: bytes = b"\x00"
    source_server: bytes = b"\x00\x00\x00\x00"
    source_rptr: bytes = b"\x00\x00\x00\x00"
    timestamp_ns: int | None = None
