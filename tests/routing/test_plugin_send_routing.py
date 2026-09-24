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
    active_routing_table,
    add_openbridge_system,
    minimal_config,
    patch_routing_wall_time,
)
from tests.routing.unit_data_helpers import idle_hbp_slot

from adn_server.application.plugins.application.bus import PluginBus
from adn_server.application.plugins.application.data_bridge import DataPluginBridge
from adn_server.application.plugins.application.ingress import PluginIngress
from adn_server.application.plugins.domain.events import UnitDataFrame
from adn_server.application.routing.announcement_ptt_inject import inject_plugin_dmrd
from adn_server.domain import HBPF_DATA_SYNC, HBPF_SLT_VHEAD, HBPF_SLT_VTERM, HBPF_VOICE, bytes_3, bytes_4

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


def test_private_voice_from_a_plugin_is_refused() -> None:
    sc = _scenario()
    assert _send(sc, _ars_ack(frame_type=HBPF_VOICE)) is False
    assert sc.capture.packets == [] and sc.protocols["SYSTEM-B"].sent_to_peer == []


def test_plugins_see_their_own_frames_as_synthetic() -> None:
    sc = _scenario()
    events: list[object] = []
    bus = PluginBus()
    bus.subscribe(events.append)
    sc.routing._data_plugin_bridge = DataPluginBridge(bus, sc.config)
    _send(sc, _ars_ack())
    frames = [e for e in events if isinstance(e, UnitDataFrame)]
    assert frames and all(f.context.is_synthetic for f in frames)


# --- group voice (voice beacons and announcements as plugins) ---

TG = 213
BEACON_ID = 2130035


def _voice_scenario():
    config = minimal_config(("SYSTEM", "SYSTEM-B"))
    config["SYSTEMS"]["SYSTEM"]["PEERS"] = {b"\x00\x00\x03\xe9": {"CALLSIGN": "HOTSPOT"}}
    table = active_routing_table(TG, (("SYSTEM", 2), ("SYSTEM-B", 2)), timeout_minutes=10**6)
    sc = DeterministicScenario(config=config, routing_table=table)
    sc.routing.apply_startup_subscriptions()
    for name in ("SYSTEM", "SYSTEM-B"):
        sc.protocols[name].STATUS[2] = idle_hbp_slot()
    local: list[bytes] = []
    ingress = PluginIngress(sc.routing, sc.config, lambda: sc.protocols, lambda system, pkt: local.append(pkt), clock=sc.clock.time)
    return sc, ingress, local


def _beacon(stream: int = 0x0B0B0B0B) -> list[bytes]:
    base = PacketSpec(rf_src=BEACON_ID, dst_id=TG, peer_id=7, slot=2, stream_id=stream)
    frames = [DeterministicScenario.voice_head_spec(base)]
    frames += [DeterministicScenario.voice_burst_spec(base, seq=n + 1, dtype_vseq=n % 6) for n in range(6)]
    frames.append(DeterministicScenario.voice_term_spec(base, seq=7))
    return [f.data() for f in frames]


def _play(sc, ingress, frames) -> list[bool]:
    out = []
    for pkt in frames:
        sc.clock.advance(0.06)
        with patch_routing_wall_time(sc.clock):
            out.append(ingress.deliver(pkt, "beacon"))
    return out


def test_a_voice_beacon_is_routed_like_an_announcement_and_heard_locally() -> None:
    sc, ingress, local = _voice_scenario()
    frames = _beacon()
    assert _play(sc, ingress, frames) == [True] * len(frames)
    to_b = sc.capture.for_system("SYSTEM-B")
    assert len(to_b) == len(frames)
    assert all(p.packet[5:8] == bytes_3(BEACON_ID) for p in to_b)
    assert len(local) == len(frames)  # hotspots of the MASTER it enters on
    assert {p[11:15] for p in local} == {ingress._server_id()}  # never the peer the plugin wrote
    assert ingress._server_id() != bytes_4(7)


def test_the_master_slot_is_held_while_it_plays_and_freed_by_the_terminator() -> None:
    sc, ingress, _ = _voice_scenario()
    frames = _beacon()
    _play(sc, ingress, frames[:3])
    slot = sc.protocols["SYSTEM"].STATUS[2]
    assert slot["TX_TYPE"] == HBPF_SLT_VHEAD and slot["TX_RFS"] == bytes_3(BEACON_ID)
    _play(sc, ingress, frames[3:])
    assert slot["TX_TYPE"] == HBPF_SLT_VTERM


