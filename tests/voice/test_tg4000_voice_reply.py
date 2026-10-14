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

"""TG4000_VOICE: a hotspot's TG/ID 4000 is answered with the link status, to that hotspot only.

Group 4000 → group call on TG 4000 from the server voice ID; private 4000 → private call from
ID 4000 to the caller. "linked to" + the statics still on that slot, else "not linked". Paced on
the reactor with callLater. On a shared MASTER a dynamic leg only the sender held is released.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tests.harness.deterministic import PacketSpec
from tests.harness.voice_helpers import FakeVoiceProvider
from tests.support.hbp_repeat_stack import HbpRepeatStack, build_hbp_repeat_stack

from adn_server.application.voice_use_cases import VoiceUseCases
from adn_server.domain import bytes_3, bytes_4
from adn_server.domain.subscription import SystemId

_A = (213003507).to_bytes(4, "big")
_B = (213003591).to_bytes(4, "big")
_ADDR_A = ("10.0.0.7", 62031)
_ADDR_B = ("10.0.0.8", 62031)
_SYSTEM = "MASTER-A"
_CALLER = 2130035


def _stack(*, voice: bool | None = True, options_a: str | None = None, options_b: str | None = None,
           release: bool = False) -> tuple[HbpRepeatStack, MagicMock]:
    stack = build_hbp_repeat_stack(talker_alias=False, system_name=_SYSTEM)
    stack.config["PROXY"] = {"TARGET_SYSTEM": _SYSTEM}  # the shared MASTER
    if voice is not None:
        stack.hbp._config["TG4000_VOICE"] = voice
    reply = MagicMock()
    stack.hbp._on_tg4000_voice = reply
    stack.hbp._dynamic_tg_uc = MagicMock()
    stack.hbp._on_in_band_signalling = stack.bridge.apply_in_band_signalling
    if release:
        stack.hbp._on_release_dynamic_leg = stack.bridge.release_dynamic_leg
    stack.register_peer(_A, _ADDR_A, options=options_a)
    stack.register_peer(_B, _ADDR_B, options=options_b)
    return stack, reply


def _frame(dtype_vseq: int, *, slot: int = 2, stream: int = 0x40004000, dst: int = 4000,
           src: int = _CALLER, peer: bytes = _A, call_type: str = "group") -> bytes:
    return replace(
        PacketSpec(dst_id=dst, slot=slot, stream_id=stream, call_type=call_type,
                   peer_id=int.from_bytes(peer, "big"), rf_src=src),
        frame_type=2, dtype_vseq=dtype_vseq,
    ).data()


def _over_4000(stack: HbpRepeatStack, **kw: Any) -> None:
    with patch("adn_server.infrastructure.twisted_adapters.udp_hbp.reactor.callInThread",
               side_effect=lambda fn, *a, **k: fn(*a, **k)):
        stack.hbp._master_datagram_received(_frame(1, **kw), _ADDR_A)  # voice header
        stack.hbp._master_datagram_received(_frame(2, **kw), _ADDR_A)  # terminator
        stack.hbp._master_datagram_received(_frame(2, **kw), _ADDR_A)  # repeated terminator


# --- the protocol queues one reply, with what the reply needs ---------------------------------

def test_the_terminator_of_a_4000_over_queues_one_reply_to_that_hotspot() -> None:
    stack, reply = _stack()
    _over_4000(stack, slot=1)
    reply.assert_called_once_with(_SYSTEM, _A, 1, rf_src=bytes_3(_CALLER), private=False, linked=())


def test_a_private_call_to_4000_asks_for_a_private_reply() -> None:
    stack, reply = _stack()
    _over_4000(stack, call_type="unit")
    reply.assert_called_once_with(_SYSTEM, _A, 2, rf_src=bytes_3(_CALLER), private=True, linked=())


def test_the_statics_left_on_that_slot_are_passed_on() -> None:
    stack, reply = _stack(options_a="TS1=91;TS2=214,21472;")
    _over_4000(stack)
    assert reply.call_args.kwargs["linked"] == (214, 21472)


def test_the_reply_is_on_unless_the_system_turns_it_off() -> None:
    stack, reply = _stack(voice=None)  # TG4000_VOICE absent
    _over_4000(stack)
    reply.assert_called_once()
    stack, reply = _stack(voice=False)
    _over_4000(stack)
    reply.assert_not_called()


# --- releasing the legs only the sender held (shared MASTER) ----------------------------------

def _hold_dynamic(stack: HbpRepeatStack, peer: bytes, tgid: int, slot: int = 2) -> None:
    pk = bytes_4(int.from_bytes(peer, "big"))
    stack.hbp._config.setdefault("_PEER_UA_SESSIONS", {}).setdefault(pk, {})[slot] = {
        "tgid": tgid, "expires": 0.0, "source": "local",
    }
    stack.bridge.ensure_dynamic_relay(bytes_3(tgid), _SYSTEM, slot, 900.0)


def _leg_active(stack: HbpRepeatStack, tgid: int, slot: int = 2) -> bool:
    return any(
        sub.is_active() for sub in stack.bridge._subscription_store.list_by_system(SystemId(_SYSTEM))
        if int(sub.target_tgid) == tgid and int(sub.channel.slot) == slot
    )


def test_a_leg_only_the_sender_held_is_released() -> None:
    stack, _ = _stack(release=True)
    _hold_dynamic(stack, _A, 3340)
    _over_4000(stack)
    assert not _leg_active(stack, 3340)


def test_a_leg_another_hotspot_holds_as_dynamic_stays() -> None:
    stack, _ = _stack(release=True)
    _hold_dynamic(stack, _A, 3340)
    _hold_dynamic(stack, _B, 3340)
    _over_4000(stack)
    assert _leg_active(stack, 3340)


def test_a_leg_the_sender_also_has_as_static_stays() -> None:
    """A 4000 clears dynamics, never a static, the sender's own included."""
    stack, _ = _stack(release=True, options_a="TS2=3340;")
    _hold_dynamic(stack, _A, 3340)
    _over_4000(stack)
    assert _leg_active(stack, 3340)


