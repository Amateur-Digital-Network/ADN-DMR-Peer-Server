# ADN DMR Peer Server - tests routing unit data routing
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

"""Unit data routing: SUB_MAP, hotspot, gateway, OBP fanout."""

from __future__ import annotations

import pytest
from tests.harness.assertions import assert_forwarded, assert_not_forwarded
from tests.harness.deterministic import (
    DeterministicScenario,
    FakeClock,
    PacketSpec,
    add_openbridge_system,
    minimal_config,
    parse_dmr_fields,
    patch_routing_wall_time,
)
from tests.routing.unit_data_helpers import idle_hbp_slot

from adn_server.domain import bytes_3, bytes_4
from adn_server.domain.hbp_protocol import HBPF_SLT_VHEAD, HBPF_SLT_VTERM

DAPRS_GATEWAY_ID = 900999
HOTSPOT_SUB_ID = 7300392
_HARNESS_T0 = FakeClock().time()


@pytest.mark.behavior
def test_unit_data_hbp_forward_wire_format_matches_legacy_send_data_to_hbp() -> None:
    """Server is a transparent bridge: only TS bit may change; DMR payload [20:53] is untouched."""
    config = minimal_config(("SYSTEM", "D-APRS"))
    config["SYSTEMS"]["D-APRS"]["PEERS"] = {
        DAPRS_GATEWAY_ID: {"CALLSIGN": "D-APRS", "IP": "127.0.0.1", "PORT": 52555},
    }
    config["SYSTEMS"]["D-APRS"]["GROUP_HANGTIME"] = 0
    scenario = DeterministicScenario(config=config)
    scenario.protocols["D-APRS"].STATUS[2] = idle_hbp_slot()
    payload = bytes(range(33))  # distinct pattern for dmrpkt [20:53]
    base = PacketSpec(
        call_type="unit",
        rf_src=HOTSPOT_SUB_ID,
        dst_id=DAPRS_GATEWAY_ID,
        stream_id=0xDEADBEEF,
        slot=1,
        dtype_vseq=6,
        payload=payload,
    )
    spec = DeterministicScenario.unit_data_header_spec(base)
    ingress = spec.data()
    scenario.inject_unit("SYSTEM", spec)
    forwarded = scenario.capture.for_system("D-APRS")
    assert len(forwarded) == 1
    out = forwarded[0].packet
    assert out[:15] == ingress[:15]
    assert out[16:20] == ingress[16:20]
    assert out[20:53] == ingress[20:53]
    assert out[8:11] == ingress[8:11]
    assert out[5:8] == ingress[5:8]
    assert (out[15] ^ ingress[15]) == 0x80


@pytest.mark.behavior
def test_unit_data_sub_map_forwards_to_idle_hbp() -> None:
    config = minimal_config(("MASTER-A", "MASTER-B"))
    # 6-digit dst: only unit_data SUB_MAP path (pvt_call runs for 7-digit only, legacy ~3253).
    dst_sub = 712345
    config["_SUB_MAP"] = {bytes_3(dst_sub): ("MASTER-B", 2, 1000.0)}
    scenario = DeterministicScenario(config=config)
    scenario.protocols["MASTER-B"].STATUS[2] = idle_hbp_slot()
    base = PacketSpec(call_type="unit", dst_id=dst_sub, stream_id=0x52525252, slot=2)

    scenario.inject_unit("MASTER-A", DeterministicScenario.unit_data_header_spec(base))

    assert_forwarded(scenario, "MASTER-B", count=1, call_type="unit")


@pytest.mark.behavior
def test_unit_data_7digit_sub_map_idle_forwards_via_both_legacy_paths() -> None:
    """Legacy parity: dtype 6/7/8 to 7-digit dst runs unit_data + pvt_call_received."""
    config = minimal_config(("MASTER-A", "MASTER-B"))
    dst_sub = 7123456
    config["_SUB_MAP"] = {bytes_3(dst_sub): ("MASTER-B", 2, 1000.0)}
    scenario = DeterministicScenario(config=config)
    scenario.protocols["MASTER-B"].STATUS[2] = idle_hbp_slot()
    base = PacketSpec(call_type="unit", dst_id=dst_sub, stream_id=0x52525252, slot=2)

    scenario.inject_unit("MASTER-A", DeterministicScenario.unit_data_header_spec(base))

    assert_forwarded(scenario, "MASTER-B", count=2, call_type="unit")


