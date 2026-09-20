# ADN DMR Peer Server - tests application dashboard state
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

"""Minimal dashboard_state payload."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from adn_server.application.report.dashboard_state import build_dashboard_state
from adn_server.domain.mesh_session import MeshSessionStore
from adn_server.domain.value_objects import bytes_4

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "report-v2.json"
_EXAMPLES_DIR = _SCHEMA_PATH.parent / "examples"


@pytest.fixture(scope="module")
def validator() -> jsonschema.Draft202012Validator:
    with _SCHEMA_PATH.open(encoding="utf-8") as fh:
        return jsonschema.Draft202012Validator(json.load(fh))


def test_dashboard_state_omits_idle_masters():
    systems = {
        "SYSTEM-0": {
            "MODE": "MASTER",
            "ENABLED": True,
            "PEERS": {
                b"\x00\x00\x00\x01": {"CONNECTION": "NO", "CONNECTED": 0},
            },
        },
        "ECHO": {
            "MODE": "MASTER",
            "ENABLED": True,
            "PEERS": {
                b"\x00\x00\x00\x02": {"CONNECTION": "YES", "CONNECTED": 1000, "IP": "10.0.0.2"},
            },
        },
    }
    state = build_dashboard_state(systems, server_id="7302")
    assert state["type"] == "dashboard_state"
    assert state["server_id"] == "7302"
    assert "SYSTEM-0" not in state["ctable"]["MASTERS"]
    assert "ECHO" in state["ctable"]["MASTERS"]
    assert 2 in state["ctable"]["MASTERS"]["ECHO"]["peers"]


def test_dashboard_state_includes_master_static_tgs_on_peers():
    systems = {
        "MASTER-A": {
            "MODE": "MASTER",
            "ENABLED": True,
            "TS1_STATIC": "91,92",
            "TS2_STATIC": "730",
            "PEERS": {
                b"\x00\x2f\xd0\x31": {"CONNECTION": "YES", "CONNECTED": 1000},
            },
        },
    }
    state = build_dashboard_state(systems)
    peers = state["ctable"]["MASTERS"]["MASTER-A"]["peers"]
    assert len(peers) == 1
    peer = next(iter(peers.values()))
    assert peer["ts1_static"] == ["91", "92"]
    assert peer["ts2_static"] == ["730"]


def test_dashboard_state_includes_enabled_openbridge():
    systems = {
        "OBP-CL": {
            "MODE": "OPENBRIDGE",
            "ENABLED": True,
            "IP": "44.31.61.68",
            "PORT": 62999,
            "NETWORK_ID": 73010,
            "ENHANCED_OBP": True,
            "PEERS": {},
        },
    }
    state = build_dashboard_state(systems)
    obp = state["ctable"]["OPENBRIDGES"]["OBP-CL"]
    assert obp["mode"] == "OPENBRIDGE"
    assert obp["network_id"] == 73010
    assert obp["enhanced_obp"] is True
    assert obp["connected"] is False
    assert obp["streams"] == {}


def test_openbridge_connected_true_when_bcka_fresh() -> None:
    now = 1000.0
    systems = {
        "OBP-CL": {
            "MODE": "OPENBRIDGE",
            "ENABLED": True,
            "NETWORK_ID": 73010,
            "ENHANCED_OBP": True,
            "PEERS": {},
        },
    }
    sessions = MeshSessionStore()
    sessions.session("OBP-CL").note_keepalive(now - 10)
    state = build_dashboard_state(systems, ts=now, sessions=sessions)
    assert state["ctable"]["OPENBRIDGES"]["OBP-CL"]["connected"] is True


def test_openbridge_connected_false_when_bcka_stale() -> None:
    now = 1000.0
    systems = {
        "OBP-CL": {
            "MODE": "OPENBRIDGE",
            "ENABLED": True,
            "NETWORK_ID": 73010,
            "ENHANCED_OBP": True,
            "PEERS": {},
        },
    }
    sessions = MeshSessionStore()
    sessions.session("OBP-CL").note_keepalive(now - 61)
    state = build_dashboard_state(systems, ts=now, sessions=sessions)
    assert state["ctable"]["OPENBRIDGES"]["OBP-CL"]["connected"] is False


def test_openbridge_connected_false_when_bcka_missing() -> None:
    now = 1000.0
    systems = {
        "OBP-CL": {
            "MODE": "OPENBRIDGE",
            "ENABLED": True,
            "NETWORK_ID": 73010,
            "ENHANCED_OBP": True,
            "PEERS": {},
        },
    }
    state = build_dashboard_state(systems, ts=now)
    assert state["ctable"]["OPENBRIDGES"]["OBP-CL"]["connected"] is False


def test_openbridge_connected_omitted_without_enhanced_obp() -> None:
    systems = {
        "OBP-CL": {
            "MODE": "OPENBRIDGE",
            "ENABLED": True,
            "NETWORK_ID": 73010,
            "PEERS": {},
        },
    }
    state = build_dashboard_state(systems)
    obp = state["ctable"]["OPENBRIDGES"]["OBP-CL"]
    assert "connected" not in obp


def test_dashboard_state_includes_connected_upstream_peer():
    systems = {
        "XLX-730": {
            "MODE": "XLXPEER",
            "ENABLED": True,
            "CALLSIGN": "XLX730",
            "RADIO_ID": 730,
            "XLXSTATS": {"CONNECTION": "YES", "CONNECTED": 1700000000},
            "PEERS": {},
        },
    }
    state = build_dashboard_state(systems)
    assert "XLX-730" in state["ctable"]["PEERS"]
    assert state["ctable"]["PEERS"]["XLX-730"]["connected"] is True
    assert state["ctable"]["MASTERS"] == {}


def test_build_dashboard_state_matches_example(validator: jsonschema.Draft202012Validator) -> None:
    with (_EXAMPLES_DIR / "dashboard_state.json").open(encoding="utf-8") as fh:
        expected = json.load(fh)
    systems = {
        "MASTER-A": {
            "MODE": "MASTER",
            "ENABLED": True,
            "IP": "10.0.0.1",
            "PORT": 62030,
            "SINGLE_MODE": False,
            "DEFAULT_UA_TIMER": 10,
            "TS1_STATIC": "91,92",
            "TS2_STATIC": "7302",
            "PEERS": {
                bytes_4(3120001): {
                    "CONNECTION": "YES",
                    "CONNECTED": 1717555200,
                    "IP": "10.0.0.50",
                    "PORT": 62031,
                    "CALLSIGN": b"CE5RPY  ",
                },
            },
        },
        "MASTER-B": {
            "MODE": "MASTER",
            "ENABLED": True,
            "IP": "10.0.0.2",
            "PORT": 62040,
            "PEERS": {
                bytes_4(3120002): {
                    "CONNECTION": "YES",
                    "CONNECTED": 1717555200,
                    "IP": "10.0.0.51",
                    "PORT": 62033,
                    "CALLSIGN": b"EA5GVK  ",
                },
            },
        },
        "OBP-CL": {
            "MODE": "OPENBRIDGE",
            "ENABLED": True,
            "IP": "44.31.61.68",
            "PORT": 62999,
            "NETWORK_ID": 73010,
            "ENHANCED_OBP": True,
            "PEERS": {},
        },
    }
    doc = build_dashboard_state(systems, server_id="7302", ts=expected["ts"])
    assert json.loads(json.dumps(doc)) == expected
    validator.validate(doc)
