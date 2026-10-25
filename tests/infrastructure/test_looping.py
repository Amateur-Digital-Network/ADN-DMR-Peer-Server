# ADN DMR Peer Server - tests infrastructure looping
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

"""A periodic task logs an unhandled error and keeps running."""

from __future__ import annotations

import logging

import pytest
from tests.support.hbp_repeat_stack import build_hbp_repeat_stack
from twisted.internet import task

from adn_server.infrastructure.twisted_adapters.looping import start_loop

_log = logging.getLogger("test-looping")


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> task.Clock:
    clock = task.Clock()

    class ClockedLoop(task.LoopingCall):
        def __init__(self, f, *a, **k) -> None:
            super().__init__(f, *a, **k)
            self.clock = clock

    monkeypatch.setattr(task, "LoopingCall", ClockedLoop)
    return clock


def _failing(times: int) -> tuple[list[int], object]:
    calls: list[int] = []

    def tick() -> None:
        calls.append(1)
        if len(calls) <= times:
            raise RuntimeError("boom")

    return calls, tick


def test_an_error_is_logged_and_the_loop_runs_again(clock: task.Clock, caplog: pytest.LogCaptureFixture) -> None:
    calls, tick = _failing(1)
    with caplog.at_level(logging.ERROR, logger="test-looping"):
        start_loop(tick, 5, _log, name="trimmer")
    assert len(calls) == 1
    assert "timed loop trimmer, starting it again" in caplog.text and "RuntimeError: boom" in caplog.text

    clock.advance(5)
    clock.advance(5)
    assert len(calls) == 3


def test_a_loop_failing_every_time_keeps_its_interval(clock: task.Clock, caplog: pytest.LogCaptureFixture) -> None:
    calls, tick = _failing(100)
    with caplog.at_level(logging.ERROR, logger="test-looping"):
        loop = start_loop(tick, 5, _log, now=False)
        for _ in range(4):
            clock.advance(5)
    assert len(calls) == 4
    assert caplog.text.count("starting it again") == 4
    assert loop.running


def test_a_stopped_loop_stays_stopped(clock: task.Clock) -> None:
    calls, tick = _failing(0)
    loop = start_loop(tick, 5, _log)
    loop.stop()
    clock.advance(20)
    assert len(calls) == 1


def test_a_master_keeps_its_maintenance_loop_after_an_error(clock: task.Clock) -> None:
    """A dead maintenance loop would leave hotspots that never time out."""
    stack = build_hbp_repeat_stack(talker_alias=False)
    calls, tick = _failing(1)
    stack.hbp._master_maintenance_loop = tick  # type: ignore[method-assign]
    stack.hbp.startProtocol()
    ping = stack.config["GLOBAL"].get("PING_TIME", 10)
    clock.advance(ping)
    clock.advance(ping)
    assert len(calls) == 3