def test_unit_data_sub_map_skips_busy_hbp_target() -> None:
    config = minimal_config(("MASTER-A", "MASTER-B"))
    # 6-digit dst isolates SUB_MAP busy check (no pvt_call fallback).
    dst_sub = 712345
    config["_SUB_MAP"] = {bytes_3(dst_sub): ("MASTER-B", 2, 1000.0)}
    scenario = DeterministicScenario(config=config)
    scenario.protocols["MASTER-B"].STATUS[2] = {
        "RX_TYPE": HBPF_SLT_VHEAD,
        "TX_TYPE": HBPF_SLT_VTERM,
        "TX_TIME": scenario.clock.time(),
    }
    base = PacketSpec(call_type="unit", dst_id=dst_sub, stream_id=0x53535353, slot=2)

    scenario.inject_unit("MASTER-A", DeterministicScenario.unit_data_header_spec(base))

    assert_not_forwarded(scenario, "MASTER-B")


@pytest.mark.behavior
def test_unit_data_hotspot_peer_match_forwards_to_idle_hbp() -> None:
    """Regression: hotspot peer ID match forwards unit data to idle MASTER slot."""
    config = minimal_config(("MASTER-A", "MASTER-B"))
    config["SYSTEMS"]["MASTER-B"]["PEERS"] = {
        1234567890: {"CALLSIGN": "HS1", "IP": "127.0.0.1", "PORT": 62040},
    }
    config["SYSTEMS"]["MASTER-B"]["GROUP_HANGTIME"] = 0
    scenario = DeterministicScenario(config=config)
    scenario.protocols["MASTER-B"].STATUS[2] = idle_hbp_slot()
    base = PacketSpec(call_type="unit", dst_id=123456, stream_id=0x54545454, slot=2)

    scenario.inject_unit("MASTER-A", DeterministicScenario.unit_data_header_spec(base))

    assert_forwarded(scenario, "MASTER-B", count=1, call_type="unit", dst_id=123456)


def test_unit_data_forwards_to_data_gateway_when_enabled() -> None:
    config = minimal_config(("MASTER-A",))
    config["GLOBAL"]["DATA_GATEWAY"] = True
    add_openbridge_system(config, "DATA-GATEWAY")
    config["SYSTEMS"]["DATA-GATEWAY"]["ENABLED"] = True
    scenario = DeterministicScenario(config=config)
    base = PacketSpec(call_type="unit", dst_id=5005, stream_id=0x65656565, slot=2)

    with patch_routing_wall_time(scenario.clock):
        scenario.inject_unit("MASTER-A", DeterministicScenario.unit_data_header_spec(base))

    assert len(scenario.capture.for_system("DATA-GATEWAY")) == 1


def test_unit_data_fanout_to_other_obp_with_ver_gt_1() -> None:
    config = minimal_config(("MASTER-A",))
    add_openbridge_system(config, "OBP-FAN")
    scenario = DeterministicScenario(config=config)
    base = PacketSpec(call_type="unit", dst_id=1000001, stream_id=0x66666666, slot=2)

    with patch_routing_wall_time(scenario.clock):
        scenario.inject_unit("MASTER-A", DeterministicScenario.unit_data_header_spec(base))

    assert len(scenario.capture.for_system("OBP-FAN")) == 1
    assert parse_dmr_fields(scenario.capture.for_system("OBP-FAN")[0].packet)["call_type"] == "unit"


@pytest.mark.behavior
def test_unit_data_to_fresh_local_sub_is_not_fanned_out_to_obp() -> None:
    """Unit data to a subscriber recently seen on a local system goes only via SUB_MAP."""
    config = minimal_config(("D-APRS", "SYSTEM"))
    add_openbridge_system(config, "OBP-FAN")
    config["_SUB_MAP"] = {bytes_3(HOTSPOT_SUB_ID): ("SYSTEM", 2, _HARNESS_T0 - 60)}
    scenario = DeterministicScenario(config=config)
    scenario.protocols["SYSTEM"].STATUS[2] = idle_hbp_slot()
    base = PacketSpec(call_type="unit", rf_src=DAPRS_GATEWAY_ID, dst_id=HOTSPOT_SUB_ID, stream_id=0x67676767, slot=2)

    with patch_routing_wall_time(scenario.clock):
        scenario.inject_unit("D-APRS", DeterministicScenario.unit_data_header_spec(base))

    assert len(scenario.capture.for_system("OBP-FAN")) == 0
    assert len(scenario.capture.for_system("SYSTEM")) >= 1