def test_a_leg_another_hotspot_has_as_static_stays() -> None:
    stack, _ = _stack(release=True, options_b="TS2=3340;")
    _hold_dynamic(stack, _A, 3340)
    _over_4000(stack)
    assert _leg_active(stack, 3340)


# --- one frame, one hotspot ---------------------------------------------------------------------

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


def test_a_private_reply_holds_the_hotspots_slot_while_it_plays() -> None:
    stack, _ = _stack()
    private = _frame(1, dst=_CALLER, src=4000, peer=bytes_4(9), stream=0x0B0B0B0B, call_type="unit")
    assert stack.hbp.send_prompt_to_peer(_A, private)
    assert stack.transport.sent[0][0][15] & 0x40  # still a private call on the wire
    assert not stack.hbp.send_prompt_to_peer(_A, _frame(1, dst=3340, src=3340062, stream=0x01010101))


def test_a_prompt_frame_for_a_hotspot_that_left_is_refused() -> None:
    stack, _ = _stack()
    stack.hbp._peers[_A]["CONNECTION"] = "RPTL-RECEIVED"
    assert not stack.hbp.send_prompt_to_peer(_A, _frame(1, dst=4000, src=1000001))
    assert stack.transport.sent == []


# --- VoiceUseCases: words, form, pacing ---------------------------------------------------------

class _Master:
    """Accepts the first ``accept`` frames, then refuses, then would accept again (hotspot free)."""

    def __init__(self, accept: int = 99) -> None:
        self.frames: list[tuple[bytes, bytes]] = []
        self.attempts = 0
        self._accept = accept

    def send_prompt_to_peer(self, peer_id: bytes, pkt: bytes) -> bool:
        self.attempts += 1
        if self.attempts == self._accept + 1:
            return False
        self.frames.append((peer_id, pkt))
        return True


class _Reactor:
    """callFromThread / callLater on a fake clock: each callLater moves time on by its delay."""

    def __init__(self) -> None:
        self.queue: list[tuple[float, Any, tuple]] = []
        self.delays: list[float] = []
        self.now = 1000.0

    def call_from_reactor(self, fn: Any, *a: Any) -> None:
        self.queue.append((0.0, fn, a))

    def call_later(self, delay: float, fn: Any, *a: Any) -> None:
        self.delays.append(delay)
        self.queue.append((delay, fn, a))

    def run(self, work: float = 0.0) -> None:
        """``work``: time each callback takes, so a schedule that drifts shows in the delays."""
        with patch("adn_server.application.voice_use_cases.time.time", side_effect=lambda: self.now):
            while self.queue:
                delay, fn, a = self.queue.pop(0)
                self.now += delay
                fn(*a)
                self.now += work


