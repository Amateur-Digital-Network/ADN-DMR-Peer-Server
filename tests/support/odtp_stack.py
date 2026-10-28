"""ODTP gateway against a real HBP MASTER (HBPProtocol), with a manual clock and no sockets."""

from __future__ import annotations

import hashlib
import struct
from collections import deque
from collections.abc import Callable
from typing import Any

from adn_server.application.odtp.gateway import OdtpGateway
from adn_server.application.odtp.ports import OdtpSettings
from adn_server.domain import odtp
from adn_server.infrastructure.config_normalizer import ensure_system_runtime_config
from adn_server.infrastructure.twisted_adapters.udp_hbp import HBPProtocol

PASSWORD = b"s3cret"
TERMINAL_ID = 2130035
PEER = (TERMINAL_ID * 100 + 98).to_bytes(4, "big")


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0
        self._due: list[tuple[float, int, Callable[..., Any], tuple]] = []
        self._n = 0

    def now(self) -> float:
        return self.t

    def call_later(self, delay: float, fn: Callable[..., Any], *args: Any) -> None:
        self._n += 1
        self._due.append((self.t + delay, self._n, fn, args))

    def advance(self, seconds: float) -> None:
        end = self.t + seconds
        while True:
            due = sorted(d for d in self._due if d[0] <= end)
            if not due:
                break
            self._due.remove(due[0])
            self.t = max(self.t, due[0][0])
            due[0][2](*due[0][3])
        self.t = end


class _Transport:
    def __init__(self, net: Net) -> None:
        self._net = net

    def write(self, data: bytes, addr: tuple[str, int]) -> None:
        self._net.queue.append((addr, data))


class _Link:
    def __init__(self, net: Net, addr: tuple[str, int]) -> None:
        self._net, self.addr = net, addr

    def send(self, packet: bytes) -> None:
        if packet[:4] == b"DMRD":
            self._net.dmrd.append(packet)
        else:
            self._net.to_master(packet, self.addr)

    def close(self) -> None:
        self._net.links.pop(self.addr, None)


class Net:
    """The MASTER and the gateway's links to it; datagrams are delivered in order, never re-entrantly."""

    def __init__(self, passphrase: bytes = PASSWORD) -> None:
        config = {
            "GLOBAL": {"PING_TIME": 5, "MAX_MISSED": 3, "USE_ACL": False},
            "SYSTEMS": {"MASTER-1": {"MODE": "MASTER", "ENABLED": True, "MAX_PEERS": 50,
                                     "PASSPHRASE": passphrase, "OPTIONS": "TS2=9990;"}},
        }
        ensure_system_runtime_config(config)
        self.master = HBPProtocol("MASTER-1", config, router=_AllowAll())
        self.master.transport = _Transport(self)
        self.queue: deque[tuple[tuple[str, int], bytes]] = deque()
        self.links: dict[tuple[str, int], Callable[[bytes], None]] = {}
        self.sent: list[bytes] = []  # control packets the MASTER got
        self.dmrd: list[bytes] = []  # DMRD frames the terminals sent
        self._port = 40000
        self._busy = False

    def open(self, on_datagram: Callable[[bytes], None]) -> _Link:
        self._port += 1
        addr = ("127.0.0.1", self._port)
        self.links[addr] = on_datagram
        return _Link(self, addr)

    def to_master(self, packet: bytes, addr: tuple[str, int]) -> None:
        self.sent.append(packet)
        self.queue.append((None, (packet, addr)))  # delivered by pump(), as the reactor would

    def pump(self) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            while self.queue:
                addr, data = self.queue.popleft()
                if addr is None:
                    self.master.datagramReceived(*data)
                elif addr in self.links:
                    self.links[addr](data)
        finally:
            self._busy = False

    def peer(self, peer_id: bytes) -> dict | None:
        return self.master._peers.get(peer_id)

    def to_peer(self, peer_id: bytes, frames: list[bytes]) -> None:
        """The MASTER sends frames to one connected peer."""
        addr = self.master._peers[peer_id]["SOCKADDR"]
        for frame in frames:
            self.queue.append((addr, frame))
        self.pump()

    def options(self) -> list[bytes]:
        return [p[8:] for p in self.sent if p[:4] == b"RPTO"]


class _AllowAll:
    def acl_check(self, *_a: Any) -> bool:
        return True


class Terminal:
    def __init__(self, gw: OdtpGateway, port: int) -> None:
        self.gw, self.addr, self.seq, self.inbox = gw, ("198.51.100.7", port), 0, []

    def send(self, msg_type: int, payload: bytes = b"") -> None:
        self.seq += 1
        self.gw.datagram(odtp.pack(msg_type, self.seq, payload), self.addr)
        self.gw.links.pump()

    def got(self, msg_type: int) -> list[bytes]:
        return [p for t, p in self.inbox if t == msg_type]

    def login(self, number: int = TERMINAL_ID, password: bytes = PASSWORD) -> None:
        self.send(odtp.KEEP_ALIVE, struct.pack("<IB", number, odtp.SERVICE_OPEN_TERMINAL) + b"Z3DMR 1.9.9")
        for salt in self.got(odtp.CHALLENGE)[-1:]:
            self.send(odtp.AUTHENTICATION, hashlib.sha256(salt + password).digest())

    def subscribe(self, tg: int, kind: int = odtp.SESSION_TYPE_GROUP_VOICE, add: bool = True) -> None:
        self.send(odtp.SUBSCRIPTION if add else odtp.CANCELLING, struct.pack("<II", kind, tg))


class _Socket:
    def __init__(self) -> None:
        self.terminals: dict[tuple[str, int], Terminal] = {}

    def send(self, data: bytes, addr: tuple[str, int]) -> None:
        msg = odtp.unpack(data)
        if addr in self.terminals and msg is not None:
            self.terminals[addr].inbox.append((msg[0], msg[3]))


class Stack:
    def __init__(self, settings: OdtpSettings | None = None, callsigns: Callable[[int], str] = lambda _i: "C31AG") -> None:
        self.net = Net()
        self.clock = FakeClock()
        self.socket = _Socket()
        self.gw = OdtpGateway(settings or OdtpSettings(), self.socket, self.net, self.clock, callsigns)

    def terminal(self, port: int = 50000) -> Terminal:
        term = Terminal(self.gw, port)
        self.socket.terminals[term.addr] = term
        return term

    def advance(self, seconds: float) -> None:
        self.clock.advance(seconds)
        self.net.pump()
