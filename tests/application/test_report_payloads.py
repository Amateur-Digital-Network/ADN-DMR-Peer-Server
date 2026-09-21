# ADN DMR Peer Server - tests application report payloads
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

"""Unit tests for report payload builders."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from adn_server.application.report import (
    build_routing_table,
    build_topology,
    hello_connected_system_names,
    parse_bridge_event_csv,
    routing_table_delta,
)
from adn_server.application.report.payloads import (
    parse_peer_options_static,
    peer_options_static_valid,
    resolve_peer_single_and_timer,
)
from adn_server.application.routing.helpers import peer_should_receive_group_voice
from adn_server.domain import bytes_3, bytes_4

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "report-v2.json"
_EXAMPLES_DIR = _SCHEMA_PATH.parent / "examples"


@pytest.fixture(scope="module")
def validator() -> jsonschema.Draft202012Validator:
    with _SCHEMA_PATH.open(encoding="utf-8") as fh:
        schema = json.load(fh)
    return jsonschema.Draft202012Validator(schema)


def test_parse_peer_options_static_ts2():
    ts1, ts2 = parse_peer_options_static(b"TS2=730444;TIMER=15;")
    assert ts1 == []
    assert ts2 == ["730444"]


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        (b"", True),
        (b"TS2=730444;TIMER=15;", True),
        (b"TS2=bad;TIMER=15;", False),
        (b"PASS=secret;TS2=730;", False),
        (b"PASS=secret;", False),
    ],
)
def test_peer_options_static_valid_table(options: bytes, expected: bool) -> None:
    assert peer_options_static_valid(options) is expected


def test_resolve_peer_single_and_timer_yaml_defaults() -> None:
    yaml_cfg = {"SINGLE_MODE": False, "DEFAULT_UA_TIMER": 60}
    single, timer = resolve_peer_single_and_timer({}, yaml_cfg)
    assert single is False
    assert timer == 60.0


def test_resolve_peer_single_and_timer_options_override_yaml() -> None:
    yaml_cfg = {"SINGLE_MODE": False, "DEFAULT_UA_TIMER": 60}
    fields = {"SINGLE": "1", "TIMER": 5.0}
    single, timer = resolve_peer_single_and_timer(fields, yaml_cfg)
    assert single is True
    assert timer == 5.0


def test_parse_peer_options_static_strips_wrapping_quotes() -> None:
    """MMDVM_DMO hotspots may wrap OPTIONS in double quotes (CA1ROG / 7301896)."""
    ts1, ts2 = parse_peer_options_static(b'"TS2=730444;VOICE=0;TIMER=300;"')
    assert ts1 == []
    assert ts2 == ["730444"]


def test_quoted_options_eligible_for_group_voice_downlink() -> None:
    peer = {"OPTIONS": b'"TS2=730444;VOICE=0;TIMER=300;"'}
    assert peer_should_receive_group_voice(peer, 2, 730444, connected_count=8)


def test_build_topology_exports_peer_options_static() -> None:
    systems = {
        "SYSTEM-10": {
            "MODE": "MASTER",
            "ENABLED": True,
            "PEERS": {
                bytes_3(7301896): {
                    "CONNECTION": "YES",
                    "CONNECTED": 1000,
                    "OPTIONS": b"TS2=730444;TIMER=15;",
                },
            },
        },
    }
    doc = build_topology(systems, seq=1)
    peer = doc["systems"][0]["peers"][0]
    assert peer["ts2_static"] == ["730444"]
    assert peer["options"] == "TS2=730444;TIMER=15;"


def test_build_topology_omits_pass_from_peer_options() -> None:
    systems = {
        "SYSTEM": {
            "MODE": "MASTER",
            "ENABLED": True,
            "PEERS": {
                bytes_4(730039101): {
                    "CONNECTION": "YES",
                    "OPTIONS": b"PASS=secret123;TS2=730;SINGLE=1;",
                },
            },
        },
    }
    doc = build_topology(systems, seq=1)
    peer = doc["systems"][0]["peers"][0]
    assert "options" not in peer


def test_build_topology_exports_master_static_tgs() -> None:
    systems = {
        "MASTER-A": {
            "MODE": "MASTER",
            "ENABLED": True,
            "TS1_STATIC": "91,92",
            "TS2_STATIC": "730",
            "PEERS": {},
        },
    }
    doc = build_topology(systems, seq=1)
    master = doc["systems"][0]
    assert master["ts1_static"] == ["91", "92"]
    assert master["ts2_static"] == ["730"]


def test_build_topology_exports_peer_connected_at() -> None:
    login_ts = 1717555100.0
    systems = {
        "MASTER-A": {
            "MODE": "MASTER",
            "ENABLED": True,
            "PEERS": {
                bytes_3(3120001): {
                    "CONNECTION": "YES",
                    "CONNECTED": login_ts,
                }
            },
        },
    }
    doc = build_topology(systems, seq=1, ts=1717555200.0)
    peer = doc["systems"][0]["peers"][0]
    assert peer["connected_at"] == int(login_ts)


def test_build_topology_matches_example(validator: jsonschema.Draft202012Validator) -> None:
    with (_EXAMPLES_DIR / "topology.json").open(encoding="utf-8") as fh:
        expected = json.load(fh)
    systems = {
        "MASTER-A": {
            "MODE": "MASTER",
            "ENABLED": True,
            "IP": "10.0.0.1",
            "PORT": 62030,
            "REPEAT": True,
            "PEERS": {
                bytes_3(3120001): {
                    "CONNECTION": "YES",
                    "IP": "10.0.0.50",
                    "PORT": 62031,
                }
            },
        },
        "OBP-CL": {
            "MODE": "OPENBRIDGE",
            "ENABLED": True,
            "IP": "10.0.0.2",
            "PORT": 62044,
            "ENHANCED_OBP": True,
            "PEERS": {},
        },
    }
    doc = build_topology(systems, seq=expected["seq"], ts=expected["ts"])
    assert doc == expected
    validator.validate(doc)


def test_build_routing_table_matches_example(validator: jsonschema.Draft202012Validator) -> None:
    with (_EXAMPLES_DIR / "routing_table.json").open(encoding="utf-8") as fh:
        expected = json.load(fh)
    bridges = {
        "52090": [
            {
                "SYSTEM": "MASTER-A",
                "TS": 2,
                "TGID": 52090,
                "ACTIVE": True,
                "TO_TYPE": "ON",
                "TIMER": 1717555320.0,
            },
            {
                "SYSTEM": "MASTER-B",
                "TS": 2,
                "TGID": 52090,
                "ACTIVE": True,
                "TO_TYPE": "ON",
            },
        ],
        "#310": [
            {
                "SYSTEM": "MASTER-A",
                "TS": 2,
                "TGID": 310,
                "ACTIVE": False,
                "TO_TYPE": "NONE",
            }
        ],
    }
    doc = build_routing_table(bridges, seq=expected["seq"], ts=expected["ts"])
    assert doc == expected
    validator.validate(doc)


def test_parse_group_voice_start_matches_example(validator: jsonschema.Draft202012Validator) -> None:
    with (_EXAMPLES_DIR / "voice_event.json").open(encoding="utf-8") as fh:
        expected = json.load(fh)
    csv = "GROUP VOICE,START,RX,MASTER-A,2155905152,1001,3120001,2,52090"
    doc = parse_bridge_event_csv(csv)
    assert doc is not None
    doc["ts"] = expected["ts"]
    assert doc == expected
    validator.validate(doc)


@pytest.mark.parametrize(
    "label",
    [
        "UNIT CSBK",
        "UNIT DATA HEADER",
        "UNIT VCSBK 1/2 DATA BLOCK",
        "UNIT VCSBK 3/4 DATA BLOCK",
    ],
)
def test_parse_unit_dtype_labels_map_to_unit_family(label: str) -> None:
    """Regression: routing_use_cases._dtype_labels emits these instead of the
    generic "UNIT DATA" for dtype_vseq in (3, 6, 7, 8) — private-call CSBK
    setup and SMS/GPS header/VCSBK blocks. Before these were added to
    _CSV_FAMILIES, parse_bridge_event_csv() returned None and the event was
    silently dropped, so a private call whose CSBK handshake never escalates
    to voice never reached the monitor's Last Heard log at all."""
    csv = f"{label},DATA,RX,SYSTEM-8,656846929,730039210,7300392,2,714001"
    doc = parse_bridge_event_csv(csv)
    assert doc is not None
    assert doc["call_family"] == "UNIT"
    assert doc["phase"] == "DATA"


