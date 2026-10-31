# ADN DMR Peer Server - tests application panel report
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

"""Report of this server to the ADN panel (ADN_PANEL)."""

from __future__ import annotations

import pytest

from adn_server.application.report.panel_report import (
    build_panel_report,
    panel_report_settings,
)
from adn_server.domain.errors import ConfigError
from adn_server.infrastructure.config_validator import validate_config
from tests.conftest import minimal_valid_config


def _config(**panel) -> dict:
    return {
        "GLOBAL": {"SERVER_ID": 21310},
        "PROXY": {"LISTEN_PORT": 62031, "TARGET_SYSTEM": "SYSTEM"},
        "OBP_PROXY": {"ENABLED": True, "LISTEN_PORT": 62032},
        "ADN_PANEL": panel,
        "SYSTEMS": {
            "SYSTEM": {
                "MODE": "MASTER",
                "ENABLED": True,
                "PORT": 54000,
                "MAX_PEERS": 10,
                "PEERS": {
                    b"\x0c\xb3\x96\x01": {"CONNECTION": "YES", "CONNECTED": 1000, "IP": "10.0.0.2"},
                    b"\x0c\xb3\x96\x02": {"CONNECTION": "NO", "CONNECTED": 0},
                },
            },
            "OBP-2141": {
                "MODE": "OPENBRIDGE",
                "ENABLED": True,
                "NETWORK_ID": 21410,
                "TARGET_IP": "192.0.2.10",
                "TARGET_PORT": 62041,
                "PASSPHRASE": "secret",
            },
            "OBP-OFF": {"MODE": "OPENBRIDGE", "ENABLED": False, "NETWORK_ID": 1},
        },
    }


def test_no_settings_without_endpoint_and_token():
    assert panel_report_settings({}) is None
    assert panel_report_settings(_config(ENDPOINT="https://panel.example/api")) is None
    assert panel_report_settings(_config(TOKEN="adn_x")) is None


def test_settings_build_the_url_and_keep_a_minimum_interval():
    settings = panel_report_settings(
        _config(ENDPOINT="https://panel.example/api/", TOKEN="adn_x", REPORT_INTERVAL=5, ODTP_PORT=54006)
    )
    assert settings.url == "https://panel.example/api/ingest/server-report"
    assert settings.interval == 60
    assert settings.odtp_port == 54006


def test_report_has_ports_peers_and_links_but_no_secrets():
    config = _config(ENDPOINT="https://panel.example/api", TOKEN="adn_x", ODTP_PORT=54006)
    report = build_panel_report(
        config,
        version="2.6.1",
        uptime_s=3600.7,
        settings=panel_report_settings(config),
        plugins=["voice-announcements"],
        now=2000.0,
    )
    assert report["version"] == "2.6.1"
    assert report["server_id"] == 21310
    assert report["uptime_s"] == 3600
    assert report["hbp_ports"] == [62031]
    assert report["odtp_port"] == 54006
    assert report["obp_proxy_port"] == 62032
    assert report["plugins"] == ["voice-announcements"]
    assert report["obp_links"] == [
        {"name": "OBP-2141", "network_id": 21410, "enhanced": False, "connected": None}
    ]
    text = repr(report)
    for secret in ("secret", "192.0.2.10", "10.0.0.2", "adn_x"):
        assert secret not in text


def test_server_id_already_normalized_to_bytes():
    config = _config(ENDPOINT="https://panel.example/api", TOKEN="adn_x")
    config["GLOBAL"]["SERVER_ID"] = (21310).to_bytes(4, "big")
    report = build_panel_report(
        config, version="2.6.1", uptime_s=0, settings=panel_report_settings(config), now=2000.0
    )
    assert report["server_id"] == 21310


def test_without_proxy_the_master_ports_are_reported():
    config = _config(ENDPOINT="https://panel.example/api", TOKEN="adn_x")
    del config["PROXY"]
    report = build_panel_report(
        config, version="2.6.1", uptime_s=0, settings=panel_report_settings(config), now=2000.0
    )
    assert report["hbp_ports"] == [54000]
    assert report["odtp_port"] is None


def test_config_validation_of_the_section():
    validate_config(minimal_valid_config())  # the section is optional
    validate_config(
        minimal_valid_config(
            ADN_PANEL={"ENDPOINT": "https://panel.example/api", "TOKEN": "adn_x", "ODTP_PORT": 54006}
        )
    )
    with pytest.raises(ConfigError) as exc:
        validate_config(
            minimal_valid_config(
                ADN_PANEL={"ENDPOINT": "panel.example", "TOKEN": "adn_x", "REPORT_INTERVAL": 10}
            )
        )
    assert "ADN_PANEL.ENDPOINT" in str(exc.value)
    assert "ADN_PANEL.REPORT_INTERVAL" in str(exc.value)
