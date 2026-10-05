# ADN DMR Peer Server - tests infrastructure on-demand playback trigger
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

"""A private call to 9991-9999 never played its file: the slot's RX_TYPE was already VTERM."""

from __future__ import annotations

from unittest.mock import patch

from tests.harness.deterministic import DeterministicScenario, PacketSpec
from tests.support.hbp_repeat_stack import build_hbp_repeat_stack

from adn_server.domain import bytes_4

_PEER = bytes_4(730039210)
_ADDR = ("10.0.0.1", 62001)


def _requests(frames) -> list[tuple]:
    stack = build_hbp_repeat_stack()
    stack.register_peer(_PEER, _ADDR, options="TS2=7304;")
    requests: list[tuple] = []
    stack.hbp._on_play_file_request = lambda *a: requests.append(a)
    stack.hbp._dmrd_received = lambda *a, **k: True  # routing accepts the call, as it does live
    base = PacketSpec(peer_id=730039210, rf_src=7300392, dst_id=9993, slot=2, call_type="unit", stream_id=0xA1B2C3D4)
    with patch("adn_server.infrastructure.twisted_adapters.udp_hbp.reactor.callInThread", lambda fn, *a: fn(*a)):
        for frame in frames(base):
            stack.inject_spec(frame, _ADDR)
    return requests


def test_a_private_call_to_9993_plays_file_9993_when_it_ends() -> None:
    requests = _requests(lambda b: [
        DeterministicScenario.voice_head_spec(b),
        DeterministicScenario.voice_burst_spec(b, seq=1, dtype_vseq=1),
        DeterministicScenario.voice_term_spec(b, seq=2),
    ])
    assert requests == [("9993", "MASTER-A")]


def test_a_lone_terminator_requests_nothing() -> None:
    assert _requests(lambda b: [DeterministicScenario.voice_term_spec(b, seq=2)]) == []
