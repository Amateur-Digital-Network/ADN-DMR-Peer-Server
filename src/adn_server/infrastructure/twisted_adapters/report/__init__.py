# ADN DMR Peer Server - infrastructure twisted adapters report   init
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

"""Report wire encoder (infrastructure).

Layering:
  application/report/payloads.py  — JSON dicts from SYSTEMS/BRIDGES/CSV
  application/ports.ReportWireEncoder — outbound port
  wire.py — sole implementation
"""

from __future__ import annotations

from typing import Any

from adn_server.application.ports import ReportWireEncoder
from adn_server.domain.mesh_session import mesh_sessions

from .opcodes import REPORT_OPCODES
from .wire import ReportWire
from .worker import DEFAULT_DRAIN_INTERVAL_SEC, start_report_queue_worker

__all__ = [
    "DEFAULT_DRAIN_INTERVAL_SEC",
    "REPORT_OPCODES",
    "ReportWire",
    "create_report_wire",
    "start_report_queue_worker",
]


def create_report_wire(config: dict[str, Any]) -> ReportWireEncoder:
    """Return the report wire encoder, reading OBP keepalives from the live sessions."""
    return ReportWire(sessions=mesh_sessions(config))
