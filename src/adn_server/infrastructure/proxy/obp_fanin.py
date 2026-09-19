# ADN DMR Peer Server - infrastructure proxy obp fanin
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

"""UDP fan-in for OPENBRIDGE: demux by NETWORK_ID and optional legacy PORT."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from twisted.internet.protocol import DatagramProtocol

from adn_server.infrastructure.hbp_constants import BCKA, BCSQ, BCST, BCVE, DMRD, DMRE
from adn_server.infrastructure.mesh.obp_v1 import verify_bcka, verify_bcsq, verify_bcst, verify_bcve
from adn_server.infrastructure.udp_rcvbuf import apply_udp_rcvbuf, udp_rcvbuf_bytes

_logger = logging.getLogger(__name__)


class _DatagramWriter(Protocol):
    def write(self, data: bytes, addr: tuple[str, int]) -> None:
        ...


class _ObpReceiver(Protocol):
    def _obp_datagram_received(self, data: bytes, sockaddr: tuple[str, int]) -> None:
        ...


class ObpIngressReplyTransport:
    """Route OBP egress through this bridge's own socket.

    Priority: pinned socket (the bridge's legacy listener, when bound) > socket that
    last received for this bridge > shared fan-in. Leaving egress on the shared fan-in
    makes remote peers learn LISTEN_PORT as our source and reply there, where control
    frames (no NETWORK_ID) can only be told apart by passphrase.
    """

    def __init__(self, fallback: _DatagramWriter) -> None:
        self._fallback = fallback
        self._active: _DatagramWriter | None = None
        self._pinned: _DatagramWriter | None = None

    def pin(self, transport: _DatagramWriter) -> None:
        """Bind egress to this bridge's own socket."""
        self._pinned = transport

    def note_ingress(self, transport: _DatagramWriter) -> None:
        self._active = transport

    def write(self, data: bytes, addr: tuple[str, int]) -> None:
        transport = self._pinned or self._active or self._fallback
        transport.write(data, addr)


class InProcessObpSink:
    """Deliver datagrams to an OPENBRIDGE HBPProtocol without a UDP hop."""

    def __init__(self, hbp: _ObpReceiver) -> None:
        self._hbp = hbp

    def inject(self, data: bytes, client_addr: tuple[str, int]) -> None:
        self._hbp._obp_datagram_received(data, client_addr)


@dataclass
class ObpBridgeEntry:
    system_name: str
    network_id: bytes
    passphrase: bytes
    sink: InProcessObpSink
    reply_transport: ObpIngressReplyTransport
    legacy_port: int | None = None
    # Live SYSTEMS.<name> dict; used to tell bridges apart by peer address.
    sys_cfg: dict[str, Any] | None = None


@dataclass
class ObpBridgeRegistry:
    """NETWORK_ID and legacy PORT lookup for OBP fan-in demux."""

    by_network_id: dict[bytes, str] = field(default_factory=dict)
    by_legacy_port: dict[int, str] = field(default_factory=dict)
    bridges: dict[str, ObpBridgeEntry] = field(default_factory=dict)

    def register(self, entry: ObpBridgeEntry) -> None:
        self.bridges[entry.system_name] = entry
        self.by_network_id[entry.network_id] = entry.system_name
        if entry.legacy_port is not None:
            self.by_legacy_port[entry.legacy_port] = entry.system_name

    def bridges_by_peer(self, addr: tuple[str, int] | None) -> list[tuple[str, ObpBridgeEntry]]:
        """Bridges ordered by how well their configured peer matches ``addr``.

        Exact IP:port first, then same IP (a peer that answers from another local port,
        e.g. its own fan-in), then the rest in registration order.
        """
        items = list(self.bridges.items())
        if addr is None:
            return items
        exact: list[tuple[str, ObpBridgeEntry]] = []
        same_host: list[tuple[str, ObpBridgeEntry]] = []
        rest: list[tuple[str, ObpBridgeEntry]] = []
        for name, entry in items:
            cfg = entry.sys_cfg or {}
            sock = cfg.get("TARGET_SOCK")
            host = sock[0] if isinstance(sock, tuple) else cfg.get("TARGET_IP")
            port = sock[1] if isinstance(sock, tuple) else cfg.get("TARGET_PORT")
            if host == addr[0] and port == addr[1]:
                exact.append((name, entry))
            elif host == addr[0]:
                same_host.append((name, entry))
            else:
                rest.append((name, entry))
        return exact + same_host + rest

    def clear(self) -> None:
        self.by_network_id.clear()
        self.by_legacy_port.clear()
        self.bridges.clear()


class ObpFanInDemux:
    """Shared demux handler for one or more OBP proxy UDP listeners."""

    def __init__(
        self,
        registry: ObpBridgeRegistry,
        *,
        debug: bool = False,
        logger: logging.Logger | None = None,
    ) -> None:
        self._registry = registry
        self.debug = debug
        self._log = logger or _logger

    def deliver(
        self,
        data: bytes,
        addr: tuple[str, int],
        *,
        local_port: int,
        transport: _DatagramWriter,
    ) -> None:
        host, port = addr
        system_name = self._registry.by_legacy_port.get(local_port)
        if system_name is None:
            system_name = self._lookup_by_network_id(data)
        if system_name is None:
            system_name = self._lookup_control(data, addr)
        if system_name is None:
            if self.debug:
                self._log.debug(
                    "(OBP_PROXY) dropped packet from %s:%s len=%d local_port=%s",
                    host,
                    port,
                    len(data),
                    local_port,
                )
            return
        entry = self._registry.bridges.get(system_name)
        if entry is None:
            return
        if self.debug:
            self._log.debug(
                "(OBP_PROXY) RX %s from %s:%s len=%d -> %s",
                data[:4],
                host,
                port,
                len(data),
                system_name,
            )
        entry.reply_transport.note_ingress(transport)
        entry.sink.inject(data, addr)

    def _lookup_by_network_id(self, data: bytes) -> str | None:
        if len(data) < 15:
            return None
        opcode = data[:4]
        if opcode not in (DMRD, DMRE):
            return None
        network_id = data[11:15]
        return self._registry.by_network_id.get(network_id)

    def _lookup_control(self, data: bytes, addr: tuple[str, int] | None = None) -> str | None:
        """Resolve a control frame (BCKA/BCSQ/BCST/BCVE) to a bridge.

        These frames carry no NETWORK_ID, so the only in-band discriminator is the
        passphrase — and a mesh where every bridge shares one passphrase (as ADN
        Systems does) would always resolve to whichever bridge is registered first.
        Try the bridges whose peer address matches the datagram source before falling
        back to the plain passphrase scan.
        """
        if len(data) < 4:
            return None
        opcode = data[:4]
        for name, entry in self._registry.bridges_by_peer(addr):
            passphrase = entry.passphrase
            if opcode == BCKA and verify_bcka(data, passphrase):
                return name
            if opcode == BCSQ and verify_bcsq(data, passphrase) is not None:
                return name
            if opcode == BCST and verify_bcst(data, passphrase):
                return name
            if opcode == BCVE:
                ok, _ver = verify_bcve(data, passphrase)
                if ok:
                    return name
        return None


class ObpFanInProtocol(DatagramProtocol):
    """Thin UDP listener delegating to shared OBP demux."""

    def __init__(self, demux: ObpFanInDemux) -> None:
        self._demux = demux

    def datagramReceived(self, data: bytes, addr: tuple[str, int]) -> None:
        transport = self.transport
        if transport is None:
            return
        local_port = int(transport.getHost().port)
        self._demux.deliver(data, addr, local_port=local_port, transport=transport)


def listen_obp_fanin(
    reactor: Any,
    listen_ip: str,
    listen_port: int,
    demux: ObpFanInDemux,
    *,
    config: dict[str, Any] | None = None,
    udp_rcvbuf: int | None = None,
    logger: logging.Logger | None = None,
) -> tuple[ObpFanInProtocol, Any]:
    """Bind one OBP proxy UDP port and return ``(protocol, udp_port)``."""
    log = logger or _logger
    proto = ObpFanInProtocol(demux)
    udp_port = reactor.listenUDP(listen_port, proto, interface=listen_ip or "0.0.0.0")
    buf_size = udp_rcvbuf if udp_rcvbuf is not None else udp_rcvbuf_bytes(config)
    apply_udp_rcvbuf(udp_port.socket, buf_size, label="OBP_PROXY", logger=log)
    return proto, udp_port


__all__ = [
    "InProcessObpSink",
    "ObpBridgeEntry",
    "ObpBridgeRegistry",
    "ObpFanInDemux",
    "ObpFanInProtocol",
    "ObpIngressReplyTransport",
    "listen_obp_fanin",
]
