# ADN DMR Peer Server - application report dashboard state
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

"""Minimal dashboard snapshot for external MQTT consumers (not full topology / routing)."""

from __future__ import annotations

import time
from typing import Any

from adn_server.domain import int_id
from adn_server.domain.mesh_session import MeshSessionStore, ObpBridgeSession

from .payloads import _peer_field_json, build_topology


def _connected_topology_peers(system: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for p in system.get("peers", []) if isinstance(p, dict) and p.get("connected")]


def _upstream_peer_connected(cfg: dict[str, Any]) -> bool:
    mode = cfg.get("MODE", "MASTER")
    if mode not in ("PEER", "XLXPEER"):
        return False
    for key in ("XLXSTATS", "STATS"):
        block = cfg.get(key)
        if isinstance(block, dict) and block.get("CONNECTION") == "YES":
            return True
    return False


def _upstream_peer_block(name: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """Homebrew / XLX upstream (``CTABLE.PEERS``), not hotspots under a MASTER."""
    mode = cfg.get("MODE", "PEER")
    block: dict[str, Any] = {"mode": mode, "connected": True}
    for legacy_key, json_key in (
        ("CALLSIGN", "callsign"),
        ("LOCATION", "location"),
        ("DESCRIPTION", "description"),
        ("URL", "url"),
        ("MASTER_IP", "master_ip"),
        ("MASTER_PORT", "master_port"),
    ):
        text = _peer_field_json(cfg.get(legacy_key))
        if text is not None:
            block[json_key] = text
    radio_id = cfg.get("RADIO_ID")
    if radio_id is not None:
        block["radio_id"] = int_id(radio_id)
    stats_key = "XLXSTATS" if mode == "XLXPEER" else "STATS"
    stats = cfg.get(stats_key)
    if isinstance(stats, dict) and stats.get("CONNECTED"):
        try:
            block["connected_at"] = int(float(stats["CONNECTED"]))
        except (TypeError, ValueError):
            pass
    return block


def _obp_ka_connected(
    cfg: dict[str, Any], session: ObpBridgeSession | None, now: float
) -> bool | None:
    """BCKA keepalive status for ENHANCED OBP legs; ``None`` when KA gating does not apply."""
    if not cfg.get("ENHANCED_OBP"):
        return None
    if session is None:
        return False
    return session.keepalive_ok(now)


def _openbridge_block(
    name: str,
    cfg: dict[str, Any],
    topology_row: dict[str, Any] | None,
    *,
    now: float,
    session: ObpBridgeSession | None = None,
) -> dict[str, Any]:
    """Enabled OPENBRIDGE legs (``CTABLE.OPENBRIDGES``); STREAMS stay empty here (live chips = monitor/voice)."""
    del name
    block: dict[str, Any] = {"mode": "OPENBRIDGE", "streams": {}}
    network_id = cfg.get("NETWORK_ID")
    if network_id is not None:
        block["network_id"] = int_id(network_id)
    row = topology_row or {}
    if row.get("ip"):
        block["ip"] = row["ip"]
    if row.get("port") is not None:
        block["port"] = int(row["port"])
    if row.get("enhanced_obp") or cfg.get("ENHANCED_OBP"):
        block["enhanced_obp"] = True
    connected = _obp_ka_connected(cfg, session, now)
    if connected is not None:
        block["connected"] = connected
    return block


def build_dashboard_state(
    systems: dict[str, Any],
    *,
    server_id: str | None = None,
    ts: float | None = None,
    sessions: MeshSessionStore | None = None,
) -> dict[str, Any]:
    """Slim linked-systems view (masters with peers, homebrew peers, openbridges).

    Mirrors adn-monitor WebSocket ``ctable_for_lnksys`` + ``ctable_for_opb`` intent:
    no routing_table, no idle masters, no secrets.
    """
    epoch = time.time() if ts is None else ts
    topology = build_topology(systems, seq=0, ts=epoch)
    topology_by_name = {
        s["name"]: s
        for s in topology.get("systems", [])
        if isinstance(s, dict) and s.get("name")
    }
    masters: dict[str, Any] = {}
    peers: dict[str, Any] = {}
    openbridges: dict[str, Any] = {}

    for name, cfg in systems.items():
        if not isinstance(cfg, dict) or not cfg.get("ENABLED", True):
            continue
        mode = cfg.get("MODE", "MASTER")
        topo = topology_by_name.get(name)

        if mode == "MASTER":
            if topo is None:
                continue
            live = _connected_topology_peers(topo)
            if not live:
                continue
            block: dict[str, Any] = {
                "mode": "MASTER",
                "peers": {int(p["id"]): p for p in live if "id" in p},
            }
            if "SINGLE_MODE" in cfg:
                block["single_mode"] = bool(cfg.get("SINGLE_MODE", False))
            if cfg.get("DEFAULT_UA_TIMER") is not None:
                block["default_ua_timer"] = float(cfg.get("DEFAULT_UA_TIMER", 10))
            if topo.get("ip"):
                block["ip"] = topo["ip"]
            if topo.get("port") is not None:
                block["port"] = int(topo["port"])
            masters[name] = block
        elif mode == "OPENBRIDGE":
            openbridges[name] = _openbridge_block(
                name,
                cfg,
                topo,
                now=epoch,
                session=sessions.get(name) if sessions is not None else None,
            )
        elif mode in ("PEER", "XLXPEER") and _upstream_peer_connected(cfg):
            peers[name] = _upstream_peer_block(name, cfg)

    payload: dict[str, Any] = {
        "type": "dashboard_state",
        "ts": float(epoch),
        "ctable": {
            "MASTERS": masters,
            "PEERS": peers,
            "OPENBRIDGES": openbridges,
        },
    }
    if server_id is not None:
        payload["server_id"] = server_id
    return payload
