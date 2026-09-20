# ADN DMR Peer Server - infrastructure obp replay
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

"""Replay a capture through the OpenBridge ingress and say what it would do.

Offline answer to "why did that call not cross": the frames from a ``tcpdump``
capture go through the same engine the server runs, against the operator's own
adn-server.yaml, and each one comes back with the bridge it belongs to and
either a delivery or the reason it was refused. Nothing is sent, nothing is
bound; the server can keep running while this reads the capture.

This is what the engine being pure buys — the decisions can be taken anywhere.
"""

from __future__ import annotations

import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from adn_server.application.proxy.deployment import obp_bridge_legacy_listen_port
from adn_server.domain import int_id
from adn_server.domain.mesh_admission import AclRules, AdmissionContext, server_prefix
from adn_server.domain.mesh_engine import (
    BridgePolicy,
    Deliver,
    Reject,
    accepts_source,
    ingest_dmrd_v1,
    ingest_dmre_v5,
    reject_v1_protocol,
    server_id_bytes,
)
from adn_server.domain.mesh_routing import PeerMeshConfig
from adn_server.domain.mesh_session import MeshSessionStore, ObpBridgeSession
from adn_server.infrastructure.acl_router import InMemoryAclRouter
from adn_server.infrastructure.hbp_constants import BC, BCKA, BCSQ, BCST, BCVE, DMRD, DMRE
from adn_server.infrastructure.mesh.dmre_v5 import parse_dmre_trailer
from adn_server.infrastructure.mesh.registry import MeshCodecRegistry
from adn_server.infrastructure.pcap import CaptureError, CapturedDatagram, read_udp

_CONTROL_NAMES = {BCKA: "BCKA", BCSQ: "BCSQ", BCST: "BCST", BCVE: "BCVE"}


@dataclass
class Verdict:
    """What the ingress would do with one captured datagram."""

    datagram: CapturedDatagram
    system: str | None = None
    kind: str = "?"
    rf_src: int = 0
    dst_id: int = 0
    outcome: str = "unmatched"
    reason: str = ""
    quench: bool = False

    def line(self) -> str:
        when = time.strftime("%H:%M:%S", time.localtime(self.datagram.timestamp))
        source = f"{self.datagram.source[0]}:{self.datagram.source[1]}"
        system = self.system or "-"
        call = f"{self.rf_src} -> {self.dst_id}" if self.rf_src or self.dst_id else ""
        verdict = self.outcome if not self.reason else f"{self.outcome} ({self.reason})"
        if self.quench:
            verdict += " +BCSQ"
        return f"{when}  {source:<24} {system:<12} {self.kind:<9} {call:<24} {verdict}"


def _openbridge_systems(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        name: sys_cfg
        for name, sys_cfg in config.get("SYSTEMS", {}).items()
        if isinstance(sys_cfg, dict)
        and sys_cfg.get("MODE") == "OPENBRIDGE"
        and sys_cfg.get("ENABLED", True)
    }


def _policy(config: dict[str, Any], name: str, sys_cfg: dict[str, Any], router: Any) -> BridgePolicy:
    global_cfg = config.get("GLOBAL", {})
    admission = AdmissionContext(
        stunned="STUN" in config,
        acl_check=router.acl_check,
        global_rules=AclRules(
            enabled=bool(global_cfg.get("USE_ACL")),
            sub_acl=global_cfg.get("SUB_ACL", (True, [])),
            tg1_acl=global_cfg.get("TG1_ACL", (True, [])),
        ),
        system_rules=AclRules(
            enabled=bool(sys_cfg.get("USE_ACL")),
            sub_acl=sys_cfg.get("SUB_ACL", (True, [])),
            tg1_acl=sys_cfg.get("TG1_ACL", (True, [])),
        ),
        server_id=server_prefix(global_cfg.get("SERVER_ID", 0)),
        validate_server_ids=bool(global_cfg.get("VALIDATE_SERVER_IDS")),
        known_server_prefixes=config.get("_SERVER_IDS", set()),
        resolve_server_id=lambda _sid: True,  # alias tables are not loaded offline
    )
    return BridgePolicy(
        system=name,
        network_id=sys_cfg.get("NETWORK_ID", b""),
        proto_ver=sys_cfg.get("VER"),
        relax_checks=bool(sys_cfg.get("RELAX_CHECKS")),
        server_id=server_id_bytes(global_cfg.get("SERVER_ID", 0)),
        admission=admission,
    )