def test_parse_bridge_event_csv_unknown_family_still_none() -> None:
    csv = "SOMETHING ELSE,DATA,RX,SYSTEM-8,656846929,730039210,7300392,2,714001"
    assert parse_bridge_event_csv(csv) is None


def test_parse_private_voice_tx_captures_trailing_dest_peer_id() -> None:
    """Regression: the JSON voice_event schema had no field for the destination
    hotspot's peer id (routing_use_cases._pvt_call_received appends it after the
    legacy CSV fields) -- it was silently dropped in this CSV->JSON conversion,
    so the monitor could never know which single hotspot was receiving a private
    call, and always reconstructed a bare "0" in its place (report_mapper.
    voice_event_to_csv_parts's is_announcement default)."""
    csv = "PRIVATE VOICE,START,TX,SYSTEM,1610544978,730039110,7300391,2,7300392,730039101"
    doc = parse_bridge_event_csv(csv)
    assert doc is not None
    assert doc["dest_peer_id"] == 730039101
    assert doc["is_announcement"] is False


def test_parse_private_voice_end_tx_captures_dest_peer_id_after_duration() -> None:
    csv = "PRIVATE VOICE,END,TX,SYSTEM,1610544978,730039110,7300391,2,7300392,5.87,730039101"
    doc = parse_bridge_event_csv(csv)
    assert doc is not None
    assert doc["dest_peer_id"] == 730039101
    assert doc["duration_s"] == pytest.approx(5.87)