@pytest.mark.behavior
def test_unit_data_to_stale_local_sub_is_still_fanned_out_to_obp() -> None:
    config = minimal_config(("D-APRS", "SYSTEM"))
    add_openbridge_system(config, "OBP-FAN")
    config["_SUB_MAP"] = {bytes_3(HOTSPOT_SUB_ID): ("SYSTEM", 2, _HARNESS_T0 - 3600)}
    scenario = DeterministicScenario(config=config)
    scenario.protocols["SYSTEM"].STATUS[2] = idle_hbp_slot()
    base = PacketSpec(call_type="unit", rf_src=DAPRS_GATEWAY_ID, dst_id=HOTSPOT_SUB_ID, stream_id=0x68686868, slot=2)

    with patch_routing_wall_time(scenario.clock):
        scenario.inject_unit("D-APRS", DeterministicScenario.unit_data_header_spec(base))

    assert len(scenario.capture.for_system("OBP-FAN")) == 1


@pytest.mark.behavior
def test_unit_data_to_sub_learned_via_obp_is_still_fanned_out_to_obp() -> None:
    config = minimal_config(("D-APRS",))
    add_openbridge_system(config, "OBP-FAN")
    add_openbridge_system(config, "OBP-OTHER")
    config["_SUB_MAP"] = {bytes_3(HOTSPOT_SUB_ID): ("OBP-OTHER", 1, _HARNESS_T0 - 60)}
    scenario = DeterministicScenario(config=config)
    base = PacketSpec(call_type="unit", rf_src=DAPRS_GATEWAY_ID, dst_id=HOTSPOT_SUB_ID, stream_id=0x69696969, slot=2)

    with patch_routing_wall_time(scenario.clock):
        scenario.inject_unit("D-APRS", DeterministicScenario.unit_data_header_spec(base))

    assert len(scenario.capture.for_system("OBP-FAN")) == 1


@pytest.mark.behavior
def test_unit_data_daprs_peer_id_match_forwards() -> None:
    """Legacy parity: unit data routes when dst matches a PEER on D-APRS (runtime or static)."""
    for gateway_id in (DAPRS_GATEWAY_ID, 730999):
        config = minimal_config(("SYSTEM", "D-APRS"))
        config["SYSTEMS"]["D-APRS"]["PEERS"] = {
            gateway_id: {"CALLSIGN": "D-APRS", "IP": "127.0.0.1", "PORT": 52555},
        }
        config["SYSTEMS"]["D-APRS"]["GROUP_HANGTIME"] = 0
        scenario = DeterministicScenario(config=config)
        scenario.protocols["D-APRS"].STATUS[2] = idle_hbp_slot()
        base = PacketSpec(
            call_type="unit",
            rf_src=HOTSPOT_SUB_ID,
            dst_id=gateway_id,
            stream_id=0xAABBCCDD,
            slot=2,
        )
        scenario.inject_unit("SYSTEM", DeterministicScenario.unit_data_header_spec(base))
        assert_forwarded(scenario, "D-APRS", count=1, call_type="unit", dst_id=gateway_id)


@pytest.mark.behavior
def test_daprs_uplink_then_downlink_while_hotspot_transmitting() -> None:
    """D-APRS: uplink unit data must not mark SYSTEM slot busy; reply via SUB_MAP forwards."""
    config = minimal_config(("SYSTEM", "D-APRS"))
    config["SYSTEMS"]["D-APRS"]["PEERS"] = {
        DAPRS_GATEWAY_ID: {"CALLSIGN": "D-APRS", "IP": "127.0.0.1", "PORT": 52555},
    }
    config["SYSTEMS"]["D-APRS"]["GROUP_HANGTIME"] = 0
    config["_SUB_MAP"] = {bytes_3(HOTSPOT_SUB_ID): ("SYSTEM", 2, 1000.0)}
    scenario = DeterministicScenario(config=config)
    scenario.protocols["SYSTEM"].STATUS[2] = idle_hbp_slot()
    scenario.protocols["D-APRS"].STATUS[2] = idle_hbp_slot()

    uplink = PacketSpec(
        call_type="unit",
        rf_src=HOTSPOT_SUB_ID,
        dst_id=DAPRS_GATEWAY_ID,
        stream_id=0x41381248,
        slot=2,
    )
    scenario.inject_unit("SYSTEM", DeterministicScenario.unit_data_header_spec(uplink))
    assert scenario.protocols["SYSTEM"].STATUS[2]["RX_TYPE"] == HBPF_SLT_VTERM
    assert_forwarded(scenario, "D-APRS", count=1, call_type="unit")

    scenario.capture.packets.clear()
    downlink = PacketSpec(
        call_type="unit",
        rf_src=DAPRS_GATEWAY_ID,
        dst_id=HOTSPOT_SUB_ID,
        stream_id=0x33416424,
        slot=2,
    )
    scenario.inject_unit("D-APRS", DeterministicScenario.unit_data_header_spec(downlink))

    # Idle SYSTEM slot: legacy sends via sendDataToHBP and pvt_call send_system (7-digit dst).
    assert_forwarded(scenario, "SYSTEM", count=2, call_type="unit", dst_id=HOTSPOT_SUB_ID)


