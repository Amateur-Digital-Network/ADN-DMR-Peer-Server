# ADN DMR Peer Server - tests plugin bus event interest and batching
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

"""Plugins pay only for the events they take; deferred events cost one call_later per tick."""

from __future__ import annotations

import pytest

from adn_server.application.plugins.application import voice_bridge as vb
from adn_server.application.plugins.application.bus import PluginBus
from adn_server.application.plugins.application.data_bridge import DataPluginBridge
from adn_server.application.plugins.application.voice_bridge import VoicePluginBridge
from adn_server.application.plugins.domain.events import (
    UnitDataEnd,
    UnitDataFrame,
    UnitDataStart,
    VoiceCallEnd,
    VoiceCallFrame,
    VoiceCallStart,
)
from adn_server.domain import HBPF_DATA_SYNC, HBPF_VOICE


class _Plugin:
    def __init__(self, name: str, events=None) -> None:
        self.name = name
        if events is not None:
            self.events = events
        self.got: list[object] = []

    def on_event(self, event: object) -> None:
        self.got.append(event)

    def on_shutdown(self) -> None:
        pass


CONFIG = {"GLOBAL": {"SERVER_ID": 2131}, "SYSTEMS": {"SYSTEM": {"MODE": "MASTER"}}}


def _voice_frame(bridge: VoicePluginBridge, stream: int = 7) -> None:
    bridge.notify_group_after_forward(
        system_name="SYSTEM", peer_id=b"\0\0\0\1", rf_src=b"\0\0\1", dst_id=b"\0\0\xd5", slot=2,
        frame_type=HBPF_VOICE, dtype_vseq=1, stream_id=stream.to_bytes(4, "big"), data=b"DMRD" + bytes(51),
        pkt_time=1.0, source_is_obp=False, obp_hops=b"", obp_source_server=None, obp_ber=b"\0",
        obp_rssi=b"\0", obp_source_rptr=b"\0\0\0\0", synthetic_announcement=False, forwarded=[],
    )


def _data_frame(bridge: DataPluginBridge, dtype: int = 7) -> None:
    bridge.notify_after_forward(
        route="unit", system_name="SYSTEM", peer_id=b"\0\0\0\1", rf_src=b"\0\0\1", dst_id=b"\x0d\xbb\xa7",
        seq=1, slot=2, frame_type=HBPF_DATA_SYNC, dtype_vseq=dtype, stream_id=b"\0\0\0\x09",
        data=b"DMRD" + bytes(51), pkt_time=1.0, source_is_obp=False, obp_hops=b"", obp_source_server=None,
        obp_ber=b"\0", obp_rssi=b"\0", obp_source_rptr=b"\0\0\0\0", forwarded=[],
    )


def test_a_plugin_without_events_still_gets_everything() -> None:
    bus = PluginBus()
    legacy = _Plugin("legacy")
    bus.register_plugin(legacy)
    assert bus.wants(VoiceCallFrame) and bus.wants(UnitDataEnd)
    _voice_frame(VoicePluginBridge(bus, CONFIG))
    assert [type(e) for e in legacy.got] == [VoiceCallFrame]


def test_declared_events_are_the_only_ones_delivered() -> None:
    bus = PluginBus()
    data = _Plugin("d-aprs", events=(UnitDataFrame,))
    voice = _Plugin("igate", events=(VoiceCallFrame, VoiceCallEnd))
    bus.register_plugin(data)
    bus.register_plugin(voice)
    _voice_frame(VoicePluginBridge(bus, CONFIG))
    _data_frame(DataPluginBridge(bus, CONFIG))
    assert [type(e) for e in voice.got] == [VoiceCallFrame]
    assert [type(e) for e in data.got] == [UnitDataFrame]


def test_voice_frames_are_not_even_built_when_nobody_takes_them(monkeypatch) -> None:
    bus = PluginBus()
    bus.register_plugin(_Plugin("d-aprs", events=(UnitDataStart, UnitDataFrame, UnitDataEnd)))
    bridge = VoicePluginBridge(bus, CONFIG)

    def boom(*a, **k):
        raise AssertionError("voice event built for nobody")

    monkeypatch.setattr(bridge, "_group_ctx_base", boom)
    monkeypatch.setattr(vb, "CallLegContext", boom)
    _voice_frame(bridge)
    assert not bridge.has_subscribers()


