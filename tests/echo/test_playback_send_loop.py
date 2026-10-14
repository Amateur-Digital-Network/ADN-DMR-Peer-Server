# ADN DMR Peer Server - tests echo playback send loop
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

"""Parrot playback send loop and max recording timer."""

from __future__ import annotations

from unittest.mock import patch

from tests.harness.deterministic import DeterministicScenario, PacketSpec
from tests.harness.playback_helpers import FakePlaybackProtocol, make_capture_call_later, run_scheduled

from adn_server.application.playback_use_cases import (
    _PACKET_INTERVAL_S,
    PLAYBACK_DELAY_S,
    _SOURCE_MAX_S,
    PlaybackUseCases,
)
from adn_server.domain import bytes_3, bytes_4


def test_send_next_packet_emits_all_packets_then_finishes() -> None:
    proto = FakePlaybackProtocol()
    call_later, scheduled = make_capture_call_later()
    pb = PlaybackUseCases("ECHO", call_later=call_later, get_protocol=lambda: proto)
    pb._playback_busy = True
    pb._playback_stream_id = bytes_4(0x77777777)
    pb._playback_packets = [bytes([i]) * 55 for i in range(1, 5)]
    pb._playback_index = 0

    pb._send_next_packet(proto)
    while scheduled:
        run_scheduled(scheduled)

    assert len(proto.sent) == 4
    assert pb._playback_busy is False
    assert pb._playback_packets == []
    assert pb._playback_index == 0


def test_start_playback_sends_first_packet_and_schedules_rest() -> None:
    proto = FakePlaybackProtocol()
    call_later, scheduled = make_capture_call_later()
    pb = PlaybackUseCases("ECHO", call_later=call_later, get_protocol=lambda: proto)
    base = PacketSpec(dst_id=9990, stream_id=0x88888888, slot=2)
    recorded = [
        DeterministicScenario.voice_head_spec(base).data(),
        DeterministicScenario.voice_burst_spec(base, seq=1, dtype_vseq=1).data(),
    ]

    pb._start_playback(
        proto,
        recorded,
        bytes_3(base.rf_src),
        bytes_4(base.peer_id),
        bytes_3(base.dst_id),
        2,
        1.5,
    )

    assert len(proto.sent) == 1
    assert pb._playback_index == 1
    assert any(item[0] == _PACKET_INTERVAL_S for item in scheduled)
    while scheduled:
        run_scheduled(scheduled)

    assert len(proto.sent) == 2
    assert pb._playback_busy is False


def test_max_duration_commits_recording_when_no_vterm() -> None:
    proto = FakePlaybackProtocol()
    call_later, scheduled = make_capture_call_later()
    pb = PlaybackUseCases("ECHO", call_later=call_later, get_protocol=lambda: proto)
    base = PacketSpec(dst_id=9990, stream_id=0x99999999, slot=2)
    pb._recording_active = True
    pb.CALL_DATA = [DeterministicScenario.voice_head_spec(base).data()]
    pb._record_stream = base.data()[16:20]
    pb.STATUS["RX_START"] = 100.0
    pb._record_ctx = {
        "slot": 2,
        "rf_src": bytes_3(base.rf_src),
        "peer_id": bytes_4(base.peer_id),
        "dst_id": bytes_3(base.dst_id),
    }

    with patch("adn_server.application.playback_use_cases.time", return_value=100.0 + _SOURCE_MAX_S):
        pb._on_record_max_duration(proto)

    assert not pb._recording_active
    assert pb._playback_busy is True
    assert any(item[0] == PLAYBACK_DELAY_S for item in scheduled)


def test_packet_interval_matches_expected() -> None:
    proto = FakePlaybackProtocol()
    call_later, scheduled = make_capture_call_later()
    pb = PlaybackUseCases("ECHO", call_later=call_later, get_protocol=lambda: proto)
    pb._playback_busy = True
    pb._playback_stream_id = bytes_4(0xAAAAAAAA)
    pb._playback_packets = [b"\x01" * 55, b"\x02" * 55]
    pb._playback_index = 0

    pb._send_next_packet(proto)

    assert scheduled[0][0] == _PACKET_INTERVAL_S