def test_parse_private_voice_rx_has_no_dest_peer_id() -> None:
    csv = "PRIVATE VOICE,START,RX,SYSTEM,1610544978,730039110,7300391,2,7300392"
    doc = parse_bridge_event_csv(csv)
    assert doc is not None
    assert "dest_peer_id" not in doc


def test_routing_delta_matches_example(validator: jsonschema.Draft202012Validator) -> None:
    with (_EXAMPLES_DIR / "routing_table.json").open(encoding="utf-8") as fh:
        previous = json.load(fh)
    with (_EXAMPLES_DIR / "delta.json").open(encoding="utf-8") as fh:
        expected = json.load(fh)

    def _legs_to_bridge(route: dict) -> list[dict]:
        rows = []
        for leg in route["legs"]:
            row = {
                "SYSTEM": leg["system"],
                "TS": leg["ts"],
                "TGID": leg["tgid"],
                "ACTIVE": leg["active"],
                "TO_TYPE": leg["to_type"],
            }
            if "timer_expires_at" in leg:
                row["TIMER"] = leg["timer_expires_at"]
            rows.append(row)
        return rows

    bridges = {
        "52090": _legs_to_bridge(expected["patch"]["routes"][0]),
        "#310": _legs_to_bridge(previous["routes"][1]),
    }
    current = build_routing_table(bridges, seq=expected["patch"]["seq"], ts=expected["patch"]["ts"])
    delta = routing_table_delta(previous, current, seq=expected["seq"], ts=expected["ts"])
    assert delta == expected
    validator.validate(delta)


def test_build_topology_includes_openbridge_network_id() -> None:
    net = (73010).to_bytes(4, "big")
    systems = {
        "OBP-CL": {
            "MODE": "OPENBRIDGE",
            "ENABLED": True,
            "NETWORK_ID": net,
            "PEERS": {},
        }
    }
    doc = build_topology(systems, seq=1, ts=1.0)
    assert doc["systems"][0]["network_id"] == 73010


