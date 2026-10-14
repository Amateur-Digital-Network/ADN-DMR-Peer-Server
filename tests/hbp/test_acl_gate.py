# ADN DMR Peer Server - tests hbp ACL gate
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

"""One ACL gate, shared by the MASTER and PEER DMRD ingress paths."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

import pytest

from tests.harness.deterministic import PacketSpec
from tests.support.hbp_repeat_stack import RecordingTransport, build_hbp_repeat_stack

from adn_server.domain import bytes_3, bytes_4
from adn_server.infrastructure.acl_router import InMemoryAclRouter
from adn_server.infrastructure.twisted_adapters.udp_hbp import HBPProtocol

_PEER = (1234567).to_bytes(4, "big")
_ADDR = ("10.0.0.9", 54321)
_RF_SRC = 3120001
_DENY_SRC = (False, [(_RF_SRC, _RF_SRC)])
_PERMIT_ALL = (True, [(1, 4294967295)])
_NO_STREAM = b"\x00"  # _make_slot_status seed: no stream seen on this slot yet


def _master_stack(**global_acl: Any):
    stack = build_hbp_repeat_stack()
    # the shared stack ships a permit-everything router; these tests need the real one
    stack.hbp._router = InMemoryAclRouter()
    # the first thing ingress does after the gate: a frame that got through is in SUB_MAP
    stack.config["_SUB_MAP"] = {}
    stack.config["GLOBAL"].update(global_acl)
    stack.register_peer(_PEER, _ADDR)
    return stack


def _voice_head(tg: int, slot: int, stream_id: int, *, call_type: str = "group") -> bytes:
    return replace(
        PacketSpec(
            dst_id=tg, slot=slot, stream_id=stream_id, call_type=call_type,
            peer_id=int.from_bytes(_PEER, "big"), rf_src=_RF_SRC,
        ),
        frame_type=2, dtype_vseq=1,
    ).data()


def test_master_global_subscriber_acl_drops_and_logs_once_per_stream(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stack = _master_stack(USE_ACL=True, SUB_ACL=_DENY_SRC, TG1_ACL=_PERMIT_ALL, TG2_ACL=_PERMIT_ALL)
    with caplog.at_level(logging.INFO):
        for _ in range(3):
            stack.hbp._master_datagram_received(_voice_head(214, 2, 0x44440001), _ADDR)

    assert stack.config["_SUB_MAP"] == {}
    dropped = [r for r in caplog.records if "BY GLOBAL ACL" in r.getMessage()]
    assert len(dropped) == 1
    assert "FROM SUBSCRIBER 3120001" in dropped[0].getMessage()


def test_master_system_tg_acl_applies_to_the_frame_slot_only() -> None:
    stack = _master_stack()
    sys_cfg = stack.config["SYSTEMS"][stack.system_name]
    sys_cfg["USE_ACL"] = True
    sys_cfg["SUB_ACL"] = _PERMIT_ALL
    sys_cfg["TG1_ACL"] = (False, [(214, 214)])
    sys_cfg["TG2_ACL"] = _PERMIT_ALL

    stack.hbp._master_datagram_received(_voice_head(214, 1, 0x44440002), _ADDR)
    assert stack.config["_SUB_MAP"] == {}

    stack.hbp._master_datagram_received(_voice_head(214, 2, 0x44440003), _ADDR)
    assert bytes_3(_RF_SRC) in stack.config["_SUB_MAP"]


def test_on_demand_unit_service_skips_tg_acl_but_not_subscriber_acl() -> None:
    """Legacy exemption: service destinations bypass the TG lists, never SUB_ACL."""
    stack = _master_stack(USE_ACL=True, SUB_ACL=_PERMIT_ALL, TG1_ACL=(False, [(1, 4294967295)]),
                          TG2_ACL=(False, [(1, 4294967295)]))
    assert not stack.hbp._acl_rejects_dmrd(
        bytes_4(_RF_SRC)[1:], bytes_4(9990)[1:], 2, b"\x44\x44\x00\x04", True,
    )
    stack.config["GLOBAL"]["SUB_ACL"] = _DENY_SRC
    assert stack.hbp._acl_rejects_dmrd(
        bytes_4(_RF_SRC)[1:], bytes_4(9990)[1:], 2, b"\x44\x44\x00\x05", True,
    )


def test_peer_mode_ingress_uses_the_same_gate() -> None:
    """PEER mode (client of another master) rejects on the very same rules."""
    master_sockaddr = ("203.0.113.7", 62031)
    config: dict[str, Any] = {
        "GLOBAL": {"USE_ACL": True, "SUB_ACL": _DENY_SRC, "TG1_ACL": _PERMIT_ALL,
                   "TG2_ACL": _PERMIT_ALL},
        "SYSTEMS": {
            "PEER-1": {
                "MODE": "PEER",
                "ENABLED": True,
                "LOOSE": True,
                "RADIO_ID": _PEER,
                "MASTER_SOCKADDR": master_sockaddr,
                "USE_ACL": False,
            },
        },
    }
    hbp = HBPProtocol("PEER-1", config, router=InMemoryAclRouter())
    hbp.transport = RecordingTransport()  # type: ignore[assignment]

    hbp._peer_datagram_received(_voice_head(214, 2, 0x44440006), master_sockaddr)
    assert hbp.STATUS[2].get("RX_STREAM_ID") == _NO_STREAM

    config["GLOBAL"]["SUB_ACL"] = _PERMIT_ALL
    hbp._peer_datagram_received(_voice_head(214, 2, 0x44440007), master_sockaddr)
    assert hbp.STATUS[2].get("RX_STREAM_ID") == (0x44440007).to_bytes(4, "big")
