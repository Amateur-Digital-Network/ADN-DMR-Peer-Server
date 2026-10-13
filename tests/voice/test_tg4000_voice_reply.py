# ADN DMR Peer Server - tests voice TG 4000 voice reply
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

"""TG4000_VOICE: a hotspot's TG/ID 4000 is answered with the link status, to that hotspot only."""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock, patch

from tests.harness.deterministic import PacketSpec
from tests.harness.voice_helpers import FakeVoiceProvider
from tests.support.hbp_repeat_stack import HbpRepeatStack, build_hbp_repeat_stack

from adn_server.application.voice_use_cases import VoiceUseCases
from adn_server.domain import bytes_3, bytes_4

_A = (213003507).to_bytes(4, "big")
_B = (213003591).to_bytes(4, "big")
_ADDR_A = ("10.0.0.7", 62031)
_ADDR_B = ("10.0.0.8", 62031)
_SYSTEM = "MASTER-A"


def _stack(*, voice: bool | None = True) -> tuple[HbpRepeatStack, MagicMock]:
    stack = build_hbp_repeat_stack(talker_alias=False, system_name=_SYSTEM)
    stack.config["PROXY"] = {"TARGET_SYSTEM": _SYSTEM}  # the shared MASTER
    if voice is not None:
        stack.hbp._config["TG4000_VOICE"] = voice
    reply = MagicMock()
    stack.hbp._on_tg4000_voice = reply
    stack.hbp._dynamic_tg_uc = MagicMock()
    stack.register_peer(_A, _ADDR_A)
    stack.register_peer(_B, _ADDR_B)
    return stack, reply


def _frame(dtype_vseq: int, *, slot: int = 2, stream: int = 0x40004000, dst: int = 4000,
           src: int = 2130035, peer: bytes = _A, call_type: str = "group") -> bytes:
    return replace(
        PacketSpec(dst_id=dst, slot=slot, stream_id=stream, call_type=call_type,
                   peer_id=int.from_bytes(peer, "big"), rf_src=src),
        frame_type=2, dtype_vseq=dtype_vseq,
    ).data()


def _over_4000(stack: HbpRepeatStack, **kw: Any) -> None:
    with patch("adn_server.infrastructure.twisted_adapters.udp_hbp.reactor.callInThread",
               side_effect=lambda fn, *a: fn(*a)):
        stack.hbp._master_datagram_received(_frame(1, **kw), _ADDR_A)  # voice header
        stack.hbp._master_datagram_received(_frame(2, **kw), _ADDR_A)  # terminator
        stack.hbp._master_datagram_received(_frame(2, **kw), _ADDR_A)  # repeated terminator


def test_the_terminator_of_a_4000_over_queues_one_reply_to_that_hotspot() -> None:
    stack, reply = _stack()
    _over_4000(stack, slot=1)
    reply.assert_called_once_with(_SYSTEM, _A, 1)


def test_a_private_call_to_4000_is_answered_too() -> None:
    stack, reply = _stack()
    _over_4000(stack, call_type="unit")
    reply.assert_called_once_with(_SYSTEM, _A, 2)


def test_no_reply_unless_the_system_asks_for_it() -> None:
    for voice in (None, False):
        stack, reply = _stack(voice=voice)
        _over_4000(stack)
        reply.assert_not_called()


def test_a_prompt_frame_reaches_only_its_hotspot() -> None:
    stack, _ = _stack()
    pkt = _frame(1, dst=4000, src=1000001, peer=bytes_4(9), stream=0x0A0A0A0A)
    assert stack.hbp.send_prompt_to_peer(_A, pkt)
    sent = stack.transport.sent
    assert [addr for _p, addr in sent] == [_ADDR_A]
    assert sent[0][0][11:15] == _A  # addressed to that hotspot


def test_a_prompt_frame_is_refused_while_the_hotspot_hears_another_stream() -> None:
    stack, _ = _stack()
    assert stack.hbp.send_prompt_to_peer(_A, _frame(1, dst=3340, src=3340062, stream=0x01010101))
    assert not stack.hbp.send_prompt_to_peer(_A, _frame(1, dst=4000, src=1000001, stream=0x0A0A0A0A))
    assert stack.hbp.send_prompt_to_peer(_B, _frame(1, dst=4000, src=1000001, stream=0x0A0A0A0A))  # B is free


def test_a_prompt_frame_for_a_hotspot_that_left_is_refused() -> None:
    stack, _ = _stack()
    stack.hbp._peers[_A]["CONNECTION"] = "RPTL-RECEIVED"
    assert not stack.hbp.send_prompt_to_peer(_A, _frame(1, dst=4000, src=1000001))
    assert stack.transport.sent == []


class _Master:
    def __init__(self, accept: int = 99) -> None:
        self.frames: list[tuple[bytes, bytes]] = []
        self._accept = accept

    def send_prompt_to_peer(self, peer_id: bytes, pkt: bytes) -> bool:
        if len(self.frames) >= self._accept:
            return False
        self.frames.append((peer_id, pkt))
        return True


def _voice(master: _Master) -> VoiceUseCases:
    config = {"GLOBAL": {}, "SYSTEMS": {_SYSTEM: {"MODE": "MASTER", "ANNOUNCEMENT_LANGUAGE": "es_ES"}}}
    return VoiceUseCases(
        FakeVoiceProvider(), config, get_protocols=lambda: {_SYSTEM: master},
        call_from_reactor=lambda fn, *a: fn(*a), audio_path="/tmp/audio",
    )


def test_the_reply_is_a_group_call_from_the_server_to_tg_4000_on_the_same_slot() -> None:
    for slot, ts_bit in ((1, 0x00), (2, 0x80)):
        master = _Master()
        with patch("adn_server.application.voice_use_cases.time.sleep"):
            sent = _voice(master).tg4000_voice(_SYSTEM, _A, slot)
        assert sent == len(master.frames) == 3
        for peer_id, pkt in master.frames:
            assert peer_id == _A
            assert pkt[8:11] == bytes_3(4000)
            assert pkt[5:8] == bytes_3(1000001)
            assert pkt[15] & 0x80 == ts_bit


def test_the_reply_stops_when_the_hotspot_refuses_a_frame() -> None:
    master = _Master(accept=1)
    with patch("adn_server.application.voice_use_cases.time.sleep"):
        assert _voice(master).tg4000_voice(_SYSTEM, _A, 2) == 1