def _voice(master: _Master, reactor: _Reactor) -> VoiceUseCases:
    config = {"GLOBAL": {}, "SYSTEMS": {_SYSTEM: {"MODE": "MASTER", "ANNOUNCEMENT_LANGUAGE": "es_ES"}}}
    return VoiceUseCases(
        FakeVoiceProvider(), config, get_protocols=lambda: {_SYSTEM: master},
        call_from_reactor=reactor.call_from_reactor, audio_path="/tmp/audio", call_later=reactor.call_later,
    )


def test_a_group_reply_is_a_group_call_from_the_server_to_tg_4000_on_the_same_slot() -> None:
    for slot, ts_bit in ((1, 0x00), (2, 0x80)):
        master, reactor = _Master(), _Reactor()
        assert _voice(master, reactor).tg4000_voice(_SYSTEM, _A, slot, rf_src=bytes_3(_CALLER)) == 3
        assert master.frames == []  # nothing sent from the worker thread
        reactor.run()
        assert len(master.frames) == 3
        for peer_id, pkt in master.frames:
            assert peer_id == _A
            assert (pkt[5:8], pkt[8:11]) == (bytes_3(1000001), bytes_3(4000))
            assert pkt[15] & 0xC0 == ts_bit  # group call on the slot of the 4000


def test_a_private_reply_comes_from_id_4000_to_the_caller() -> None:
    master, reactor = _Master(), _Reactor()
    _voice(master, reactor).tg4000_voice(_SYSTEM, _A, 2, rf_src=bytes_3(_CALLER), private=True)
    reactor.run()
    assert master.frames
    for _peer, pkt in master.frames:
        assert (pkt[5:8], pkt[8:11]) == (bytes_3(4000), bytes_3(_CALLER))
        assert pkt[15] & 0xC0 == 0xC0  # TS2, private call


def test_the_reply_is_paced_on_the_reactor_after_a_second() -> None:
    master, reactor = _Master(), _Reactor()
    _voice(master, reactor).tg4000_voice(_SYSTEM, _A, 2, rf_src=bytes_3(_CALLER))
    reactor.run(work=0.005)  # each frame takes 5 ms to send
    assert reactor.delays[0] == 1.0  # after the over, the radio is back in receive
    # frames keep a fixed 58 ms grid from the start: the 5 ms of work comes off each wait
    assert reactor.delays[1:] == pytest.approx([0.053] * 3)


def test_the_reply_stops_when_the_hotspot_refuses_a_frame() -> None:
    """Once refused, no later frame is even tried: the reply must not resume half-way
    when the hotspot is free again."""
    master, reactor = _Master(accept=1), _Reactor()
    _voice(master, reactor).tg4000_voice(_SYSTEM, _A, 2, rf_src=bytes_3(_CALLER))
    reactor.run()
    assert len(master.frames) == 1
    assert master.attempts == 2  # the accepted frame and the refused one, nothing after


def test_linked_to_names_the_statics_left_else_not_linked() -> None:
    words = {"silence": "_", "linkedto": "LINKED", "notlinked": "NOT", "2": "2", "1": "1", "4": "4", "9": "9", "0": "0"}
    voice = _voice(_Master(), _Reactor())
    voice.get_ambe_words = lambda lang, path: {lang: words}  # type: ignore[method-assign]
    sys_cfg = {"ANNOUNCEMENT_LANGUAGE": "es_ES"}
    assert voice._link_status_words(sys_cfg) == ["_", "_", "NOT", "_"]
    assert voice._link_status_words(sys_cfg, (214, 91)) == ["_", "_", "LINKED", "_", "2", "1", "4", "_", "9", "1", "_"]
    many = voice._link_status_words(sys_cfg, (1, 2, 4, 9, 10))
    assert many.count("_") == 2 + 4 + 1  # at most four TGs are named
