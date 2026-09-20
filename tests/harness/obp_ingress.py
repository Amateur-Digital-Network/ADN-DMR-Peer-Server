# ADN DMR Peer Server - tests harness obp ingress corpus
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

"""What an OpenBridge leg does with a datagram, recorded frame by frame.

Each case is a recipe (not raw bytes): the frame to build, the configuration to
build it against, and the address it arrives from. Running one feeds a real
``HBPProtocol`` and records everything observable from outside — what reached
routing, what was quenched, where egress went afterwards and what was logged.

The recorded answers live in ``fixtures/obp_ingress_effects.jsonl``; they were
taken from the pre-refactor handler and verified frame by frame against
upstream ``develop``, so they are the contract the OBP ingress must keep
whatever it is rebuilt on. Regenerate with ``CAPTURE=1 pytest
tests/infrastructure/test_obp_ingress_effects.py`` and read the diff carefully:
every line that moves is a behaviour change.
"""

from __future__ import annotations

import copy
import functools
import itertools
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from adn_server.domain import bytes_3, bytes_4
from adn_server.infrastructure.acl_router import InMemoryAclRouter
from adn_server.infrastructure.hbp_constants import BCST, DMRD
from adn_server.infrastructure.mesh.dmre_v5 import build_dmre
from adn_server.infrastructure.mesh.obp_v1 import build_bcka, build_bcsq, build_dmrd_v1, obp_hmac_sha1
from adn_server.infrastructure.twisted_adapters.udp_hbp import HBPProtocol

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "obp_ingress_effects.jsonl"

PASSPHRASE = (b"test-passphrase" + b"\x00" * 20)[:20]
NETWORK_ID = bytes_4(20840)
PEER = ("82.65.127.86", 62201)
SERVER_ID = 21310

# Subscribers the ACLs deny, so both scopes can be told apart in the recording.
DENIED_BY_GLOBAL = 2130002
DENIED_BY_SYSTEM = 2130001
ALLOWED = 2130003

if not hasattr(logging.Logger, "trace"):  # the server installs TRACE with the log config
    logging.addLevelName(5, "TRACE")
    logging.Logger.trace = functools.partialmethod(logging.Logger.log, 5)  # type: ignore[attr-defined]


