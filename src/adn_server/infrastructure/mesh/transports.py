# ADN DMR Peer Server - infrastructure mesh transports
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

"""Built-in ``PeerTransport`` implementations wrapping mesh wire codecs."""

from __future__ import annotations

import time

from adn_server.application.ports import PeerTransport
from adn_server.domain.mesh_routing import MeshEgress, MeshIngress, PeerMeshConfig
from adn_server.infrastructure.hbp_constants import DMRD, DMRE
from adn_server.infrastructure.mesh.dmre_v5 import build_dmre, parse_dmre_trailer, verify_dmre_mac
from adn_server.infrastructure.mesh.obp_v1 import build_dmrd_v1, verify_dmrd_v1


class ObpV1PeerTransport:
    """OpenBridge DMRD v1 (53 bytes + HMAC-SHA1)."""

    name = "obp_v1"

    def try_decode(self, datagram: bytes, config: PeerMeshConfig) -> MeshIngress | None:
        if datagram[:4] != DMRD:
            return None
        verified = verify_dmrd_v1(datagram, config.passphrase)
        if verified is None:
            return None
        return MeshIngress(
            codec=self.name,
            voice_frame=verified.payload,
            hops=b"",
            ber=b"\x00",
            rssi=b"\x00",
            source_server=config.server_id,
            source_rptr=b"\x00\x00\x00\x00",
        )

    def encode(self, egress: MeshEgress, config: PeerMeshConfig) -> bytes | None:
        if egress.inner_packet[:4] != DMRD:
            return None
        return build_dmrd_v1(egress.inner_packet, config.server_id, config.passphrase)


class DmreV5PeerTransport:
    """FreeBridge DMRE v4/v5 (BLAKE2b)."""

    name = "dmre_v5"

    def try_decode(self, datagram: bytes, config: PeerMeshConfig) -> MeshIngress | None:
        if datagram[:4] != DMRE:
            return None
        trailer = parse_dmre_trailer(datagram)
        if trailer is None:
            return None
        if not verify_dmre_mac(datagram, config.passphrase, trailer.hash_len):
            return None
        return MeshIngress(
            codec=self.name,
            voice_frame=datagram[:53],
            hops=trailer.hops,
            ber=trailer.ber,
            rssi=trailer.rssi,
            source_server=trailer.source_server,
            source_rptr=trailer.source_rptr,
            embedded_ver=trailer.embedded_version,
            timestamp=trailer.timestamp,
        )

    def encode(self, egress: MeshEgress, config: PeerMeshConfig) -> bytes | None:
        if egress.inner_packet[:4] != DMRD:
            return None
        extended = config.wire_ver is not None and config.wire_ver > 4
        return build_dmre(
            egress.inner_packet,
            server_id=config.server_id,
            ber=egress.ber,
            rssi=egress.rssi,
            embedded_ver=config.embedded_ver,
            timestamp_ns=egress.timestamp_ns if egress.timestamp_ns is not None else time.time_ns(),
            source_server=egress.source_server,
            source_rptr=egress.source_rptr,
            hops=egress.hops,
            passphrase=config.passphrase,
            extended_layout=extended,
        )


def default_peer_transports() -> dict[str, PeerTransport]:
    return {
        ObpV1PeerTransport.name: ObpV1PeerTransport(),
        DmreV5PeerTransport.name: DmreV5PeerTransport(),
    }
