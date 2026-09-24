# ADN DMR Peer Server - tests server prompts hold the slot they play on
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

"""Ident, on-demand and disconnected prompts hold TS2 while they play, like broadcasts do."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from tests.harness.voice_helpers import FakeMasterForVoice, FakeVoiceProvider, voice_master_scenario

from adn_server.application.routing.helpers import hbp_slot_blocks_group_voice
from adn_server.application.voice_use_cases import VoiceUseCases
from adn_server.domain import HBPF_SLT_VHEAD, HBPF_SLT_VTERM, bytes_3

PROMPT_STREAM = b"\xaa\xbb\xcc\xdd"
OTHER_STREAM = b"\x01\x02\x03\x04"
FRAMES = 6


def _speech(n: int = FRAMES) -> list[bytes]:
    return [b"DMRD" + b"\x00" * 12 + PROMPT_STREAM + bytes([i]) * 33 for i in range(n)]


def _idle_slot() -> dict:
    return {"RX_TYPE": HBPF_SLT_VTERM, "TX_TYPE": HBPF_SLT_VTERM, "RX_TIME": 0.0, "TX_TIME": 0.0}


class _Master(FakeMasterForVoice):
    """Records, per frame sent, whether routing would let another TG onto the slot."""

    def __init__(self, name: str, on_frame=None) -> None:
        super().__init__(name)
        self.voice_packets: list[bytes] = []
        self.other_tg_admitted: list[bool] = []
        self._on_frame = on_frame

    def send_voice_packet(self, packet: bytes, _source_id: bytes, _dst_id: bytes, slot: dict) -> None:
        self.voice_packets.append(packet)
        blocked = hbp_slot_blocks_group_voice(slot, bytes_3(214), OTHER_STREAM, time.time(), 0)
        self.other_tg_admitted.append(not blocked)
        if self._on_frame:
            self._on_frame(len(self.voice_packets), slot)


def _uc(master: _Master) -> VoiceUseCases:
    scenario, _ = voice_master_scenario()
    return VoiceUseCases(
        FakeVoiceProvider(),
        scenario.config,
        get_protocols=lambda: {"MASTER-A": master},
        call_from_reactor=lambda fn, *args: fn(*args),
        audio_path="/tmp/audio",
    )


def _play(uc: VoiceUseCases, master: _Master) -> int:
    with patch("adn_server.application.voice_use_cases.time") as mock_time:
        mock_time.time.side_effect = time.time
        mock_time.sleep = MagicMock()
        return uc.play_on_slot(master, "MASTER-A", _speech(), bytes_3(9990), bytes_3(9))


def test_routed_group_voice_is_kept_off_the_slot_while_a_prompt_plays() -> None:
    master = _Master("MASTER-A")
    master.STATUS[2] = _idle_slot()
    uc = _uc(master)

    with patch("adn_server.application.voice_use_cases.time") as mock_time:
        mock_time.time.side_effect = time.time
        mock_time.sleep = MagicMock()
        uc.play_file_on_request("9991", "MASTER-A")

    assert master.voice_packets
    # With GROUP_HANGTIME 0 only the slot state keeps a second stream off TS2: the
    # prompt used to leave it at VTERM, so routing admitted another TG on every frame.
    assert not any(master.other_tg_admitted)


def test_the_slot_is_released_when_the_prompt_ends() -> None:
    master = _Master("MASTER-A")
    master.STATUS[2] = _idle_slot()

    _play(_uc(master), master)

    assert master.STATUS[2]["TX_TYPE"] == HBPF_SLT_VTERM


def test_a_radio_keying_up_stops_the_prompt() -> None:
    def key_up(n: int, slot: dict) -> None:
        if n == 2:
            slot["RX_TYPE"] = HBPF_SLT_VHEAD
            slot["RX_TIME"] = time.time()

    master = _Master("MASTER-A", on_frame=key_up)
    master.STATUS[2] = _idle_slot()

    sent = _play(_uc(master), master)

    assert sent == 2
    assert master.STATUS[2]["TX_TYPE"] == HBPF_SLT_VTERM


def test_a_call_already_on_the_slot_is_left_alone() -> None:
    master = _Master("MASTER-A")
    routed = {"TX_TYPE": HBPF_SLT_VHEAD, "TX_STREAM_ID": OTHER_STREAM, "TX_TIME": time.time()}
    master.STATUS[2] = {**_idle_slot(), **routed}

    sent = _play(_uc(master), master)

    assert sent == 0
    assert master.voice_packets == []
    assert {k: master.STATUS[2][k] for k in routed} == routed