@pytest.mark.behavior
def test_unit_data_7digit_dst_uses_pvt_call_when_sub_map_idle_check_fails() -> None:
    """Legacy parity: dtype 6/7/8 to 7-digit dst also runs pvt_call_received (routerHBP ~3253)."""
    from adn_server.domain.hbp_protocol import HBPF_SLT_VHEAD

    config = minimal_config(("D-APRS", "SYSTEM"))
    config["_SUB_MAP"] = {bytes_3(HOTSPOT_SUB_ID): ("SYSTEM", 2, 1000.0)}
    scenario = DeterministicScenario(config=config)
    # SUB_MAP idle check fails (strict RX/TX VTERM), but pvt_call uses STREAM_TO contention.
    scenario.protocols["SYSTEM"].STATUS[2] = {
        "RX_TYPE": HBPF_SLT_VHEAD,
        "TX_TYPE": HBPF_SLT_VHEAD,
        "RX_TGID": bytes_3(900999),
        "TX_TGID": bytes_3(0),
        "RX_TIME": scenario.clock.time(),
        "TX_TIME": 0.0,
        "RX_STREAM_ID": bytes_3(0),
    }
    base = PacketSpec(
        call_type="unit",
        rf_src=900999,
        dst_id=HOTSPOT_SUB_ID,
        stream_id=0x86673819,
        slot=2,
    )
    scenario.inject_unit("D-APRS", DeterministicScenario.unit_data_header_spec(base))

    assert_forwarded(scenario, "SYSTEM", count=1, call_type="unit", dst_id=HOTSPOT_SUB_ID)


@pytest.mark.behavior
def test_unit_data_7digit_multi_frame_pierces_rx_tgid_match() -> None:
    """Unit data downlink must forward all frames when target RX_TGID equals private dst."""
    from adn_server.domain.hbp_protocol import HBPF_DATA_SYNC, HBPF_SLT_VHEAD

    config = minimal_config(("D-APRS", "SYSTEM"))
    config["_SUB_MAP"] = {bytes_3(HOTSPOT_SUB_ID): ("SYSTEM", 2, 1000.0)}
    scenario = DeterministicScenario(config=config)
    scenario.protocols["SYSTEM"].STATUS[2] = {
        "RX_TYPE": HBPF_SLT_VHEAD,
        "TX_TYPE": HBPF_SLT_VHEAD,
        "RX_TGID": bytes_3(HOTSPOT_SUB_ID),
        "TX_TGID": bytes_3(0),
        "RX_TIME": scenario.clock.time(),
        "TX_TIME": 0.0,
        "RX_STREAM_ID": bytes_3(0),
    }
    stream = 0xAABBCCDD
    for i, dtype in enumerate([3, 6, 8, 8, 8]):
        spec = PacketSpec(
            call_type="unit",
            rf_src=900999,
            dst_id=HOTSPOT_SUB_ID,
            stream_id=stream,
            slot=2,
            dtype_vseq=dtype,
            frame_type=HBPF_DATA_SYNC,
            payload=bytes([dtype]) * 33,
            seq=i,
        )
        scenario.inject_unit("D-APRS", spec)
    assert_forwarded(scenario, "SYSTEM", count=5, call_type="unit", dst_id=HOTSPOT_SUB_ID)


