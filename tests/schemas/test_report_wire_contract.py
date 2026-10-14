# ADN DMR Peer Server - tests schemas report wire contract
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

"""ReportWire output must validate against report-v2.json and match committed examples."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import jsonschema
import pytest

from adn_server.domain.value_objects import bytes_4
from adn_server.infrastructure.twisted_adapters.report.opcodes import REPORT_OPCODES
from adn_server.infrastructure.twisted_adapters.report.wire import ReportWire

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "report-v2.json"
_EXAMPLES_DIR = _SCHEMA_PATH.parent / "examples"
_VOICE_CSV = "GROUP VOICE,START,RX,MASTER-A,2155905152,1001,3120001,2,52090"


@pytest.fixture(scope="module")
def validator() -> jsonschema.Draft202012Validator:
    with _SCHEMA_PATH.open(encoding="utf-8") as fh:
        return jsonschema.Draft202012Validator(json.load(fh))


def _dashboard_systems() -> dict:
    return {
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


def _wire_json(frame: bytes) -> dict:
    return json.loads(frame[1:].decode("utf-8"))


def test_report_wire_state_frames_match_dashboard_state_example(
    validator: jsonschema.Draft202012Validator,
) -> None:
    with (_EXAMPLES_DIR / "dashboard_state.json").open(encoding="utf-8") as fh:
        expected = json.load(fh)
    expected_wire = {key: value for key, value in expected.items() if key != "server_id"}
    wire = ReportWire()
    with patch("adn_server.infrastructure.twisted_adapters.report.wire.time") as mock_time:
        mock_time.time.return_value = expected["ts"]
        frames = wire.state_frames(_dashboard_systems(), force=True)
    assert len(frames) == 1
    assert frames[0][:1] == REPORT_OPCODES["STATE_SND"]
    payload = _wire_json(frames[0])
    assert json.loads(json.dumps(payload)) == expected_wire
    validator.validate({**payload, "server_id": expected.get("server_id", "7302")})


def test_report_wire_bridge_event_frames_match_voice_event_example(
    validator: jsonschema.Draft202012Validator,
) -> None:
    with (_EXAMPLES_DIR / "voice_event.json").open(encoding="utf-8") as fh:
        expected = json.load(fh)
    wire = ReportWire()
    frames = wire.bridge_event_frames(_VOICE_CSV)
    assert len(frames) == 1
    assert frames[0][:1] == REPORT_OPCODES["VOICE_EVENT_SND"]
    payload = _wire_json(frames[0])
    assert payload["type"] == expected["type"]
    for key, value in expected.items():
        if key == "ts":
            continue
        assert payload[key] == value
    validator.validate({**payload, "ts": expected["ts"]})


def test_topology_of_a_hotspot_with_options_validates(validator: jsonschema.Draft202012Validator) -> None:
    # A logged-in hotspot with OPTIONS carries connected_at and options in its peer row.
    from adn_server.application.report.payloads import build_topology

    systems = _dashboard_systems()
    peer = systems["MASTER-A"]["PEERS"][bytes_4(3120001)]
    peer.update(CONNECTED=1_790_000_000, OPTIONS=b"TS2=91;TIMER=30;")
    topology = build_topology(systems, seq=1, ts=1_790_000_100.0)
    row = topology["systems"][0]["peers"][0]
    assert {"connected_at", "options"} <= row.keys()
    validator.validate(topology)