def _mesh_config(sys_cfg: dict[str, Any], server_id: bytes) -> PeerMeshConfig:
    passphrase = sys_cfg.get("PASSPHRASE") or b""
    if isinstance(passphrase, str):
        passphrase = (passphrase.strip().encode("utf-8") + b"\x00" * 20)[:20]
    ver = sys_cfg.get("VER")
    return PeerMeshConfig(
        passphrase=passphrase,
        server_id=server_id,
        wire_ver=int(ver) if ver is not None else None,
    )


def _candidates(
    systems: dict[str, dict[str, Any]],
    datagram: CapturedDatagram,
    *,
    listen_port: int,
) -> list[str]:
    """Bridges to try for this datagram, the likeliest first.

    Same evidence the server has: the port it arrived on, then the configured
    peer address, then everyone else — a shared passphrase makes the rest
    ambiguous, which is exactly why the order matters.
    """
    host, port = datagram.source
    local_port = datagram.destination[1]
    exact: list[str] = []
    same_host: list[str] = []
    rest: list[str] = []
    for name, sys_cfg in systems.items():
        bridge_port = obp_bridge_legacy_listen_port(
            sys_cfg, listen_port=listen_port, bind_legacy_ports=True
        )
        if bridge_port == local_port:
            exact.insert(0, name)
            continue
        sock = sys_cfg.get("TARGET_SOCK")
        target_host = sock[0] if isinstance(sock, tuple) else sys_cfg.get("TARGET_IP")
        target_port = sock[1] if isinstance(sock, tuple) else sys_cfg.get("TARGET_PORT")
        if target_host == host and target_port == port:
            exact.append(name)
        elif target_host == host:
            same_host.append(name)
        else:
            rest.append(name)
    return exact + same_host + rest


def _control_verdict(datagram: CapturedDatagram, payload: bytes, system: str | None) -> Verdict:
    name = _CONTROL_NAMES.get(payload[:4], "BC?")
    return Verdict(
        datagram=datagram,
        system=system,
        kind=name,
        outcome="control",
    )


def replay(
    config: dict[str, Any],
    datagrams: list[CapturedDatagram],
    *,
    system: str | None = None,
    now: float | None = None,
) -> list[Verdict]:
    """Run captured datagrams through the ingress engine. Sends nothing."""
    systems = _openbridge_systems(config)
    if system is not None:
        systems = {name: cfg for name, cfg in systems.items() if name == system}
        if not systems:
            raise KeyError(f"no enabled OPENBRIDGE system named {system!r}")
    listen_port = int(config.get("OBP_PROXY", {}).get("LISTEN_PORT", 62032) or 62032)
    router = InMemoryAclRouter()
    registry = MeshCodecRegistry()
    store = MeshSessionStore()
    store.sync(config)
    server_id = server_id_bytes(config.get("GLOBAL", {}).get("SERVER_ID", 0))

    verdicts: list[Verdict] = []
    for datagram in datagrams:
        payload = datagram.payload
        if len(payload) < 4:
            continue
        opcode = payload[:4]
        if opcode[:2] == BC and opcode in _CONTROL_NAMES:
            names = _candidates(systems, datagram, listen_port=listen_port)
            verdicts.append(_control_verdict(datagram, payload, names[0] if names else None))
            continue
        if opcode not in (DMRD, DMRE):
            continue
        verdict = Verdict(datagram=datagram, kind="DMRD v1" if opcode == DMRD else "DMRE v5")
        for name in _candidates(systems, datagram, listen_port=listen_port):
            sys_cfg = systems[name]
            ingress = registry.decode_auto(payload, _mesh_config(sys_cfg, server_id))
            if ingress is None:
                continue
            session = store.session(name, sys_cfg)
            policy = _policy(config, name, sys_cfg, router)
            verdict.system = name
            frame = ingress.voice_frame
            verdict.rf_src = int(int_id(frame[5:8]))
            verdict.dst_id = int(int_id(frame[8:11]))
            effects = _effects_for(
                opcode, ingress, payload, datagram, policy=policy, session=session, now=now
            )
            _fill(verdict, effects)
            break
        verdicts.append(verdict)
    return verdicts


