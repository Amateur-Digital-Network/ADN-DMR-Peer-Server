# ADN DMR Peer Server plugin - voice-announcements tests
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

"""Scheduling, per-TG queue, busy slots, QSO cut, hourly and TTS — on a simulated reactor."""

from __future__ import annotations

import heapq
import itertools
from datetime import datetime
from types import SimpleNamespace

import pytest
from plugin.application import announcer as ann
from plugin.application.announcer import Announcer
from plugin.application.plugin_impl import VoiceAnnouncementsPlugin


class Reactor:
    """call_later on a fake clock; the announcer's time.time follows it."""

    def __init__(self) -> None:
        self.now = 1000.0
        self._heap: list = []
        self._seq = itertools.count()

    def call_later(self, delay, fn, *args):
        handle = SimpleNamespace(cancelled=False, called=False)
        handle.active = lambda h=handle: not (h.cancelled or h.called)
        handle.cancel = lambda h=handle: setattr(h, "cancelled", True)
        heapq.heappush(self._heap, (self.now + delay, next(self._seq), handle, fn, args))
        return handle

    def advance(self, seconds: float) -> None:
        end = self.now + seconds
        while self._heap and self._heap[0][0] <= end:
            when, _, handle, fn, args = heapq.heappop(self._heap)
            self.now = when
            if not handle.cancelled:
                handle.called = True
                fn(*args)
        self.now = end


class Voice:
    frames = 5

    def read_single_file(self, audio_path, lang, name):
        return [["ambe"]] if name != "missing" else []

    def pkt_gen(self, src, dst, peer, slot_bit, phrase):
        return [b"DMRD" + bytes([n]) + src + dst + peer + bytes([slot_bit << 7]) for n in range(self.frames)]

    def ensure_tts_ambe(self, config, item, audio_path):
        return "/tmp/x.ambe"


class Deferred:
    def __init__(self, result):
        self.result = result

    def addCallback(self, fn, *a):
        if not isinstance(self.result, Exception):
            fn(self.result, *a)
        return self

    def addErrback(self, fn, *a):
        if isinstance(self.result, Exception):
            fn(self.result, *a)
        return self


def _ctx(reactor, voice_cfg, *, slots=None, refuse_at=None):
    sent: list[bytes] = []
    slots = slots if slots is not None else [2]

    def send(pkt):
        if refuse_at is not None and len(sent) == refuse_at:
            return False
        sent.append(pkt)
        return True

    def slot_for_tg(tg):
        return slots.pop(0) if len(slots) > 1 else slots[0]

    ctx = SimpleNamespace(
        config={"GLOBAL": {"SERVER_ID": 2131}, "VOICE": voice_cfg},
        project_root="/tmp", call_later=reactor.call_later, send_dmrd=send, voice_slot_for_tg=slot_for_tg,
        defer_to_thread=lambda fn, *a: Deferred(fn(*a)),
    )
    return ctx, sent


@pytest.fixture
def reactor(monkeypatch):
    r = Reactor()
    monkeypatch.setattr(ann.time, "time", lambda: r.now)
    return r


def _ann(file="beacon", tg=213, interval=60, **kw):
    return {"ENABLED": True, "FILE": file, "TG": tg, "MODE": "interval", "INTERVAL": interval, "LANGUAGE": "es_ES", **kw}


def test_an_interval_announcement_plays_every_interval(reactor) -> None:
    ctx, sent = _ctx(reactor, {"ANNOUNCEMENTS": [_ann(DMR_ID=2130035)]})
    a = Announcer(ctx, Voice())
    a.reconcile()
    reactor.advance(59)
    assert sent == []
    reactor.advance(2)
    assert len(sent) == Voice.frames
    assert sent[0][5:8] == (2130035).to_bytes(3, "big") and sent[0][8:11] == (213).to_bytes(3, "big")
    assert sent[0][-1] == 0x80  # TS2, as voice_slot_for_tg said
    reactor.advance(60)
    assert len(sent) == 2 * Voice.frames