def test_build_topology_includes_peer_display_fields() -> None:
    systems = {
        "MASTER-A": {
            "MODE": "MASTER",
            "ENABLED": True,
            "PEERS": {
                bytes_3(3120001): {
                    "CONNECTION": "YES",
                    "CALLSIGN": b"CE5RPY  ",
                    "RX_FREQ": b"145625000",
                    "TX_FREQ": b"145625000",
                    "LOCATION": b"Chile               ",
                    "SLOTS": b"2",
                }
            },
        }
    }
    doc = build_topology(systems, seq=1, ts=1.0)
    peer = doc["systems"][0]["peers"][0]
    assert peer["callsign"] == "CE5RPY"
    assert peer["rx_freq"] == "145625000"
    assert peer["tx_freq"] == "145625000"
    assert peer["slots"] == "2"


def test_build_topology_includes_peer_coordinates(validator: jsonschema.Draft202012Validator) -> None:
    systems = {
        "MASTER-A": {
            "MODE": "MASTER",
            "ENABLED": True,
            "PEERS": {
                bytes_3(3120001): {
                    "CONNECTION": "YES",
                    "CALLSIGN": b"CE5RPY  ",
                    "LATITUDE": b"-33.4489\x00",
                    "LONGITUDE": b"-70.6693\x00",
                    "HEIGHT": b"12\x00",
                }
            },
        }
    }
    doc = build_topology(systems, seq=1, ts=1.0)
    validator.validate(doc)
    peer = doc["systems"][0]["peers"][0]
    assert peer["latitude"] == "-33.4489"
    assert peer["longitude"] == "-70.6693"
    assert peer["height"] == "12"


def test_build_topology_omits_empty_peer_coordinates() -> None:
    systems = {
        "MASTER-A": {
            "MODE": "MASTER",
            "ENABLED": True,
            "PEERS": {
                bytes_3(3120001): {
                    "CONNECTION": "YES",
                    "CALLSIGN": b"CE5RPY  ",
                    "LATITUDE": b"",
                    "LONGITUDE": b"",
                    "HEIGHT": b"",
                }
            },
        }
    }
    doc = build_topology(systems, seq=1, ts=1.0)
    peer = doc["systems"][0]["peers"][0]
    assert "latitude" not in peer
    assert "longitude" not in peer
    assert "height" not in peer


def test_topology_strips_nul_padded_callsign() -> None:
    systems = {
        "MASTER-A": {
            "MODE": "MASTER",
            "ENABLED": True,
            "PEERS": {
                bytes_3(3120001): {
                    "CONNECTION": "YES",
                    "CALLSIGN": b"Bridge\x00\x00",
                }
            },
        }
    }
    doc = build_topology(systems, seq=1, ts=1.0)
    assert doc["systems"][0]["peers"][0]["callsign"] == "Bridge"


def test_parse_peer_options_static_strips_nul_padding() -> None:
    raw = b"TS1=73080;TS2=73081;SINGLE=1;" + b"\x00" * 12
    ts1, ts2 = parse_peer_options_static(raw)
    assert ts1 == ["73080"]
    assert ts2 == ["73081"]


def test_hello_connected_system_names_only_live_systems() -> None:
    systems = {
        "SYSTEM-0": {
            "MODE": "MASTER",
            "ENABLED": True,
            "PEERS": {
                1001: {"CONNECTION": "YES"},
                1002: {"CONNECTION": "NO"},
            },
        },
        "SYSTEM-1": {
            "MODE": "MASTER",
            "ENABLED": True,
            "PEERS": {2001: {"CONNECTION": "NO"}},
        },
        "OBP-CL": {
            "MODE": "OPENBRIDGE",
            "ENABLED": True,
            "PEERS": {},
        },
        "XLX-1": {
            "MODE": "XLXPEER",
            "ENABLED": True,
            "XLXSTATS": {"CONNECTION": "YES"},
        },
        "DISABLED": {
            "MODE": "MASTER",
            "ENABLED": False,
            "PEERS": {1: {"CONNECTION": "YES"}},
        },
    }
    assert hello_connected_system_names(systems) == ["SYSTEM-0", "XLX-1"]