class _Recorder(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=1)
        self.lines: list[tuple[int, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = record.getMessage()
        except TypeError as exc:  # a message and its arguments that do not match
            text = f"<BROKEN LOG CALL: {exc}>"
        self.lines.append((record.levelno, text))


class _Transport:
    def __init__(self) -> None:
        self.sent: list[tuple[int, tuple[str, int]]] = []

    def write(self, data: bytes, addr: tuple[str, int]) -> None:
        self.sent.append((len(data), addr))


def build_config(case: dict[str, Any]) -> dict[str, Any]:
    """The server config one case runs against."""
    system = {
        "MODE": "OPENBRIDGE",
        "ENABLED": True,
        "NETWORK_ID": NETWORK_ID,
        "PASSPHRASE": PASSPHRASE,
        "TARGET_IP": PEER[0],
        "TARGET_PORT": PEER[1],
        "TARGET_SOCK": PEER,
        "RELAX_CHECKS": case.get("relax", True),
        "VER": 5 if case["kind"] == "v5" else 1,
        "ENHANCED_OBP": True,
        "USE_ACL": case.get("system_acl", False),
        "SUB_ACL": (False, [(DENIED_BY_SYSTEM, DENIED_BY_SYSTEM)]),
        "TG1_ACL": (False, [(777, 777)]),
    }
    config: dict[str, Any] = {
        "GLOBAL": {
            "SERVER_ID": bytes_4(SERVER_ID),
            "USE_ACL": case.get("global_acl", False),
            "SUB_ACL": (False, [(DENIED_BY_GLOBAL, DENIED_BY_GLOBAL)]),
            "TG1_ACL": (False, [(778, 778)]),
            "VALIDATE_SERVER_IDS": case.get("validate_server_ids", False),
            "PING_TIME": 10,
        },
        "SYSTEMS": {"OBP-FR": system},
        "_SERVER_IDS": {"2084"},
        "_SUB_IDS": {DENIED_BY_SYSTEM: "C31AG", DENIED_BY_GLOBAL: "C31AG"},
        "_PEER_IDS": {},
        "_LOCAL_SUBSCRIBER_IDS": {},
    }
    if case.get("stun"):
        config["STUN"] = True
    return config


def _voice_body(case: dict[str, Any]) -> bytes:
    return b"".join(
        [
            DMRD,
            bytes([1]),
            bytes_3(case.get("src", ALLOWED)),
            bytes_3(case.get("dst", 214)),
            NETWORK_ID,
            bytes([case.get("bits", 0x00)]),
            bytes_4(case.get("stream", 0xAABBCCDD)),
            b"\x00" * 33,
        ]
    )


def build_packet(case: dict[str, Any], *, now: float | None = None) -> bytes:
    """The datagram a case puts on the wire, valid MAC included."""
    kind = case["kind"]
    if kind == "v1":
        return build_dmrd_v1(_voice_body(case), NETWORK_ID, PASSPHRASE)
    if kind == "v5":
        now = time.time() if now is None else now
        packet = build_dmre(
            _voice_body(case),
            server_id=NETWORK_ID,
            ber=b"\x00",
            rssi=b"\x00",
            embedded_ver=5,
            timestamp_ns=int((now - case.get("age", 0.0)) * 1_000_000_000),
            source_server=bytes_4(case.get("source_server", 2084)),
            source_rptr=bytes_4(0),
            hops=case.get("hops", 0).to_bytes(1, "big"),
            passphrase=PASSPHRASE,
            extended_layout=True,
        )
        assert packet is not None
        return packet
    if kind == "bcka":
        return build_bcka(PASSPHRASE)
    if kind == "bcsq":
        return build_bcsq(bytes_3(case.get("dst", 214)), bytes_4(case.get("stream", 1)), PASSPHRASE)
    if kind == "bcst":
        return BCST + obp_hmac_sha1(PASSPHRASE, BCST)
    raise ValueError(f"unknown case kind: {kind}")


def observe(case: dict[str, Any], protocol_cls: type = HBPProtocol) -> dict[str, Any]:
    """Run one case and record everything observable from outside the bridge."""
    delivered: list[tuple[int, int]] = []
    quenched: list[tuple[int, int]] = []
    recorder = _Recorder()
    root = logging.getLogger("adn_server")
    root.addHandler(recorder)
    previous_level = root.level
    root.setLevel(1)
    transport = _Transport()
    try:
        packet = build_packet(case)
        protocol = protocol_cls(
            "OBP-FR",
            build_config(case),
            router=InMemoryAclRouter(),
            dmrd_received=lambda *a, **k: delivered.append((int.from_bytes(a[2], "big"), int.from_bytes(a[3], "big"))),
        )
        protocol._obp_send_bcsq = lambda tgid, stream: quenched.append(  # type: ignore[assignment]
            (int.from_bytes(tgid, "big"), int.from_bytes(stream, "big"))
        )
        protocol._obp_send_bcve = lambda: None  # type: ignore[assignment]
        protocol.transport = transport  # type: ignore[assignment]
        protocol.startProtocol()
        transport.sent.clear()
        protocol._obp_datagram_received(packet, tuple(case.get("from", PEER)))
        # Where egress goes now is what the peer address is for, and a stunned
        # bridge must stop sending: probe both after every case.
        protocol_cls._obp_send_bcka(protocol)
        protocol.send_system(_voice_body({"src": ALLOWED, "dst": 214})[:53])
    finally:
        root.removeHandler(recorder)
        root.setLevel(previous_level)
    return {
        "delivered": delivered,
        "quenched": quenched,
        "egress": [[size, list(addr)] for size, addr in transport.sent],
        "log": [[level, text] for level, text in recorder.lines],
    }


def _cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    stream = 0x51000000

    def add(**case: Any) -> None:
        nonlocal stream
        stream += 1
        case.setdefault("stream", stream)
        case["name"] = "{kind} {desc}".format(**case)
        cases.append(case)

    # DMRD v1: the talkgroup filter, the bits byte and both ACL scopes.
    for dst in (1, 9, 79, 80, 92, 199, 200, 214, 777, 778, 9989, 9990, 9999, 900999):
        add(kind="v1", desc=f"tg {dst}", dst=dst)
    for bits in (0x00, 0x40, 0x23, 0x80, 0xE3, 0x16):
        add(kind="v1", desc=f"bits {bits:#04x}", bits=bits)
        add(kind="v1", desc=f"bits {bits:#04x} on a local tg", bits=bits, dst=9)
    for src, scope in ((DENIED_BY_GLOBAL, "global"), (DENIED_BY_SYSTEM, "system"), (ALLOWED, "allowed")):
        for global_acl, system_acl in ((True, False), (False, True), (True, True)):
            add(
                kind="v1",
                desc=f"{scope} subscriber, acl g={global_acl} s={system_acl}",
                src=src,
                global_acl=global_acl,
                system_acl=system_acl,
            )
    for dst in (777, 778):
        add(kind="v1", desc=f"denied tg {dst}", dst=dst, global_acl=True, system_acl=True)
    add(kind="v1", desc="stunned by the operator", stun=True)
    add(kind="v1", desc="from an unexpected address", **{"from": ["9.9.9.9", 40000]})
    add(kind="v1", desc="from an unexpected address, no relax", relax=False, **{"from": ["9.9.9.9", 40000]})

    # DMRE v5: the envelope (age, hops, source server) on top of the same filters.
    for dst in (1, 9, 79, 85, 92, 100, 199, 214, 850, 9990, 900999):
        add(kind="v5", desc=f"tg {dst}", dst=dst)
    for source_server in (123, 2084, 2131, 21310, 2130001):
        add(kind="v5", desc=f"source server {source_server}", source_server=source_server)
        add(
            kind="v5",
            desc=f"source server {source_server}, validated",
            source_server=source_server,
            validate_server_ids=True,
        )
    for dst, source_server in ((100, 20840), (100, 21310), (85, 20851), (850, 21310)):
        add(kind="v5", desc=f"tg {dst} from server {source_server}", dst=dst, source_server=source_server)
    for hops in (0, 8, 9, 10, 20):
        add(kind="v5", desc=f"{hops} hops", hops=hops)
    for age in (0.0, 4.0, 6.0, 60.0):
        add(kind="v5", desc=f"{age}s old", age=age)
    for src, scope in ((DENIED_BY_GLOBAL, "global"), (DENIED_BY_SYSTEM, "system")):
        add(kind="v5", desc=f"{scope} subscriber", src=src, global_acl=True, system_acl=True)
    add(kind="v5", desc="stunned by the operator", stun=True)
    add(kind="v5", desc="from an unexpected address", **{"from": ["9.9.9.9", 40000]})

    # Control frames: where do they leave the egress afterwards?
    for kind, addr in itertools.product(
        ("bcka", "bcsq", "bcst"),
        (PEER, ("82.65.127.86", 40000), ("9.9.9.9", 62201)),
    ):
        for relax in (False, True):
            add(kind=kind, desc=f"from {addr[0]}:{addr[1]} relax={relax}", relax=relax, **{"from": list(addr)})
    return cases


CASES: list[dict[str, Any]] = _cases()


def capture_enabled() -> bool:
    return os.environ.get("CAPTURE", "").strip().lower() in ("1", "true", "yes")


def record(path: Path = FIXTURE) -> None:
    """Rewrite the fixture from what this tree does right now."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for case in CASES:
            row = {"case": case, "effects": observe(copy.deepcopy(case))}
            fh.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")


def load(path: Path = FIXTURE) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def as_json(effects: dict[str, Any]) -> dict[str, Any]:
    """Round-trip through JSON so recorded and observed compare as equals."""
    return json.loads(json.dumps(effects))


__all__ = [
    "CASES",
    "FIXTURE",
    "as_json",
    "build_config",
    "build_packet",
    "capture_enabled",
    "load",
    "observe",
    "record",
]
