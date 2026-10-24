# ADN DMR Peer Server - tests routing voice burst C is not a terminator
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

"""A call whose last voice burst before the terminator is C (vseq 2, the terminator's
number) still ends: RX_TYPE/TX_TYPE never read a voice burst as HBPF_SLT_VTERM."""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock

import pytest
from tests.harness.deterministic import (
    DeterministicScenario,
    PacketSpec,
    active_routing_table,
    add_openbridge_system,
    minimal_config,
    patch_routing_wall_time,
)
from tests.routing.unit_data_helpers import idle_hbp_slot
from tests.support.hbp_repeat_stack import RecordingTransport, build_hbp_repeat_stack

import adn_server.application.plugins.application.ingress as plugin_ingress
import adn_server.application.routing.timers as timers_mod
import adn_server.infrastructure.twisted_adapters.udp_hbp as udp_hbp_mod
from adn_server.application.plugins.domain.send import parse_dmrd_header
from adn_server.domain import (
    HBPF_DATA_SYNC,
    HBPF_SLT_VHEAD,
    HBPF_SLT_VTERM,
    HBPF_VOICE,
    HBPF_VOICE_SYNC,
    bytes_3,
    bytes_4,
)
from adn_server.infrastructure.acl_router import InMemoryAclRouter
from adn_server.infrastructure.twisted_adapters.udp_hbp import HBPProtocol

_PEER = (3120001).to_bytes(4, "big")
_ADDR = ("10.0.0.9", 54321)
# Head, bursts A, B and C, terminator.
_FRAMES = (
    (0, HBPF_DATA_SYNC, HBPF_SLT_VHEAD),
    (1, HBPF_VOICE_SYNC, 0),
    (2, HBPF_VOICE, 1),
    (3, HBPF_VOICE, 2),
    (4, HBPF_DATA_SYNC, HBPF_SLT_VTERM),
)


def _specs(
    stream_id: int, *, upto_burst_c: bool = False, dst_id: int = 91, call_type: str = "group"
) -> list[PacketSpec]:
    base = PacketSpec(
        dst_id=dst_id,
        slot=2,
        stream_id=stream_id,
        call_type=call_type,
        peer_id=int.from_bytes(_PEER, "big"),
        rf_src=3120001,
    )
    frames = _FRAMES[:-1] if upto_burst_c else _FRAMES
    return [replace(base, seq=seq, frame_type=ft, dtype_vseq=dv) for seq, ft, dv in frames]


def test_master_answers_an_on_demand_request_whose_last_burst_was_c(monkeypatch: pytest.MonkeyPatch) -> None:
    """A private call to 9991-9999 asks for a recording on its terminator, read before that frame updates the slot."""
    stack = build_hbp_repeat_stack()
    stack.hbp._dmrd_received = MagicMock(return_value=True)  # routing accepts; only the slot bookkeeping is under test
    stack.hbp._on_play_file_request = MagicMock()
    in_thread = MagicMock()
    monkeypatch.setattr(udp_hbp_mod.reactor, "callInThread", in_thread)
    stack.register_peer(_PEER, _ADDR)

    for spec in _specs(0x0C0C0001, dst_id=9991, call_type="unit"):
        stack.hbp._master_datagram_received(spec.data(), _ADDR)

    in_thread.assert_called_once_with(stack.hbp._on_play_file_request, "9991", stack.system_name)


def test_peer_runs_in_band_signalling_on_the_terminator_after_burst_c() -> None:
    master = ("203.0.113.7", 62031)
    config: dict[str, Any] = {
        "GLOBAL": {"USE_ACL": False},
        "SYSTEMS": {
            "PEER-1": {
                "MODE": "PEER",
                "ENABLED": True,
                "LOOSE": True,
                "RADIO_ID": _PEER,
                "MASTER_SOCKADDR": master,
                "USE_ACL": False,
            },
        },
    }
    hbp = HBPProtocol("PEER-1", config, router=InMemoryAclRouter())
    hbp.transport = RecordingTransport()  # type: ignore[assignment]
    signalling = MagicMock()
    hbp._on_in_band_signalling = signalling

    for spec in _specs(0x0C0C0002):
        hbp._peer_datagram_received(spec.data(), master)

    signalling.assert_called_once()


