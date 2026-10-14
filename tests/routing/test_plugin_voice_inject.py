# ADN DMR Peer Server - tests plugin group voice through the synthetic ingress
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

"""Plugin group voice (announcements, beacons) through the synthetic ingress on the proxy MASTER."""

from __future__ import annotations

from tests.harness.deterministic import DeterministicScenario, PacketSpec, patch_routing_wall_time
from tests.harness.scenarios import obp_bridge_scenario
from tests.harness.voice_helpers import FakeMasterForVoice

from adn_server.application.plugins.application.ingress import PluginIngress
from adn_server.application.routing.announcement_ptt_inject import (
    announcement_ptt_system,
    inject_plugin_dmrd,
)
from adn_server.application.server_voice import DEFAULT_SERVER_VOICE_ID
from adn_server.domain import bytes_3, bytes_4, int_id


def test_announcement_ptt_system_prefers_proxy_target() -> None:
    config = DeterministicScenario().config
    config["PROXY"] = {"TARGET_SYSTEM": "MASTER-A"}
    assert announcement_ptt_system(config) == "MASTER-A"


def test_inject_forwards_to_obp_and_peer_master() -> None:
    scenario = obp_bridge_scenario("OBP-CL", tg=91)
    scenario.config["PROXY"] = {"TARGET_SYSTEM": "MASTER-A"}
    server_id = scenario.config["GLOBAL"]["SERVER_ID"]
    if isinstance(server_id, bytes):
        peer_id = int.from_bytes(server_id[:4], "big")
    else:
        peer_id = int(server_id)
    base = PacketSpec(
        peer_id=peer_id,
        rf_src=DEFAULT_SERVER_VOICE_ID,
        dst_id=91,
        slot=2,
        stream_id=0xA0A0A0A0,
    )
    sid = server_id if isinstance(server_id, bytes) else bytes_4(int(server_id))
    with patch_routing_wall_time(scenario.clock):
        vhead = DeterministicScenario.voice_head_spec(base)
        inject_plugin_dmrd(
            scenario.routing,
            "MASTER-A",
            vhead.data(),
            pkt_time=scenario.clock.time(),
            server_id=sid,
            plugin="beacon",
        )
        for seq in range(1, 3):
            spec = DeterministicScenario.voice_burst_spec(base, seq=seq, dtype_vseq=min(seq, 4))
            assert inject_plugin_dmrd(
                scenario.routing,
                "MASTER-A",
                spec.data(),
                pkt_time=scenario.clock.time(),
                server_id=sid,
                plugin="beacon",
            ) is True
    assert len(scenario.capture.for_system("OBP-CL")) == 3
    assert len(scenario.capture.for_system("MASTER-A")) == 0


def test_inject_does_not_attribute_call_to_real_peer_matching_rf_src() -> None:
    """A plugin voice stream (announcement, beacon) whose rf_src (e.g. 1000001) matches a real connected peer's

    login id must never be reported as that peer's call: no RX/TX attribution,
    no dynamic TG learned for it. Regression for the misattribution bug where
    ``resolve_voice_peer_id``'s rf_src fallback fired for synthetic frames.
    """
    scenario = obp_bridge_scenario("OBP-CL", tg=91)
    scenario.config["PROXY"] = {"TARGET_SYSTEM": "MASTER-A"}
    real_peer_id = DEFAULT_SERVER_VOICE_ID
    scenario.config["SYSTEMS"]["MASTER-A"]["PEERS"] = {
        bytes_4(real_peer_id): {"CONNECTION": "YES", "OPTIONS": b"TS2=91;"},
    }
    server_id = scenario.config["GLOBAL"]["SERVER_ID"]
    sid = server_id if isinstance(server_id, bytes) else bytes_4(int(server_id))
    events: list[str] = []
    scenario.routing._send_routing_event = events.append  # type: ignore[method-assign]
    base = PacketSpec(
        peer_id=int_id(sid),
        rf_src=real_peer_id,
        dst_id=91,
        slot=2,
        stream_id=0xB0B0B0B0,
    )
    with patch_routing_wall_time(scenario.clock):
        vhead = DeterministicScenario.voice_head_spec(base)
        inject_plugin_dmrd(
            scenario.routing,
            "MASTER-A",
            vhead.data(),
            pkt_time=scenario.clock.time(),
            server_id=sid,
            plugin="beacon",
        )
        for seq in range(1, 3):
            spec = DeterministicScenario.voice_burst_spec(base, seq=seq, dtype_vseq=min(seq, 4))
            inject_plugin_dmrd(
                scenario.routing,
                "MASTER-A",
                spec.data(),
                pkt_time=scenario.clock.time(),
                server_id=sid,
                plugin="beacon",
            )

    assert events, "expected at least one GROUP VOICE report"
    rx_events = [e for e in events if e.startswith("GROUP VOICE,START,RX,") or e.startswith("GROUP VOICE,END,RX,")]
    assert rx_events, "expected an RX report for the MASTER-A inject"
    for event in rx_events:
        fields = event.split(",")
        reported_peer = int(fields[5])
        assert reported_peer != real_peer_id
        assert reported_peer == int_id(sid)
        assert fields[-1] == "1"