def test_unregistering_the_last_interested_plugin_stops_the_work() -> None:
    bus = PluginBus()
    igate = _Plugin("igate", events=(VoiceCallFrame,))
    bus.register_plugin(igate)
    assert bus.wants(VoiceCallFrame)
    bus.unregister_plugin(igate)
    assert not bus.wants(VoiceCallFrame) and not bus.wants_any(VoiceCallStart, UnitDataFrame)


def test_an_internal_handler_takes_everything() -> None:
    bus = PluginBus()
    bus.subscribe(lambda e: None)
    assert bus.wants(VoiceCallFrame) and bus.wants(UnitDataStart)


def test_deferred_events_of_a_tick_share_one_call_later_and_keep_their_order() -> None:
    scheduled: list = []
    bus = PluginBus()
    bus.set_call_later(lambda delay, fn, *a: scheduled.append((fn, a)))
    plugin = _Plugin("p")
    bus.register_plugin(plugin)
    events = [object(), object(), object()]
    for e in events:
        bus.emit_deferred(e)
    assert len(scheduled) == 1 and plugin.got == []
    fn, args = scheduled.pop()
    fn(*args)
    assert plugin.got == events
    bus.emit_deferred(events[0])  # next tick schedules again
    assert len(scheduled) == 1


def test_a_deferred_event_nobody_takes_is_not_scheduled() -> None:
    scheduled: list = []
    bus = PluginBus()
    bus.set_call_later(lambda delay, fn, *a: scheduled.append(fn))
    bus.register_plugin(_Plugin("d-aprs", events=(UnitDataFrame,)))
    bus.emit_deferred(VoiceCallFrame(context=None, dmrpkt=b"", frame_type=0, dtype_vseq=0))
    assert scheduled == []


@pytest.mark.parametrize("calls", [3, vb._ENDED_KEEP + 10])
def test_stream_bookkeeping_does_not_grow_without_bound(calls: int) -> None:
    bus = PluginBus()
    bus.register_plugin(_Plugin("p"))
    bridge = VoicePluginBridge(bus, CONFIG)
    kw = dict(system_name="SYSTEM", peer_id=b"\0\0\0\1", rf_src=b"\0\0\1", dst_id=b"\0\0\xd5", slot=2,
              pkt_time=1.0, source_is_obp=False)
    for n in range(calls):
        sid = n.to_bytes(4, "big")
        assert bridge.emit_group_voice_start(stream_id=sid, **kw)
        assert bridge.emit_group_voice_end(stream_id=sid, duration_s=1.0, **kw)
        assert not bridge.emit_group_voice_end(stream_id=sid, duration_s=1.0, **kw)  # still once
    assert bridge._plugin_started == set()
    assert len(bridge._plugin_ended) == min(calls, vb._ENDED_KEEP)


def test_a_stream_resolves_its_aliases_once_and_each_event_owns_its_extra(monkeypatch) -> None:
    calls: list[int] = []
    real = vb.alias_extra
    monkeypatch.setattr(vb, "alias_extra", lambda *a: calls.append(1) or real(*a))
    bus = PluginBus()
    plugin = _Plugin("igate", events=(VoiceCallFrame,))
    bus.register_plugin(plugin)
    bridge = VoicePluginBridge(bus, {**CONFIG, "_SUB_IDS": {1: "C31AG"}})
    for _ in range(6):
        _voice_frame(bridge)
    assert len(calls) == 1
    assert plugin.got[0].context.extra == {"src_callsign": "C31AG"}
    plugin.got[0].context.extra["mutated"] = True
    assert "mutated" not in plugin.got[1].context.extra


def test_the_cached_context_is_dropped_when_the_stream_ends() -> None:
    bus = PluginBus()
    bus.register_plugin(_Plugin("p"))
    bridge = VoicePluginBridge(bus, CONFIG)
    _voice_frame(bridge, stream=7)
    assert bridge._stream_ctx
    bridge.emit_group_voice_end(system_name="SYSTEM", peer_id=b"\0\0\0\1", rf_src=b"\0\0\1", dst_id=b"\0\0\xd5",
                                slot=2, stream_id=(7).to_bytes(4, "big"), pkt_time=2.0, duration_s=1.0,
                                source_is_obp=False)
    assert bridge._stream_ctx == {}
