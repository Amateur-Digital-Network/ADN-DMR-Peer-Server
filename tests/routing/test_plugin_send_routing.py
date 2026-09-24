# ADN DMR Peer Server - tests plugin send routing
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

"""Unit data a plugin sends (ServerContext.send_dmrd) is routed locally, and only locally."""

from __future__ import annotations

from tests.harness.deterministic import (
    DeterministicScenario,
    PacketSpec,
    add_openbridge_system,
    minimal_config,
    patch_routing_wall_time,
)
from tests.routing.unit_data_helpers import idle_hbp_slot

from adn_server.application.plugins.application.bus import PluginBus
from adn_server.application.plugins.application.data_bridge import DataPluginBridge
from adn_server.application.plugins.domain.events import UnitDataFrame
from adn_server.application.routing.announcement_ptt_inject import inject_plugin_dmrd
from adn_server.domain import HBPF_DATA_SYNC, bytes_3, bytes_4

GATEWAY_ID = 900999
RADIO_ID = 7140023
RADIO_HOTSPOT = bytes_4(714002301)
SERVER_ID = bytes_4(2131)


def _scenario(radio_on: str = "SYSTEM-B") -> DeterministicScenario:
    config = minimal_config(("SYSTEM", "SYSTEM-B"))
    add_openbridge_system(config, "OBP-1")
    config["SYSTEMS"]["OBP-1"]["VER"] = 5
    config["GLOBAL"]["DATA_GATEWAY"] = True
    add_openbridge_system(config, "DATA-GATEWAY")
    config["SYSTEMS"]["DATA-GATEWAY"]["ENABLED"] = True
    sc = DeterministicScenario(config=config)
    for name in ("SYSTEM", "SYSTEM-B"):
        sc.protocols[name].STATUS[2] = idle_hbp_slot()
        sc.config["SYSTEMS"][name]["GROUP_HANGTIME"] = 0
    sc.protocols[radio_on]._peers[RADIO_HOTSPOT] = {}
    sc.config["_SUB_MAP"] = {bytes_3(RADIO_ID): (radio_on, 2, sc.clock.time(), RADIO_HOTSPOT)}
    return sc


def _ars_ack(dst: int = RADIO_ID, *, call_type: str = "unit", frame_type: int = HBPF_DATA_SYNC) -> bytes:
    return PacketSpec(
        rf_src=GATEWAY_ID, dst_id=dst, peer_id=1, slot=2, call_type=call_type,
        frame_type=frame_type, dtype_vseq=6, stream_id=0x0A0B0C0D, payload=bytes(range(33)),
    ).data()


def _send(sc: DeterministicScenario, pkt: bytes):
    with patch_routing_wall_time(sc.clock):
        return inject_plugin_dmrd(sc.routing, "SYSTEM", pkt, pkt_time=sc.clock.time(), server_id=SERVER_ID, plugin="d-aprs")


def test_reaches_the_radio_on_another_system_and_nothing_else() -> None:
    sc = _scenario("SYSTEM-B")
    _send(sc, _ars_ack())
    assert [peer for peer, _ in sc.protocols["SYSTEM-B"].sent_to_peer] == [RADIO_HOTSPOT]
    assert sc.capture.packets == []  # no OpenBridge, no DATA-GATEWAY


def test_reaches_a_radio_behind_the_ingress_master_itself() -> None:
    # The common case: every hotspot sits on the proxy MASTER the plugin injects on.
    sc = _scenario("SYSTEM")
    _send(sc, _ars_ack())
    assert [peer for peer, _ in sc.protocols["SYSTEM"].sent_to_peer] == [RADIO_HOTSPOT]


def test_unknown_destination_is_not_flooded_to_openbridge() -> None:
    sc = _scenario()
    _send(sc, _ars_ack(dst=3341234))
    assert sc.capture.for_system("OBP-1") == []
    assert sc.capture.for_system("DATA-GATEWAY") == []


def test_the_plugin_source_is_never_learned_in_sub_map() -> None:
    sc = _scenario()
    _send(sc, _ars_ack())
    assert bytes_3(GATEWAY_ID) not in sc.config["_SUB_MAP"]


def test_group_traffic_from_a_plugin_is_refused() -> None:
    sc = _scenario()
    assert _send(sc, _ars_ack(dst=213, call_type="group", frame_type=0)) is False
    assert sc.capture.packets == []


def test_plugins_see_their_own_frames_as_synthetic() -> None:
    sc = _scenario()
    events: list[object] = []
    bus = PluginBus()
    bus.subscribe(events.append)
    sc.routing._data_plugin_bridge = DataPluginBridge(bus, sc.config)
    _send(sc, _ars_ack())
    frames = [e for e in events if isinstance(e, UnitDataFrame)]
    assert frames and all(f.context.is_synthetic for f in frames)
