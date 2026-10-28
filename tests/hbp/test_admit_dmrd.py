# ADN DMR Peer Server - tests hbp admit dmrd
#
# Copyright (C) 2026  Rodrigo Pérez, CE5RPY <ce5rpy@qmd.cl>
# Copyright (C) 2026  Joaquin Madrid Belando, EA5GVK <ea5gvk@gmail.com>
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

"""The DMRD admission step the MASTER and PEER ingress paths share: decode, null stream, ACL, SUB_MAP."""

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
_MASTER_ADDR = ("203.0.113.7", 62031)
_RF_SRC = 3120001
_DENY_ALL = (False, [(1, 4294967295)])
_PERMIT_ALL = (True, [(1, 4294967295)])


def _master():
    stack = build_hbp_repeat_stack()
    stack.hbp._router = InMemoryAclRouter()
    stack.config["_SUB_MAP"] = {}
    stack.register_peer(_PEER, _ADDR)
    return stack


def _peer_mode() -> tuple[HBPProtocol, dict[str, Any]]:
    config: dict[str, Any] = {
        "GLOBAL": {},
        "_SUB_MAP": {},
        "SYSTEMS": {
            "PEER-1": {
                "MODE": "PEER", "ENABLED": True, "LOOSE": True, "RADIO_ID": _PEER,
                "MASTER_SOCKADDR": _MASTER_ADDR, "USE_ACL": False,
            },
        },
    }
    hbp = HBPProtocol("PEER-1", config, router=InMemoryAclRouter())
    hbp.transport = RecordingTransport()  # type: ignore[assignment]
    return hbp, config


def _frame(**fields: Any) -> bytes:
    return replace(PacketSpec(peer_id=1234567, rf_src=_RF_SRC, dst_id=214, frame_type=2, dtype_vseq=1), **fields).data()


def _admit(stack, frame: bytes):
    return stack.hbp.admit_dmrd(frame, _PEER, frame[5:8], frame[8:11], int.from_bytes(frame[8:11], "big"), 1000.0)


@pytest.mark.parametrize(
    ("bits", "decoded"),
    [
        (0x00, (1, "group", 0, 0)),
        (0xA1, (2, "group", 2, 1)),
        (0x23, (1, "vcsbk", 2, 3)),
        (0x63, (1, "unit", 2, 3)),
        (0xD5, (2, "unit", 1, 5)),
    ],
    ids=["ts1-group-voice", "ts2-group-vhead", "ts1-vcsbk", "unit-before-vcsbk", "ts2-unit-sync"],
)
def test_the_flags_byte_gives_slot_call_type_frame_type_and_dtype(bits: int, decoded: tuple) -> None:
    stack = _master()
    frame = bytearray(_frame(stream_id=0x44440001))
    frame[15] = bits

    assert _admit(stack, bytes(frame)) == (*decoded, bytes_4(0x44440001))


def test_an_admitted_frame_learns_where_its_source_is() -> None:
    stack = _master()

    _admit(stack, _frame(stream_id=0x44440002))

    assert stack.config["_SUB_MAP"] == {bytes_3(_RF_SRC): (stack.system_name, 2, 1000.0, _PEER)}


@pytest.mark.parametrize("mode", ["MASTER", "PEER"])
def test_a_null_stream_is_dropped_and_logged(mode: str, caplog: pytest.LogCaptureFixture) -> None:
    if mode == "MASTER":
        stack = _master()
        hbp, config, addr = stack.hbp, stack.config, _ADDR
    else:
        hbp, config = _peer_mode()
        addr = _MASTER_ADDR
    before = dict(hbp.STATUS[2])

    with caplog.at_level(logging.WARNING):
        hbp.datagramReceived(_frame(stream_id=0), addr)

    assert "CALL DROPPED AS STREAM ID IS NULL FROM SUBSCRIBER 3120001" in caplog.text
    assert config["_SUB_MAP"] == {} and hbp.STATUS[2] == before


@pytest.mark.parametrize(("dst", "admitted"), [(9991, True), (9990, False)], ids=["on-demand-9991", "9990"])
def test_only_an_on_demand_unit_call_skips_the_tg_acl(dst: int, admitted: bool) -> None:
    stack = _master()
    stack.config["GLOBAL"].update(USE_ACL=True, SUB_ACL=_PERMIT_ALL, TG1_ACL=_DENY_ALL, TG2_ACL=_DENY_ALL)

    stack.inject(_frame(dst_id=dst, call_type="unit", stream_id=0x44440003), _ADDR)

    assert (bytes_3(_RF_SRC) in stack.config["_SUB_MAP"]) is admitted
