# ADN DMR Peer Server - report of this server to the ADN panel
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

"""What this server tells the ADN panel about itself (``ADN_PANEL`` in adn-server.yaml).

The sysop generates a token for the server in the panel and adds the section by hand;
it is not in the config template. Without ENDPOINT and TOKEN nothing is sent.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from adn_server.application.proxy.deployment import (
    obp_proxy_enabled,
    proxy_target_system,
)
from adn_server.application.report.dashboard_state import build_dashboard_state
from adn_server.domain.mesh_session import MeshSessionStore
from adn_server.domain.value_objects import int_id

DEFAULT_INTERVAL = 300
MIN_INTERVAL = 60
REPORT_PATH = "/ingest/server-report"


@dataclass(frozen=True)
class PanelReportSettings:
    url: str
    token: str
    interval: int
    odtp_port: int | None


def panel_report_settings(config: dict[str, Any]) -> PanelReportSettings | None:
    """``ADN_PANEL`` settings, or None when the server does not report to the panel."""
    block = config.get("ADN_PANEL")
    if not isinstance(block, dict):
        return None
    endpoint = str(block.get("ENDPOINT") or "").strip().rstrip("/")
    token = str(block.get("TOKEN") or "").strip()
    if not endpoint or not token:
        return None
    interval = max(int(block.get("REPORT_INTERVAL") or DEFAULT_INTERVAL), MIN_INTERVAL)
    odtp_port = block.get("ODTP_PORT")
    return PanelReportSettings(
        url=endpoint + REPORT_PATH,
        token=token,
        interval=interval,
        odtp_port=int(odtp_port) if odtp_port else None,
    )


def _hbp_ports(config: dict[str, Any]) -> list[int]:
    """Ports hotspots connect to: the integrated proxy's, else each enabled MASTER's."""
    if proxy_target_system(config) is not None:
        return [int(config.get("PROXY", {}).get("LISTEN_PORT", 62031))]
    ports = {
        int(cfg["PORT"])
        for cfg in config.get("SYSTEMS", {}).values()
        if isinstance(cfg, dict)
        and cfg.get("ENABLED", True)
        and cfg.get("MODE") == "MASTER"
        and cfg.get("PORT")
    }
    return sorted(ports)


def build_panel_report(
    config: dict[str, Any],
    *,
    version: str,
    uptime_s: float,
    settings: PanelReportSettings,
    sessions: MeshSessionStore | None = None,
    plugins: Iterable[str] = (),
    now: float | None = None,
) -> dict[str, Any]:
    """Version, ports, connected hotspots and OBP links. No IPs, passphrases or IDs of users."""
    state = build_dashboard_state(config.get("SYSTEMS", {}), ts=now, sessions=sessions)
    ctable = state["ctable"]
    peers = sum(len(block.get("peers", {})) for block in ctable["MASTERS"].values())
    obp_links = [
        {
            "name": name,
            "network_id": block.get("network_id"),
            "enhanced": bool(block.get("enhanced_obp")),
            "connected": block.get("connected"),
        }
        for name, block in sorted(ctable["OPENBRIDGES"].items())
    ]
    # bytes once the server normalized its config, int in the yaml
    server_id = config.get("GLOBAL", {}).get("SERVER_ID")
    obp_proxy_port = None
    if obp_proxy_enabled(config):
        obp_proxy_port = int(config.get("OBP_PROXY", {}).get("LISTEN_PORT", 62032))
    return {
        "version": version,
        "server_id": int_id(server_id) if server_id else None,
        "uptime_s": int(uptime_s),
        "hbp_ports": _hbp_ports(config),
        "odtp_port": settings.odtp_port,
        "obp_proxy_port": obp_proxy_port,
        "peers": peers,
        "obp_links": obp_links,
        "plugins": sorted(plugins),
    }