def test_a_private_call_ending_after_burst_c_reports_its_end() -> None:
    unit = 7123456
    config = minimal_config(("MASTER-B",))
    add_openbridge_system(config, "OBP-1")
    config["_SUB_MAP"] = {bytes_3(unit): ("MASTER-B", 2, 1000.0)}
    sc = DeterministicScenario(config=config, enable_reporting=True)
    base = PacketSpec(call_type="unit", peer_id=9990, rf_src=3120002, dst_id=unit, slot=1, stream_id=0x0C0C0003)

    for seq, ft, dv in _FRAMES:
        sc.inject_obp("OBP-1", replace(base, seq=seq, frame_type=ft, dtype_vseq=dv))

    assert [e for e in sc.report_factory.events if e.startswith("PRIVATE VOICE,END,RX,OBP-1,")]


def test_a_bridge_leg_whose_last_frame_was_burst_c_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    sc = DeterministicScenario(
        routing_table=active_routing_table(91, (("MASTER-A", 2), ("MASTER-B", 2))),
        enable_reporting=True,
    )
    with patch_routing_wall_time(sc.clock):
        for spec in _specs(0x0C0C0004, upto_burst_c=True):
            sc.clock.advance(0.06)
            sc.inject_hbp("MASTER-A", spec)
    sc.clock.advance(6)
    monkeypatch.setattr(timers_mod.time, "time", sc.clock.time)
    sc.routing.stream_trimmer_loop()

    assert [e for e in sc.report_factory.events if e.startswith("GROUP VOICE,END,TX,MASTER-B,")]


def test_plugin_voice_after_burst_c_still_holds_the_slot() -> None:
    pkt = _specs(0x0C0C0005, upto_burst_c=True)[-1].data()
    slot: dict[str, Any] = {}
    plugin_ingress._record_rx(slot, parse_dmrd_header(pkt), pkt, bytes_4(9990), 1000.0)

    assert plugin_ingress._rx_taken(slot, bytes_4(0x0C0C0006), 1000.1)


def test_a_private_call_at_burst_c_keeps_its_target_slot_from_plugin_voice() -> None:
    unit = 7123456
    config = minimal_config(("MASTER-A", "MASTER-B"))
    config["_SUB_MAP"] = {bytes_3(unit): ("MASTER-B", 2, 1000.0)}
    sc = DeterministicScenario(config=config)
    sc.protocols["MASTER-B"].STATUS[2] = idle_hbp_slot()
    call = PacketSpec(call_type="unit", peer_id=730039101, rf_src=3120003, dst_id=unit, slot=2, stream_id=0x0C0C0007)
    for seq, ft, dv in _FRAMES[:-1]:
        sc.inject_unit("MASTER-A", replace(call, seq=seq, frame_type=ft, dtype_vseq=dv))

    target = sc.protocols["MASTER-B"].STATUS[2]
    assert target.get("TX_STREAM_ID") == bytes_4(0x0C0C0007)
    assert plugin_ingress.PluginIngress._slot_busy(target, False, sc.clock.time())


def test_an_obp_leg_at_burst_c_keeps_the_slot_from_a_second_stream() -> None:
    config = minimal_config(("MASTER-A",))
    add_openbridge_system(config, "OBP-1")
    sc = DeterministicScenario(config=config)
    table = active_routing_table(3340, (("MASTER-A", 2), ("OBP-1", 1)))
    table.update(active_routing_table(3341, (("MASTER-A", 2), ("OBP-1", 1))))
    sc.seed_routing_table(table)
    first = PacketSpec(dst_id=3340, stream_id=0x0C0C0009, rf_src=3340062, slot=1)
    second = PacketSpec(dst_id=3341, stream_id=0x0C0C000A, rf_src=3340063, slot=1)
    with patch_routing_wall_time(sc.clock):
        for seq, ft, dv in _FRAMES[:-1]:
            sc.clock.advance(0.06)
            sc.inject_obp("OBP-1", replace(first, seq=seq, frame_type=ft, dtype_vseq=dv))
        sc.clock.advance(0.02)
        sc.inject_obp("OBP-1", DeterministicScenario.voice_head_spec(second))

    assert sc.protocols["MASTER-A"].STATUS[2].get("TX_STREAM_ID") == bytes_4(0x0C0C0009)