def _ingress(scenario, master: FakeMasterForVoice) -> PluginIngress:
    scenario.protocols["MASTER-A"] = master
    return PluginIngress(
        scenario.routing, scenario.config, lambda: scenario.protocols,
        lambda system, pkt: scenario.protocols[system].send_system(pkt), clock=scenario.clock.time,
    )


def test_voice_slot_prefers_the_dynamic_ua_slot() -> None:
    scenario = obp_bridge_scenario("OBP-CL", tg=730600)
    scenario.config["PROXY"] = {"TARGET_SYSTEM": "MASTER-A"}
    peer = bytes_4(730039210)
    sys_cfg = scenario.config["SYSTEMS"]["MASTER-A"]
    sys_cfg["PEERS"] = {peer: {"CONNECTION": "YES", "OPTIONS": b"TS2=730500;"}}
    sys_cfg.setdefault("_PEER_UA_MULTI_TGS", {}).setdefault(peer, {})[2] = {730600}
    master = FakeMasterForVoice("MASTER-A")
    master.STATUS[2] = {"RX_TYPE": 1, "TX_TYPE": 1, "RX_TGID": bytes_3(730600), "TX_TGID": bytes_3(730600),
                        "RX_STREAM_ID": b"\x01" * 4}
    master.STATUS[1] = {"RX_TYPE": 2, "TX_TYPE": 2, "RX_STREAM_ID": b"\x00" * 4}
    assert _ingress(scenario, master).voice_slot_for_tg(730600) == 2


def test_voice_slot_prefers_the_active_bridge_slot_for_a_static_tg() -> None:
    scenario = obp_bridge_scenario("OBP-CL", tg=730500)
    scenario.config["PROXY"] = {"TARGET_SYSTEM": "MASTER-A"}
    scenario.config["SYSTEMS"]["MASTER-A"]["PEERS"] = {
        bytes_4(730039210): {"CONNECTION": "YES", "OPTIONS": b"TS2=730500;"},
    }
    master = FakeMasterForVoice("MASTER-A")
    master.STATUS[2] = {"RX_TYPE": 1, "TX_TYPE": 1, "TX_TGID": bytes_3(730500), "RX_STREAM_ID": b"\x01" * 4}
    master.STATUS[1] = {"RX_TYPE": 2, "TX_TYPE": 2, "RX_STREAM_ID": b"\x00" * 4}
    assert _ingress(scenario, master).voice_slot_for_tg(730500) == 2


def test_plugin_voice_needs_no_pre_armed_bridge_and_reaches_obp() -> None:
    """The synthetic PTT creates the UA relay itself, as announcements always did."""
    scenario = obp_bridge_scenario("OBP-CL", tg=730500)
    scenario.config["PROXY"] = {"TARGET_SYSTEM": "MASTER-A"}
    scenario.seed_routing_table({})
    master = FakeMasterForVoice("MASTER-A")
    master.STATUS[2] = {"RX_TYPE": 2, "TX_TYPE": 2, "RX_STREAM_ID": b"\x00" * 4}
    ingress = _ingress(scenario, master)
    base = PacketSpec(rf_src=DEFAULT_SERVER_VOICE_ID, dst_id=730500, slot=2, stream_id=0xA0A0A0A0)
    frames = [DeterministicScenario.voice_head_spec(base)] + [
        DeterministicScenario.voice_burst_spec(base, seq=n, dtype_vseq=n) for n in (1, 2)
    ]
    with patch_routing_wall_time(scenario.clock):
        assert [ingress.deliver(f.data(), "voice-announcements") for f in frames] == [True, True, True]
    assert len(scenario.capture.for_system("OBP-CL")) == 3
    assert len(master.sent) == 3
