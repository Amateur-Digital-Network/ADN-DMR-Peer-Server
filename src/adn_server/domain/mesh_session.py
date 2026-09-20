# ADN DMR Peer Server - domain mesh session
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

"""Live state of an OpenBridge link, kept out of the configuration.

Legacy hblink wrote what a bridge learns at runtime straight into its ``SYSTEMS``
block: ``_bcka`` for the last keepalive, ``_bcsq`` for the peer's quench table,
and ``TARGET_IP``/``TARGET_PORT``/``TARGET_SOCK`` rewritten in place whenever
``RELAX_CHECKS`` accepted a datagram from an unexpected address. Configuration
and session state shared one mutable dict, so what the operator wrote in the
YAML could be overwritten by whatever arrived on the wire, and no reader could
tell the two apart.

Here they are separate: ``configured_peer`` is what the YAML says and never
moves, ``learned_peer`` is what the wire says, and every reader asks the
question it actually means — "has a keepalive ever arrived?", "is it stale?",
"is this stream quenched?" — instead of poking at dict keys.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .value_objects import bytes_3, int_id

KEEPALIVE_TIMEOUT_S = 60.0


def _peer_from_config(sys_cfg: dict[str, Any] | None) -> tuple[str | None, int]:
    """The peer as configured: TARGET_SOCK when normalized, else TARGET_IP/PORT."""
    cfg = sys_cfg or {}
    sock = cfg.get("TARGET_SOCK")
    if isinstance(sock, tuple) and len(sock) == 2:
        host, port = sock
    else:
        host, port = cfg.get("TARGET_IP"), cfg.get("TARGET_PORT", 62044)
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = 62044
    return (str(host) if host else None, port)


@dataclass
class ObpBridgeSession:
    """What one OpenBridge link knows about its peer right now."""

    system_name: str
    configured_peer: tuple[str | None, int] = (None, 62044)
    learned_peer: tuple[str, int] | None = None
    learned_at: float = 0.0
    last_keepalive: float | None = None
    quenched: dict[bytes, bytes] = field(default_factory=dict)
    stunned: bool = False
    drops: dict[str, int] = field(default_factory=dict)

    # --- peer address --------------------------------------------------------

    @property
    def peer(self) -> tuple[str | None, int]:
        """Where to send: what the wire taught us, else what the YAML says."""
        return self.learned_peer or self.configured_peer

    @property
    def peer_known(self) -> bool:
        return bool(self.peer[0])

    def learn_peer(self, addr: tuple[str, int], *, at: float) -> bool:
        """Remember the address a datagram really came from. True when it moved."""
        if not addr or not addr[0]:
            return False
        host, port = str(addr[0]), int(addr[1])
        if self.peer == (host, port):
            return False
        self.learned_peer = (host, port)
        self.learned_at = at
        return True

    def forget_learned_peer(self) -> None:
        """Drop what the wire taught us and fall back to the configured peer."""
        self.learned_peer = None
        self.learned_at = 0.0

    # --- keepalive -----------------------------------------------------------

    def note_keepalive(self, at: float) -> None:
        self.last_keepalive = at

    @property
    def keepalive_seen(self) -> bool:
        """False until the first keepalive (or the seed at startup)."""
        return self.last_keepalive is not None

    def keepalive_age(self, now: float) -> float | None:
        if self.last_keepalive is None:
            return None
        return now - self.last_keepalive

    def keepalive_stale(self, now: float, *, timeout: float = KEEPALIVE_TIMEOUT_S) -> bool:
        """True when a keepalive was expected by now and has not arrived."""
        if self.last_keepalive is None:
            return False
        return self.last_keepalive < now - timeout

    def keepalive_ok(self, now: float, *, timeout: float = KEEPALIVE_TIMEOUT_S) -> bool:
        return self.keepalive_seen and not self.keepalive_stale(now, timeout=timeout)

    # --- source quench -------------------------------------------------------

    def quench(self, tgid: bytes, stream_id: bytes) -> None:
        """The peer asked us to stop sending this stream on this talkgroup."""
        self.quenched[tgid] = stream_id

    def quenches(self, dst_id: Any, stream_id: bytes) -> bool:
        """True when the peer quenched this stream for this talkgroup.

        Talkgroups arrive as 3-byte ids here and as 4-byte ids elsewhere, so a
        direct hit is tried first and the rest are compared numerically.
        """
        if not self.quenched:
            return False
        tid = dst_id[:3] if isinstance(dst_id, bytes) and len(dst_id) >= 3 else bytes_3(int_id(dst_id))
        if self.quenched.get(tid) == stream_id:
            return True
        for key, value in self.quenched.items():
            if value != stream_id:
                continue
            try:
                if isinstance(key, bytes) and len(key) >= 3 and int_id(key) == int_id(tid):
                    return True
            except Exception:
                continue
        return False

    # --- what this link refuses ----------------------------------------------

    def count_drop(self, reason: str) -> None:
        """Tally a refused frame by reason, for counters and traces."""
        self.drops[reason] = self.drops.get(reason, 0) + 1

    # --- stun ----------------------------------------------------------------

    def stun(self) -> None:
        """The peer asked this bridge to stop sending (BCST)."""
        self.stunned = True

    def release_stream(self, stream_id: bytes) -> None:
        """Forget the quench entries of a stream that is over."""
        for tgid, value in list(self.quenched.items()):
            if value == stream_id:
                self.quenched.pop(tgid, None)


class MeshSessionStore:
    """The live sessions, one per OPENBRIDGE system, by system name."""

    def __init__(self) -> None:
        self._sessions: dict[str, ObpBridgeSession] = {}

    def __contains__(self, system_name: str) -> bool:
        return system_name in self._sessions

    def __len__(self) -> int:
        return len(self._sessions)

    def get(self, system_name: str) -> ObpBridgeSession | None:
        return self._sessions.get(system_name)

    def session(self, system_name: str, sys_cfg: dict[str, Any] | None = None) -> ObpBridgeSession:
        """The session for this system, created from its config on first use."""
        session = self._sessions.get(system_name)
        if session is None:
            session = ObpBridgeSession(
                system_name=system_name,
                configured_peer=_peer_from_config(sys_cfg),
            )
            self._sessions[system_name] = session
        elif sys_cfg is not None:
            session.configured_peer = _peer_from_config(sys_cfg)
        return session

    def drop(self, system_name: str) -> None:
        self._sessions.pop(system_name, None)

    def sync(self, config: dict[str, Any]) -> None:
        """Follow a config (re)load: refresh configured peers, forget dead links.

        A bridge that is still there keeps what it has learned; the address the
        operator edited in the YAML wins again only where it is now different,
        which is what makes a reload a way out of a bad learned address.
        """
        systems = config.get("SYSTEMS", {})
        live: set[str] = set()
        for name, sys_cfg in systems.items():
            if not isinstance(sys_cfg, dict) or sys_cfg.get("MODE") != "OPENBRIDGE":
                continue
            if not sys_cfg.get("ENABLED", True):
                continue
            live.add(name)
            configured = _peer_from_config(sys_cfg)
            session = self._sessions.get(name)
            if session is None:
                self._sessions[name] = ObpBridgeSession(system_name=name, configured_peer=configured)
                continue
            if session.configured_peer != configured:
                session.configured_peer = configured
                session.forget_learned_peer()
        for name in list(self._sessions):
            if name not in live:
                self._sessions.pop(name, None)


def mesh_sessions(config: dict[str, Any]) -> MeshSessionStore:
    """The store for this server, kept beside the other runtime tables.

    It lives under a private top-level key, like ``_SUB_MAP`` and ``_PEER_IDS``,
    so every layer that already receives the config can reach the same instance
    and ``config_reload`` preserves it across a SIGHUP. What it holds is no
    longer inside the SYSTEMS blocks, which is what makes those read-only.
    """
    store = config.get("_MESH_SESSIONS")
    if not isinstance(store, MeshSessionStore):
        store = MeshSessionStore()
        store.sync(config)
        config["_MESH_SESSIONS"] = store
    return store


def obp_session(config: dict[str, Any], system_name: str) -> ObpBridgeSession:
    """Session of one OPENBRIDGE system, from the server-wide store."""
    sys_cfg = config.get("SYSTEMS", {}).get(system_name)
    return mesh_sessions(config).session(system_name, sys_cfg)


__all__ = [
    "KEEPALIVE_TIMEOUT_S",
    "MeshSessionStore",
    "ObpBridgeSession",
    "mesh_sessions",
    "obp_session",
]
