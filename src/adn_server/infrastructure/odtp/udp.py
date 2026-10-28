# ADN DMR Peer Server - ODTP gateway UDP adapters
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

"""Twisted adapters of the ODTP gateway: the terminals' socket, one UDP link to the MASTER per terminal, the clock."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from twisted.internet import reactor
from twisted.internet.protocol import DatagramProtocol

from ...application.odtp.gateway import OdtpGateway

logger = logging.getLogger(__name__)


class OdtpListener(DatagramProtocol):
    """The ODTP port: every datagram goes to the gateway."""

    def __init__(self) -> None:
        self.gateway: OdtpGateway | None = None

    def datagramReceived(self, data: bytes, addr: tuple[str, int]) -> None:
        if self.gateway is not None:
            self.gateway.datagram(data, addr)

    def send(self, data: bytes, addr: tuple[str, int]) -> None:
        if self.transport is not None:
            self.transport.write(data, addr)


class _MasterProtocol(DatagramProtocol):
    def __init__(self, master: tuple[str, int], on_datagram: Callable[[bytes], None]) -> None:
        self._master = master
        self._on_datagram = on_datagram

    def startProtocol(self) -> None:
        self.transport.connect(*self._master)

    def datagramReceived(self, data: bytes, _addr: tuple[str, int]) -> None:
        self._on_datagram(data)


class _MasterLink:
    def __init__(self, protocol: _MasterProtocol, port: Any) -> None:
        self._protocol = protocol
        self._port = port

    def send(self, packet: bytes) -> None:
        if self._protocol.transport is not None:
            self._protocol.transport.write(packet)

    def close(self) -> None:
        self._port.stopListening()


class TwistedMasterLinks:
    """Each terminal logs in from its own UDP socket, as a hotspot would."""

    def __init__(self, master_host: str, master_ip: str, master_port: int) -> None:
        self._host = master_host
        self._master = (master_ip, master_port)

    def refresh(self) -> None:
        """Resolve the MASTER's name again, as the PEER mode does; new links use the new address."""
        d = reactor.resolve(self._host)
        d.addCallback(self._resolved)
        d.addErrback(lambda failure: logger.info("(ODTP) cannot resolve %s: %s", self._host, failure.getErrorMessage()))

    def _resolved(self, ip: str) -> None:
        if ip != self._master[0]:
            logger.info("(ODTP) MASTER %s is now %s", self._host, ip)
            self._master = (ip, self._master[1])

    def open(self, on_datagram: Callable[[bytes], None]) -> _MasterLink:
        protocol = _MasterProtocol(self._master, on_datagram)
        return _MasterLink(protocol, reactor.listenUDP(0, protocol))


class ReactorClock:
    def now(self) -> float:
        return time.monotonic()

    def call_later(self, delay: float, fn: Callable[..., Any], *args: Any) -> Any:
        return reactor.callLater(delay, fn, *args)
