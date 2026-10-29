# ADN DMR Peer Server - tests hbp slot rx record
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


"""What the MASTER and PEER ingress note on the slot row for a frame: the call's LC and the last accepted frame."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from bitarray import bitarray

from tests.harness.deterministic import DeterministicScenario, PacketSpec
from tests.support.hbp_repeat_stack import RecordingTransport, build_hbp_repeat_stack

from adn_server.domain import HBPF_SLT_VHEAD, bytes_3, bytes_4
from adn_server.domain.dmr.bptc import encode_header_lc
from adn_server.domain.dmr.const import LC_OPT
from adn_server.domain.hbp_protocol import HBPF_SLT_VTERM
from adn_server.infrastructure.acl_router import InMemoryAclRouter
from adn_server.infrastructure.twisted_adapters.udp_hbp import HBPProtocol

_PEER = (1234567).to_bytes(4, "big")
_ADDR = ("10.0.0.9", 54321)
_MASTER_ADDR = ("203.0.113.7", 62031)
_RF, _TG, _STREAM = 3120001, 214, 0x44440010
# Service options set: only the header carries them, so a built LC would lose them.
_LC = b"\x00\x10\x40" + bytes_3(_TG) + bytes_3(_RF)
_SPEC = PacketSpec(peer_id=1234567, rf_src=_RF, dst_id=_TG, slot=2, stream_id=_STREAM)


def _head() -> bytes:
    head = DeterministicScenario.voice_head_spec(_SPEC)
    bits = bitarray(endian="big")
    bits.frombytes(head.payload)
    h_lc = encode_header_lc(_LC)
    return replace(head, payload=(h_lc[0:98] + bits[98:166] + h_lc[98:196]).tobytes()).data()


def _burst(seq: int) -> bytes:
    return DeterministicScenario.voice_burst_spec(_SPEC, seq=seq, dtype_vseq=seq - 1).data()


def _master():
    stack = build_hbp_repeat_stack()
    stack.hbp._dmrd_received = stack.bridge.dmrd_received
    stack.register_peer(_PEER, _ADDR)
    return stack


def _peer_mode() -> HBPProtocol:
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
    return hbp


def test_a_master_notes_the_header_lc_and_the_accepted_frame_on_the_slot_row() -> None:
    stack = _master()

    stack.inject(_head(), _ADDR)

    st = stack.hbp.STATUS[2]
    assert st["RX_LC"] == _LC
    assert (st["RX_PEER"], st["RX_SEQ"], st["RX_RFS"], st["RX_TGID"]) == (_PEER, 0, bytes_3(_RF), bytes_3(_TG))
    assert st["RX_TYPE"] == HBPF_SLT_VHEAD and st["RX_STREAM_ID"] == bytes_4(_STREAM)
    assert st["RX_STREAMS"][bytes_4(_STREAM)][:3] == [_PEER, bytes_3(_RF), bytes_3(_TG)]


def test_a_call_heard_from_a_burst_on_gets_an_lc_built_from_the_frame() -> None:
    stack = _master()

    stack.inject(_burst(1), _ADDR)

    assert stack.hbp.STATUS[2]["RX_LC"] == LC_OPT + bytes_3(_TG) + bytes_3(_RF)


def test_the_terminator_ends_the_call_on_the_slot_row() -> None:
    stack = _master()
    stack.inject(_head(), _ADDR)

    stack.inject(DeterministicScenario.voice_term_spec(_SPEC, seq=2).data(), _ADDR)

    st = stack.hbp.STATUS[2]
    assert st["RX_TYPE"] == HBPF_SLT_VTERM and bytes_4(_STREAM) not in st["RX_STREAMS"]


def test_a_peer_notes_the_header_lc_of_a_call_from_its_master() -> None:
    hbp = _peer_mode()

    hbp.datagramReceived(_head(), _MASTER_ADDR)

    assert hbp.STATUS[2]["RX_LC"] == _LC