@pytest.mark.behavior
def test_unit_data_pvt_call_does_not_emit_private_voice_monitor_events() -> None:
    """Unit data downlink must not light monitor TS chips (no PRIVATE VOICE START/END)."""
    config = minimal_config(("D-APRS", "SYSTEM"))
    config["_SUB_MAP"] = {bytes_3(HOTSPOT_SUB_ID): ("SYSTEM", 2, 1000.0)}
    scenario = DeterministicScenario(config=config, enable_reporting=True)
    scenario.protocols["SYSTEM"].STATUS[2] = idle_hbp_slot()
    base = PacketSpec(
        call_type="unit",
        rf_src=900999,
        dst_id=HOTSPOT_SUB_ID,
        stream_id=0x11223344,
        slot=2,
    )
    scenario.inject_unit("D-APRS", DeterministicScenario.unit_data_header_spec(base))

    assert_forwarded(scenario, "SYSTEM", count=2, call_type="unit", dst_id=HOTSPOT_SUB_ID)
    assert scenario.report_factory is not None
    private_voice = [e for e in scenario.report_factory.events if e.startswith("PRIVATE VOICE")]
    assert private_voice == []


@pytest.mark.behavior
def test_unit_data_sub_map_with_peer_id_targets_only_that_peer() -> None:
    """Regression: cross-talk -- unit DATA to a known subscriber must reach only the
    one hotspot it was last heard on, not every peer of the destination system
    (send_to_system/send_peers broadcasts to all peers when no peer is targeted)."""
    dst_sub = 712345
    dst_peer = bytes_4(730039101)
    config = minimal_config(("MASTER-A", "MASTER-B"))
    config["_SUB_MAP"] = {bytes_3(dst_sub): ("MASTER-B", 2, 1000.0, dst_peer)}
    scenario = DeterministicScenario(config=config)
    scenario.protocols["MASTER-B"].STATUS[2] = idle_hbp_slot()
    scenario.protocols["MASTER-B"]._peers[dst_peer] = {}
    base = PacketSpec(call_type="unit", dst_id=dst_sub, stream_id=0x52525253, slot=2)

    scenario.inject_unit("MASTER-A", DeterministicScenario.unit_data_header_spec(base))

    assert scenario.protocols["MASTER-B"].sent_to_peer, "must deliver directly to the known peer"
    assert scenario.protocols["MASTER-B"].sent_to_peer[0][0] == dst_peer
    assert_not_forwarded(scenario, "MASTER-B")


@pytest.mark.behavior
def test_unit_data_sub_map_peer_not_connected_falls_back_to_broadcast() -> None:
    """When the known peer isn't (or is no longer) connected, fall back to the old
    broadcast-to-system behavior rather than silently dropping the packet."""
    dst_sub = 712345
    dst_peer = bytes_4(730039101)
    config = minimal_config(("MASTER-A", "MASTER-B"))
    config["_SUB_MAP"] = {bytes_3(dst_sub): ("MASTER-B", 2, 1000.0, dst_peer)}
    scenario = DeterministicScenario(config=config)
    scenario.protocols["MASTER-B"].STATUS[2] = idle_hbp_slot()
    # dst_peer intentionally NOT registered in scenario.protocols["MASTER-B"]._peers.
    base = PacketSpec(call_type="unit", dst_id=dst_sub, stream_id=0x52525254, slot=2)

    scenario.inject_unit("MASTER-A", DeterministicScenario.unit_data_header_spec(base))

    assert not scenario.protocols["MASTER-B"].sent_to_peer
    assert_forwarded(scenario, "MASTER-B", count=1, call_type="unit")


@pytest.mark.behavior
def test_pvt_call_sub_map_with_peer_id_targets_only_that_peer() -> None:
    """Same cross-talk fix for the 7-digit pvt_call_received cross-system path (private
    voice and ARS/LRRP downlink use this, independent of the unit-data SUB_MAP branch)."""
    dst_peer = bytes_4(730039101)
    config = minimal_config(("D-APRS", "SYSTEM"))
    config["_SUB_MAP"] = {bytes_3(HOTSPOT_SUB_ID): ("SYSTEM", 2, 1000.0, dst_peer)}
    scenario = DeterministicScenario(config=config)
    scenario.protocols["SYSTEM"].STATUS[2] = idle_hbp_slot()
    scenario.protocols["SYSTEM"]._peers[dst_peer] = {}
    base = PacketSpec(
        call_type="unit",
        rf_src=DAPRS_GATEWAY_ID,
        dst_id=HOTSPOT_SUB_ID,
        stream_id=0x33416425,
        slot=2,
    )

    scenario.inject_unit("D-APRS", DeterministicScenario.unit_data_header_spec(base))

    assert scenario.protocols["SYSTEM"].sent_to_peer, "must deliver directly to the known peer"
    assert scenario.protocols["SYSTEM"].sent_to_peer[0][0] == dst_peer
    assert_not_forwarded(scenario, "SYSTEM")
