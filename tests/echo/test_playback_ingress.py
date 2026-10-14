# ADN DMR Peer Server - tests echo playback ingress
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

"""Parrot ingress path (PEER dmrd_received + record-to-playback)."""

from __future__ import annotations

import logging
from unittest.mock import patch

from tests.harness.deterministic import DeterministicScenario, PacketSpec
from tests.harness.playback_helpers import (
    FakePlaybackProtocol,
    make_capture_call_later,
    noop_call_later,
    send_playback,
)

from adn_server.application.playback_use_cases import PLAYBACK_DELAY_S, _RECORD_IDLE_S, PlaybackUseCases


def test_dmrd_received_accepts_ingress_pkt_time_kwarg() -> None:
    """Regression: PEER udp_hbp passes ingress_pkt_time (echo must not TypeError)."""
    proto = FakePlaybackProtocol()
    pb = PlaybackUseCases("ECHO", call_later=noop_call_later, get_protocol=lambda: proto)
    base = PacketSpec(dst_id=9990, stream_id=0x12121212, slot=2)
    args = DeterministicScenario.voice_head_spec(base).decoded_hbp_args()

    pb.dmrd_received(
        "ECHO",
        args["peer_id"],
        args["rf_src"],
        args["dst_id"],
        args["seq"],
        args["slot"],
        args["call_type"],
        args["frame_type"],
        args["dtype_vseq"],
        args["stream_id"],
        DeterministicScenario.voice_head_spec(base).data(),
        ingress_pkt_time=1_700_000_000.0,
    )

    assert pb._recording_active is True
    assert len(pb.CALL_DATA) == 1


def test_ingress_pkt_time_enables_record_to_playback(caplog) -> None:
    """Regression: PEER path (ingress_pkt_time) records voice and schedules playback."""
    proto = FakePlaybackProtocol()
    call_later, scheduled = make_capture_call_later()
    pb = PlaybackUseCases("ECHO", call_later=call_later, get_protocol=lambda: proto)
    base = PacketSpec(dst_id=9990, stream_id=0x34343434, slot=2)
    t0 = 1_700_000_000.0

    with patch("adn_server.application.playback_use_cases.time", return_value=t0):
        send_playback(pb, "ECHO", DeterministicScenario.voice_head_spec(base))
        send_playback(
            pb,
            "ECHO",
            DeterministicScenario.voice_burst_spec(base, seq=1, dtype_vseq=1),
            ingress_pkt_time=t0 + 2.0,
        )
    with patch("adn_server.application.playback_use_cases.time", return_value=t0 + _RECORD_IDLE_S + 1):
        with caplog.at_level(logging.INFO, logger="adn_server.application.playback_use_cases"):
            pb._on_record_idle(proto)

    assert any(item[0] == PLAYBACK_DELAY_S for item in scheduled)
    assert pb._playback_busy is True
    assert any("*END   RECORDING*" in r.message for r in caplog.records)
    assert not any("unexpected keyword argument" in r.message for r in caplog.records)
