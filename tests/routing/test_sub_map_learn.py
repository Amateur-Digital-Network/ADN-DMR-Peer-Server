# ADN DMR Peer Server - tests routing SUB_MAP learning
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

"""SUB_MAP_LEARN: false keeps a service system from becoming where a subscriber is."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from tests.harness.deterministic import DeterministicScenario, PacketSpec, minimal_config
from tests.support.hbp_repeat_stack import RecordingTransport, build_hbp_repeat_stack

from adn_server.application.routing.sub_map_learning import (
    learn_sub_map,
    purge_non_learning_systems,
    system_learns_sub_map,
)
from adn_server.domain import bytes_3, bytes_4
from adn_server.infrastructure.acl_router import InMemoryAclRouter
from adn_server.infrastructure.twisted_adapters.udp_hbp import HBPProtocol

_PEER = bytes_4(2130035)
_ADDR = ("10.0.0.9", 54321)
_RF_SRC = 2130035
_PERMIT_ALL = (True, [(1, 4294967295)])


def _voice_head(tg: int, stream_id: int, *, call_type: str = "group") -> bytes:
    return replace(
        PacketSpec(
            dst_id=tg, slot=2, stream_id=stream_id, call_type=call_type,
            peer_id=int.from_bytes(_PEER, "big"), rf_src=_RF_SRC,
        ),
        frame_type=2, dtype_vseq=1,
    ).data()


def test_default_and_explicit_true_learn() -> None:
    assert system_learns_sub_map({"MODE": "MASTER"})
    assert system_learns_sub_map({"MODE": "MASTER", "SUB_MAP_LEARN": True})
    assert system_learns_sub_map(None)
    assert not system_learns_sub_map({"MODE": "MASTER", "SUB_MAP_LEARN": False})
    assert not system_learns_sub_map({"MODE": "PEER", "SUB_MAP_LEARN": False})


def test_openbridge_always_learns() -> None:
    """The validator rejects the key on OBP; if it slips through, OBP still learns."""
    config: dict[str, Any] = {
        "_SUB_MAP": {},
        "SYSTEMS": {"OBP-1": {"MODE": "OPENBRIDGE", "SUB_MAP_LEARN": False}},
    }
    assert learn_sub_map(config, "OBP-1", bytes_3(_RF_SRC), 1, 1000.0, None)
    assert config["_SUB_MAP"][bytes_3(_RF_SRC)][0] == "OBP-1"


def test_master_ingress_learns_by_default() -> None:
    stack = build_hbp_repeat_stack()
    stack.config["_SUB_MAP"] = {}
    stack.register_peer(_PEER, _ADDR)
    stack.hbp.datagramReceived(_voice_head(213, 0x51000001), _ADDR)
    assert stack.config["_SUB_MAP"][bytes_3(_RF_SRC)][0] == stack.system_name


def test_master_ingress_skips_learning_when_disabled() -> None:
    stack = build_hbp_repeat_stack()
    stack.config["SYSTEMS"][stack.system_name]["SUB_MAP_LEARN"] = False
    stack.hbp.apply_system_config(stack.config)  # as a SIGHUP reload does
    elsewhere = ("MASTER-HOTSPOTS", 2, 1000.0, bytes_4(2130099))
    stack.config["_SUB_MAP"] = {bytes_3(_RF_SRC): elsewhere}
    stack.register_peer(_PEER, _ADDR)
    stack.hbp.datagramReceived(_voice_head(213, 0x51000002), _ADDR)
    # the subscriber stays where they were really last heard
    assert stack.config["_SUB_MAP"][bytes_3(_RF_SRC)] == elsewhere

    # same frame path, flag back on by reload: now it is learned (the frame did get that far)
    stack.config["SYSTEMS"][stack.system_name]["SUB_MAP_LEARN"] = True
    stack.hbp.apply_system_config(stack.config)
    stack.hbp.datagramReceived(_voice_head(213, 0x51000003), _ADDR)
    assert stack.config["_SUB_MAP"][bytes_3(_RF_SRC)][0] == stack.system_name


def test_peer_ingress_skips_learning_when_disabled() -> None:
    master_sockaddr = ("203.0.113.7", 62031)
    config: dict[str, Any] = {
        "GLOBAL": {"USE_ACL": False, "SUB_ACL": _PERMIT_ALL, "TG1_ACL": _PERMIT_ALL, "TG2_ACL": _PERMIT_ALL},
        "SYSTEMS": {
            "BRIDGE-PEER": {
                "MODE": "PEER",
                "ENABLED": True,
                "LOOSE": True,
                "RADIO_ID": _PEER,
                "MASTER_SOCKADDR": master_sockaddr,
                "USE_ACL": False,
                "SUB_MAP_LEARN": False,
            },
        },
        "_SUB_MAP": {},
    }
    hbp = HBPProtocol("BRIDGE-PEER", config, router=InMemoryAclRouter())
    hbp.transport = RecordingTransport()  # type: ignore[assignment]

    hbp._peer_datagram_received(_voice_head(214, 0x51000013), master_sockaddr)
    assert hbp.STATUS[2].get("RX_STREAM_ID") == (0x51000013).to_bytes(4, "big")
    assert config["_SUB_MAP"] == {}

    config["SYSTEMS"]["BRIDGE-PEER"]["SUB_MAP_LEARN"] = True
    hbp.apply_system_config(config)
    hbp._peer_datagram_received(_voice_head(214, 0x51000004), master_sockaddr)
    assert config["_SUB_MAP"][bytes_3(_RF_SRC)][0] == "BRIDGE-PEER"


def test_private_call_ingress_skips_learning_but_still_routes() -> None:
    config = minimal_config(("ECHO", "MASTER-B"))
    config["SYSTEMS"]["ECHO"]["SUB_MAP_LEARN"] = False
    dst_sub = 7123456
    config["_SUB_MAP"] = {bytes_3(dst_sub): ("MASTER-B", 2, 1000.0)}
    scenario = DeterministicScenario(config=config)
    base = PacketSpec(call_type="unit", dst_id=dst_sub, rf_src=3120001, stream_id=0xAABBCC01, slot=2)

    scenario.inject_unit("ECHO", DeterministicScenario.unit_voice_head_spec(base))
    scenario.inject_unit("ECHO", DeterministicScenario.unit_voice_burst_spec(base, seq=1))

    assert scenario.capture.for_system("MASTER-B"), "the private call is still delivered"
    assert bytes_3(3120001) not in config["_SUB_MAP"]


def test_purge_drops_only_entries_of_non_learning_systems() -> None:
    config: dict[str, Any] = {
        "SYSTEMS": {
            "BALIZA-MASTER": {"MODE": "MASTER", "SUB_MAP_LEARN": False},
            "MASTER-A": {"MODE": "MASTER"},
            "OBP-1": {"MODE": "OPENBRIDGE", "SUB_MAP_LEARN": False},
        },
        "_SUB_MAP": {
            bytes_3(1): ("BALIZA-MASTER", 2, 1000.0, bytes_4(1)),
            bytes_3(2): ("MASTER-A", 2, 1000.0, bytes_4(2)),
            bytes_3(3): ("OBP-1", 1, 1000.0),
            bytes_3(4): ("GONE-SYSTEM", 1, 1000.0),
        },
    }
    assert purge_non_learning_systems(config) == 1
    assert set(config["_SUB_MAP"]) == {bytes_3(2), bytes_3(3), bytes_3(4)}
    assert purge_non_learning_systems(config) == 0
