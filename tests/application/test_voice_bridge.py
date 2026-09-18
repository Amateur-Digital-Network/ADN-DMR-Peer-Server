# ADN DMR Peer Server - tests voice plugin bridge
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

"""VoicePluginBridge emit helpers and frame-only notify."""

from __future__ import annotations

from adn_server.application.plugins.application.bus import PluginBus
from adn_server.application.plugins.application.voice_bridge import VoicePluginBridge
from adn_server.application.plugins.domain.events import VoiceCallEnd, VoiceCallFrame, VoiceCallStart
from adn_server.domain import HBPF_DATA_SYNC, HBPF_SLT_VHEAD, HBPF_SLT_VTERM, HBPF_VOICE


def _stream_id(n: int) -> bytes:
    return n.to_bytes(4, "big")


def _rf(n: int) -> bytes:
    return n.to_bytes(3, "big")


def _tg(n: int) -> bytes:
    return n.to_bytes(3, "big")


def _bridge() -> tuple[VoicePluginBridge, list[object]]:
    events: list[object] = []
    bus = PluginBus()
    bus.subscribe(events.append)
    config = {
        "GLOBAL": {"SERVER_ID": 1},
        "SYSTEMS": {"OBP-CL": {"MODE": "OPENBRIDGE"}},
    }
    return VoicePluginBridge(bus, config), events


def test_emit_group_voice_start_is_idempotent() -> None:
    bridge, events = _bridge()
    kw = dict(
        system_name="OBP-CL",
        peer_id=b"\x00\x00\x01\x00",
        rf_src=_rf(0x334202),
        dst_id=_tg(0x33420),
        slot=1,
        stream_id=_stream_id(42),
        pkt_time=100.0,
        source_is_obp=True,
        voice_phase="INGRESS",
    )
    assert bridge.emit_group_voice_start(**kw) is True
    assert bridge.emit_group_voice_start(**kw) is False
    assert len(events) == 1
    assert isinstance(events[0], VoiceCallStart)
    assert events[0].context.extra.get("voice_phase") == "INGRESS"


def test_emit_group_voice_end_is_idempotent() -> None:
    bridge, events = _bridge()
    kw = dict(
        system_name="OBP-CL",
        peer_id=b"\x00\x00\x01\x00",
        rf_src=_rf(0x334202),
        dst_id=_tg(0x33420),
        slot=1,
        stream_id=_stream_id(42),
        pkt_time=110.0,
        duration_s=10.0,
        source_is_obp=True,
    )
    assert bridge.emit_group_voice_end(**kw) is True
    assert bridge.emit_group_voice_end(**kw) is False
    assert len(events) == 1
    assert isinstance(events[0], VoiceCallEnd)


def test_notify_group_after_forward_emits_frames_only() -> None:
    bridge, events = _bridge()
    bridge.notify_group_after_forward(
        system_name="OBP-CL",
        peer_id=b"\x00\x00\x01\x00",
        rf_src=_rf(0x334202),
        dst_id=_tg(0x33420),
        slot=1,
        stream_id=_stream_id(42),
        frame_type=HBPF_VOICE,
        dtype_vseq=1,
        data=b"\x00" * 60,
        pkt_time=101.0,
        source_is_obp=True,
        obp_hops=b"\x00",
        obp_source_server=None,
        obp_ber=b"\x00",
        obp_rssi=b"\x00",
        obp_source_rptr=b"\x00",
        synthetic_announcement=False,
        forwarded=["HBP-1"],
    )
    assert len(events) == 1
    assert isinstance(events[0], VoiceCallFrame)
    bridge.notify_group_after_forward(
        system_name="OBP-CL",
        peer_id=b"\x00\x00\x01\x00",
        rf_src=_rf(0x334202),
        dst_id=_tg(0x33420),
        slot=1,
        stream_id=_stream_id(42),
        frame_type=HBPF_DATA_SYNC,
        dtype_vseq=HBPF_SLT_VHEAD,
        data=b"\x00" * 60,
        pkt_time=101.0,
        source_is_obp=True,
        obp_hops=b"\x00",
        obp_source_server=None,
        obp_ber=b"\x00",
        obp_rssi=b"\x00",
        obp_source_rptr=b"\x00",
        synthetic_announcement=False,
        forwarded=[],
    )
    assert len(events) == 1
