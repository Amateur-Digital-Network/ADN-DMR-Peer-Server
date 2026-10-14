# ADN DMR Peer Server - tests infrastructure doctor
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

"""adn-server --doctor checks."""

from __future__ import annotations

import textwrap

from adn_server.infrastructure.doctor import collect_findings, run_doctor


def test_doctor_echo_requires_peer_system(tmp_path) -> None:
    cfg = tmp_path / "adn-echo.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            GLOBAL:
              SERVER_ID: 9990
            LOGGER:
              LOG_LEVEL: INFO
            SYSTEMS:
              ECHO:
                MODE: MASTER
                ENABLED: true
            """
        ),
        encoding="utf-8",
    )
    code = run_doctor(str(cfg), str(tmp_path), echo=True, version="test")
    assert code == 1


def test_doctor_ok_minimal_peer(tmp_path) -> None:
    cfg = tmp_path / "adn-server.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            GLOBAL:
              SERVER_ID: 1
            LOGGER:
              LOG_LEVEL: INFO
            REPORTS:
              REPORT: false
            SYSTEMS:
              P1:
                MODE: PEER
                ENABLED: true
                MASTER_IP: 127.0.0.1
                MASTER_PORT: 56400
            """
        ),
        encoding="utf-8",
    )
    code = run_doctor(str(cfg), str(tmp_path), version="test")
    assert code == 0


def test_collect_findings_peer_mesh_protocol() -> None:
    config = {
        "GLOBAL": {"SERVER_ID": 1},
        "SYSTEMS": {
            "P1": {
                "MODE": "PEER",
                "ENABLED": True,
                "MASTER_IP": "127.0.0.1",
                "MASTER_PORT": 56400,
                "MESH_PROTOCOL": "dmre_v5",
            }
        },
    }
    findings = collect_findings(config, project_root=".", config_path="cfg.yaml")
    peer_msgs = [f.message for f in findings if f.section == "peer"]
    assert any("MESH_PROTOCOL=dmre_v5" in m for m in peer_msgs)


def test_collect_findings_warns_when_two_bridges_share_a_passphrase() -> None:
    """Without it nothing tells the operator their control frames are only being
    told apart by source address. The passphrase itself must not be printed."""
    config = {
        "GLOBAL": {"SERVER_ID": 7301},
        "REPORTS": {"REPORT": False},
        "SYSTEMS": {
            "OBP-A": {"MODE": "OPENBRIDGE", "ENABLED": True, "NETWORK_ID": 1, "PASSPHRASE": "shared"},
            "OBP-B": {"MODE": "OPENBRIDGE", "ENABLED": True, "NETWORK_ID": 2, "PASSPHRASE": "shared"},
        },
    }
    findings = collect_findings(config, project_root=".", config_path="cfg.yaml")
    shared = [f for f in findings if "same PASSPHRASE" in f.message]
    assert len(shared) == 1
    assert shared[0].level == "warn"
    assert "OBP-A, OBP-B" in shared[0].message
    assert not any("shared" in f.message for f in findings)


def test_collect_findings_obp_per_bridge_migration() -> None:
    """7301-style: OBP-CL2 on fan-in 62032; another bridge keeps legacy 62999."""
    config = {
        "GLOBAL": {"SERVER_ID": 7301},
        "REPORTS": {"REPORT": False},
        "PROXY": {"LISTEN_PORT": 62031, "TARGET_SYSTEM": "HOTSPOT"},
        "OBP_PROXY": {"ENABLED": True, "LISTEN_PORT": 62032, "BIND_LEGACY_PORTS": True},
        "SYSTEMS": {
            "HOTSPOT": {"MODE": "MASTER", "ENABLED": True, "MAX_PEERS": 1},
            "OBP-CL2": {
                "MODE": "OPENBRIDGE",
                "ENABLED": True,
                "PORT": 62032,
                "NETWORK_ID": 7302,
                "PASSPHRASE": "dev",
                "TARGET_IP": "44.31.61.68",
                "TARGET_PORT": 62032,
            },
            "OBP-EU": {
                "MODE": "OPENBRIDGE",
                "ENABLED": True,
                "PORT": 62999,
                "NETWORK_ID": 73045,
                "PASSPHRASE": "dev",
                "TARGET_IP": "10.0.0.1",
                "TARGET_PORT": 62044,
            },
        },
    }
    findings = collect_findings(config, project_root=".", config_path="cfg.yaml")
    messages = [f.message for f in findings]
    assert any("OBP-CL2" in m and "fan-in only" in m for m in messages)
    assert any("OBP-EU" in m and "legacy UDP" in m and ":62999" in m for m in messages)


def test_collect_findings_obp_fanin_only_with_remote_target() -> None:
    """7302-style doctor: inject-only local; TARGET_PORT points at peer OBP_PROXY fan-in."""
    config = {
        "GLOBAL": {"SERVER_ID": 7302},
        "REPORTS": {"REPORT": False},
        "PROXY": {"LISTEN_PORT": 62031, "TARGET_SYSTEM": "HOTSPOT"},
        "OBP_PROXY": {"ENABLED": True, "LISTEN_PORT": 62032, "BIND_LEGACY_PORTS": False},
        "SYSTEMS": {
            "HOTSPOT": {"MODE": "MASTER", "ENABLED": True, "MAX_PEERS": 1},
            "OBP-CL": {
                "MODE": "OPENBRIDGE",
                "ENABLED": True,
                "PORT": 62032,
                "NETWORK_ID": 73010,
                "PASSPHRASE": "dev",
                "TARGET_IP": "44.31.61.66",
                "TARGET_PORT": 62032,
            },
        },
    }
    findings = collect_findings(config, project_root=".", config_path="cfg.yaml")
    messages = [f.message for f in findings]
    assert any("OBP-CL" in m and "fan-in only" in m for m in messages)
    assert any("target 44.31.61.66:62032" in m for m in messages)
    assert any("OBP_PROXY UDP" in m and ":62032" in m for m in messages)