def _effects_for(
    opcode: bytes,
    ingress: Any,
    payload: bytes,
    datagram: CapturedDatagram,
    *,
    policy: BridgePolicy,
    session: ObpBridgeSession,
    now: float | None,
) -> list[Any] | None:
    moment = datagram.timestamp if now is None else now
    if opcode == DMRD:
        if policy.rejects_v1:
            return reject_v1_protocol(ingress.voice_frame[16:20], policy=policy)
        if not accepts_source(datagram.source, policy=policy, session=session):
            return None
        return ingest_dmrd_v1(ingress, datagram.source, policy=policy, session=session, now=moment)
    trailer = parse_dmre_trailer(payload)
    timestamp = trailer.timestamp if trailer is not None else b"\x00" * 8
    if not accepts_source(datagram.source, policy=policy, session=session):
        return None
    return ingest_dmre_v5(
        ingress,
        datagram.source,
        policy=policy,
        session=session,
        timestamp_ns=int.from_bytes(timestamp, "big"),
        now=moment,
    )


def _fill(verdict: Verdict, effects: list[Any] | None) -> None:
    if effects is None:
        verdict.outcome = "refused"
        verdict.reason = "source not accepted"
        return
    for effect in effects:
        if isinstance(effect, Reject):
            verdict.outcome = "dropped"
            verdict.reason = effect.reason
            verdict.quench = effect.rejection.quench
            return
        if isinstance(effect, Deliver):
            verdict.outcome = "delivered"
            return
    verdict.outcome = "ignored"


def format_report(verdicts: list[Verdict], *, capture: str, verbose: bool = True) -> str:
    """The per-frame lines and the tally the sysop actually reads."""
    lines = [f"OBP replay of {capture}", ""]
    if verbose:
        lines.extend(verdict.line() for verdict in verdicts)
        lines.append("")
    by_system: dict[str, Counter] = {}
    for verdict in verdicts:
        label = verdict.system or "(no bridge)"
        key = verdict.outcome if not verdict.reason else f"{verdict.outcome}: {verdict.reason}"
        by_system.setdefault(label, Counter())[key] += 1
    lines.append(f"{len(verdicts)} datagram(s)")
    for label in sorted(by_system):
        lines.append(f"  {label}")
        for key, count in sorted(by_system[label].items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"    {count:>6}  {key}")
    return "\n".join(lines)


def run_replay(
    config: dict[str, Any],
    capture_path: str,
    *,
    system: str | None = None,
    limit: int | None = None,
    summary_only: bool = False,
    out: TextIO | None = None,
) -> int:
    """Read a capture, replay it, print the report. Returns 0 when it ran."""
    stream = out or sys.stdout
    try:
        datagrams = list(read_udp(Path(capture_path)))
    except (CaptureError, OSError) as exc:
        print(f"ERROR capture: {exc}", file=sys.stderr)
        return 1
    if limit is not None:
        datagrams = datagrams[:limit]
    try:
        verdicts = replay(config, datagrams, system=system)
    except KeyError as exc:
        print(f"ERROR system: {exc}", file=sys.stderr)
        return 1
    print(format_report(verdicts, capture=capture_path, verbose=not summary_only), file=stream)
    return 0


__all__ = [
    "Verdict",
    "format_report",
    "replay",
    "run_replay",
]
