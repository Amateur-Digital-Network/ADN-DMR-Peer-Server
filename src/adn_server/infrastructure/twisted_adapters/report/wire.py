# ADN DMR Peer Server - infrastructure twisted adapters report wire
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

"""Report wire: slim dashboard_state + voice_event to monitor (D-25)."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from adn_server.application.ports import ReportWireEncoder
from adn_server.domain.mesh_session import MeshSessionStore
from adn_server.application.report import (
    REPORT_FEATURES,
    REPORT_PROTOCOL,
    build_dashboard_state,
    build_routing_table,
    hello_connected_system_names,
    parse_bridge_event_csv,
    routing_table_delta,
)

from .opcodes import REPORT_OPCODES, SERVER_NAME, server_version

logger = logging.getLogger(__name__)


def _json_wire(opcode: bytes, payload: dict[str, Any]) -> bytes:
    return opcode + json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _state_dedup_key(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        {"ctable": payload.get("ctable"), "server_id": payload.get("server_id")},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


class ReportWire(ReportWireEncoder):
    """Slim monitor encoder — ``dashboard_state`` + ``routing_table`` + ``voice_event``."""

    def __init__(self, sessions: MeshSessionStore | None = None) -> None:
        self._sessions = sessions
        self._last_state_key: bytes | None = None
        self._routing_seq: int = 0
        self._last_routing_snapshot: dict[str, Any] | None = None

    def hello_frames(self, systems: dict[str, Any]) -> tuple[bytes, ...]:
        names = hello_connected_system_names(systems)
        info: dict[str, Any] = {
            "type": "hello",
            "server": SERVER_NAME,
            "version": server_version(),
            "report_protocol": REPORT_PROTOCOL,
            "features": list(REPORT_FEATURES),
        }
        if names:
            info["systems"] = names
        payload = json.dumps(info, separators=(",", ":")).encode("utf-8")
        return (REPORT_OPCODES["HELLO"] + payload,)

    def config_frames(self, systems: dict[str, Any], *, full_snapshot: bool) -> tuple[bytes, ...]:
        return self.state_frames(systems, force=full_snapshot)

    def state_frames(self, systems: dict[str, Any], *, force: bool = False) -> tuple[bytes, ...]:
        ts = time.time()
        payload = build_dashboard_state(systems, ts=ts, sessions=self._sessions)
        key = _state_dedup_key(payload)
        if not force and self._last_state_key == key:
            logger.debug("(REPORT) STATE_SND unchanged, skip")
            return ()
        self._last_state_key = key
        logger.debug("(REPORT) STATE_SND ts=%s", payload.get("ts"))
        return (_json_wire(REPORT_OPCODES["STATE_SND"], payload),)

    def bridge_frames(self, bridges: dict[str, Any], *, full_snapshot: bool) -> tuple[bytes, ...]:
        """UA / static bridge legs for monitor ``SINGLE_TS*`` chips (slim wire)."""
        ts = time.time()
        self._routing_seq += 1
        current = build_routing_table(bridges, seq=self._routing_seq, ts=ts)
        if full_snapshot or self._last_routing_snapshot is None:
            self._last_routing_snapshot = current
            logger.debug("(REPORT) ROUTING_TABLE_SND seq=%s routes=%d", self._routing_seq, len(current.get("routes", [])))
            return (_json_wire(REPORT_OPCODES["ROUTING_TABLE_SND"], current),)
        delta = routing_table_delta(self._last_routing_snapshot, current, seq=self._routing_seq, ts=ts)
        if delta is None:
            self._routing_seq -= 1
            logger.debug("(REPORT) routing_table unchanged, skip")
            return ()
        self._last_routing_snapshot = current
        logger.debug("(REPORT) DELTA_SND routing_table seq=%s", self._routing_seq)
        return (_json_wire(REPORT_OPCODES["DELTA_SND"], delta),)

    def bridge_event_frames(self, event: str) -> tuple[bytes, ...]:
        voice = parse_bridge_event_csv(event)
        if voice is None:
            logger.warning("(REPORT) voice_event not emitted (unmapped CSV): %s", event[:120])
            return ()
        logger.debug(
            "(REPORT) VOICE_EVENT_SND %s %s %s",
            voice["call_family"],
            voice["phase"],
            voice["system"],
        )
        return (_json_wire(REPORT_OPCODES["VOICE_EVENT_SND"], voice),)