def test_a_radio_talking_on_the_slot_stops_the_beacon() -> None:
    sc, ingress, local = _voice_scenario()
    slot = sc.protocols["SYSTEM"].STATUS[2]
    slot.update(RX_TYPE=HBPF_SLT_VHEAD, RX_TIME=sc.clock.time() + 0.06, RX_STREAM_ID=b"\x01\x01\x01\x01")
    assert _play(sc, ingress, _beacon()[:1]) == [False]
    assert sc.capture.for_system("SYSTEM-B") == [] and local == []


def test_a_second_plugin_stream_cannot_talk_over_the_first() -> None:
    sc, ingress, _ = _voice_scenario()
    _play(sc, ingress, _beacon(stream=1)[:2])
    assert _play(sc, ingress, _beacon(stream=2)[:1]) == [False]


def test_voice_slot_prefers_the_slot_where_the_tg_is_bridged_on_the_master() -> None:
    config = minimal_config(("SYSTEM", "SYSTEM-B"))
    config["SYSTEMS"]["SYSTEM"]["PEERS"] = {b"\x00\x00\x03\xe9": {"CALLSIGN": "HOTSPOT"}}
    table = active_routing_table(TG, (("SYSTEM", 1), ("SYSTEM-B", 2)), timeout_minutes=10**6)
    sc = DeterministicScenario(config=config, routing_table=table)
    sc.routing.apply_startup_subscriptions()
    for ts in (1, 2):
        sc.protocols["SYSTEM"].STATUS[ts] = idle_hbp_slot() | {"RX_TYPE": HBPF_SLT_VTERM}
    ingress = PluginIngress(sc.routing, sc.config, lambda: sc.protocols, lambda *a: None, clock=sc.clock.time)
    assert ingress.voice_slot_for_tg(TG) == 1  # TG 213 lives on TS1 of this MASTER
    assert ingress.voice_slot_for_tg(9999) == 2  # nowhere yet: TS2 first


def test_voice_slot_is_none_while_every_slot_is_busy() -> None:
    sc, ingress, _ = _voice_scenario()
    for ts in (1, 2):
        sc.protocols["SYSTEM"].STATUS[ts] = {
            "RX_TYPE": HBPF_SLT_VHEAD, "RX_TIME": sc.clock.time(), "TX_TYPE": HBPF_SLT_VTERM,
            "RX_STREAM_ID": b"\x05\x05\x05\x05",
        }
    assert ingress.voice_slot_for_tg(TG) is None


def test_the_monitor_sees_the_beacon_on_the_master_it_plays_on() -> None:
    sc, _, _ = _voice_scenario()
    events: list[str] = []
    ingress = PluginIngress(sc.routing, sc.config, lambda: sc.protocols, lambda *a: None,
                            clock=sc.clock.time, send_routing_event=events.append)
    _play(sc, ingress, _beacon(stream=0x0C0C0C0C))
    starts = [e for e in events if e.startswith("GROUP VOICE,START,TX,SYSTEM,")]
    ends = [e for e in events if e.startswith("GROUP VOICE,END,TX,SYSTEM,")]
    assert len(starts) == 1 and len(ends) == 1
    assert starts[0].split(",")[4:9] == [str(0x0C0C0C0C), str(BEACON_ID), str(BEACON_ID), "2", str(TG)]


def test_a_beacon_cut_by_a_radio_still_ends_on_the_monitor() -> None:
    sc, _, _ = _voice_scenario()
    events: list[str] = []
    ingress = PluginIngress(sc.routing, sc.config, lambda: sc.protocols, lambda *a: None,
                            clock=sc.clock.time, send_routing_event=events.append)
    frames = _beacon()
    _play(sc, ingress, frames[:3])
    sc.protocols["SYSTEM"].STATUS[2].update(RX_TYPE=HBPF_SLT_VHEAD, RX_TIME=sc.clock.time() + 0.06, RX_STREAM_ID=b"\x09" * 4)
    assert _play(sc, ingress, frames[3:4]) == [False]
    assert sum(e.startswith("GROUP VOICE,END,TX,SYSTEM,") for e in events) == 1
    assert sc.protocols["SYSTEM"].STATUS[2]["TX_TYPE"] == HBPF_SLT_VTERM