def test_frames_are_paced_58_ms_apart(reactor) -> None:
    ctx, _ = _ctx(reactor, {"ANNOUNCEMENTS": [_ann()]})
    times: list[float] = []
    send = ctx.send_dmrd
    ctx.send_dmrd = lambda pkt: times.append(reactor.now) or send(pkt)
    Announcer(ctx, Voice()).reconcile()
    reactor.advance(61)
    gaps = [round(b - a, 3) for a, b in zip(times, times[1:])]
    assert gaps == [0.058] * (Voice.frames - 1)


def test_a_refused_frame_stops_the_playback(reactor) -> None:
    ctx, sent = _ctx(reactor, {"ANNOUNCEMENTS": [_ann()]}, refuse_at=2)
    a = Announcer(ctx, Voice())
    a.reconcile()
    reactor.advance(61)
    assert len(sent) == 2
    assert not a._running  # free to play at the next interval


def test_two_items_on_one_tg_take_turns(reactor) -> None:
    ctx, sent = _ctx(reactor, {"ANNOUNCEMENTS": [_ann("one"), _ann("two")]})
    Announcer(ctx, Voice()).reconcile()
    reactor.advance(60.6)  # both fired at 60 s; the first is playing
    assert 0 < len(sent) <= Voice.frames
    reactor.advance(10)
    assert len(sent) == 2 * Voice.frames


def test_busy_slots_are_retried_every_5_s(reactor) -> None:
    ctx, sent = _ctx(reactor, {"ANNOUNCEMENTS": [_ann()]}, slots=[None, None, 1])
    Announcer(ctx, Voice()).reconcile()
    reactor.advance(61)
    assert sent == []
    reactor.advance(10.6)
    assert len(sent) == Voice.frames and sent[0][-1] == 0x00  # TS1 once free


def test_hourly_plays_once_in_the_first_minute_of_the_hour(reactor) -> None:
    clock = {"t": datetime(2026, 9, 24, 21, 59, 10)}
    ctx, sent = _ctx(reactor, {"ANNOUNCEMENTS": [_ann(MODE="hourly")]})
    Announcer(ctx, Voice(), now=lambda: clock["t"]).reconcile()
    reactor.advance(31)
    assert sent == []
    clock["t"] = datetime(2026, 9, 24, 22, 0, 5)
    reactor.advance(31)
    assert len(sent) == Voice.frames
    clock["t"] = datetime(2026, 9, 24, 22, 0, 40)
    reactor.advance(31)
    assert len(sent) == Voice.frames  # not twice in the same hour


def test_tts_is_encoded_in_a_thread_then_played(reactor) -> None:
    ctx, sent = _ctx(reactor, {"TTS_ANNOUNCEMENTS": [_ann("texto1")]})
    calls: list = []
    real = ctx.defer_to_thread
    ctx.defer_to_thread = lambda fn, *a: calls.append(fn.__name__) or real(fn, *a)
    Announcer(ctx, Voice()).reconcile()
    reactor.advance(61)
    assert calls == ["ensure_tts_ambe"] and len(sent) == Voice.frames


def test_a_config_change_restarts_and_disabling_stops(reactor) -> None:
    voice = {"ANNOUNCEMENTS": [_ann(interval=60)]}
    ctx, sent = _ctx(reactor, voice)
    a = Announcer(ctx, Voice())
    a.reconcile()
    voice["ANNOUNCEMENTS"][0] = _ann(interval=10)
    a.reconcile()
    reactor.advance(11)
    assert len(sent) == Voice.frames
    voice["ANNOUNCEMENTS"][0]["ENABLED"] = False
    a.reconcile()
    reactor.advance(100)
    assert len(sent) == Voice.frames


def test_a_missing_file_is_skipped(reactor) -> None:
    ctx, sent = _ctx(reactor, {"ANNOUNCEMENTS": [_ann("missing")]})
    a = Announcer(ctx, Voice())
    a.reconcile()
    reactor.advance(61)
    assert sent == [] and not a._running


def test_without_a_send_grant_the_plugin_does_nothing(reactor) -> None:
    ctx, _ = _ctx(reactor, {"ANNOUNCEMENTS": [_ann()]})
    ctx.send_dmrd = None
    plugin = VoiceAnnouncementsPlugin()
    plugin.on_load(None, {}, ctx)
    assert plugin._announcer is None
