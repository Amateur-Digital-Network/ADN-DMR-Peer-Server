# ADN DMR Peer Server - alias reload on one dedicated thread

from __future__ import annotations

import logging
import threading
from typing import Any
from unittest.mock import patch

import pytest

from adn_server.infrastructure.twisted_adapters.alias_reload import (
    THREAD_NAME,
    AliasReloader,
)


class _Reactor:
    """Just what deferToThreadPool and start() use; results come back on the pool thread."""

    def __init__(self) -> None:
        self.triggers: list[tuple[str, str, Any]] = []

    def callFromThread(self, fn, *args, **kwargs) -> None:
        fn(*args, **kwargs)

    def callWhenRunning(self, fn, *args, **kwargs) -> None:
        fn(*args, **kwargs)

    def addSystemEventTrigger(self, phase, event, fn) -> None:
        self.triggers.append((phase, event, fn))

    def shutdown(self) -> None:
        # Twisted runs every "before" trigger, then every "during" one.
        for wanted in ("before", "during"):
            for phase, event, fn in self.triggers:
                if (phase, event) == (wanted, "shutdown"):
                    fn()


def _wait(d) -> Any:
    done = threading.Event()
    out: list[Any] = []
    d.addBoth(lambda r: (out.append(r), done.set()))
    assert done.wait(5), "reload did not finish"
    return out[0]


_made: list[AliasReloader] = []


@pytest.fixture
def no_reactor_pool():
    # Going back to the reactor pool must fail here, not just pass on another thread.
    with patch("twisted.internet.threads.deferToThread", side_effect=AssertionError("reactor pool")):
        yield
    # A test that fails must not leave a non-daemon thread holding pytest open.
    while _made:
        pool = _made.pop()._pool
        if pool.started:
            pool.stop()


def _reloader(load, apply=lambda r: None):
    reactor = _Reactor()
    reloader = AliasReloader(load, apply, reactor, logging.getLogger("test"), 900)
    reloader.start()
    _made.append(reloader)
    return reloader, reactor


def test_every_reload_runs_on_the_same_dedicated_thread(no_reactor_pool) -> None:
    seen: list[tuple[int, str]] = []
    applied: list[int] = []

    def load():
        seen.append((threading.get_ident(), threading.current_thread().name))
        return len(seen)

    reloader, reactor = _reloader(load, applied.append)
    try:
        for _ in range(5):
            _wait(reloader.tick())
    finally:
        reactor.shutdown()
    assert len({ident for ident, _ in seen}) == 1
    assert THREAD_NAME in seen[0][1]
    assert threading.get_ident() not in {ident for ident, _ in seen}
    assert applied == [1, 2, 3, 4, 5]


def test_a_tick_while_a_reload_runs_is_skipped(no_reactor_pool, caplog) -> None:
    release = threading.Event()
    calls: list[int] = []

    def load():
        calls.append(1)
        release.wait(5)
        return "ok"

    reloader, reactor = _reloader(load)
    try:
        first = reloader.tick()
        with caplog.at_level(logging.WARNING):
            assert reloader.tick() is None
        assert "skipping this tick" in caplog.text
        release.set()
        _wait(first)
        assert not reloader.pending
        _wait(reloader.tick())
    finally:
        reactor.shutdown()
    assert len(calls) == 2


def test_a_failed_reload_frees_the_next_tick(no_reactor_pool, caplog) -> None:
    attempts: list[int] = []

    def load():
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("selfcare down")
        return "ok"

    applied: list[str] = []
    reloader, reactor = _reloader(load, applied.append)
    try:
        with caplog.at_level(logging.WARNING):
            _wait(reloader.tick())
        assert "alias reload failed: selfcare down" in caplog.text
        _wait(reloader.tick())
    finally:
        reactor.shutdown()
    assert applied == ["ok"]


def test_shutdown_stops_the_thread(no_reactor_pool) -> None:
    def alive() -> bool:
        return any(THREAD_NAME in t.name for t in threading.enumerate())

    reloader, reactor = _reloader(lambda: None)
    try:
        _wait(reloader.tick())
        assert alive()
    finally:
        reactor.shutdown()
    assert not alive()
    reloader.stop()  # a second stop is harmless


def test_no_thread_until_the_reactor_runs() -> None:
    # Bootstrapping without a running reactor (tests, --check) must not leave a thread behind.
    class _NotRunning(_Reactor):
        def callWhenRunning(self, fn, *args, **kwargs) -> None:
            self.pending_start = fn

    reactor = _NotRunning()
    reloader = AliasReloader(lambda: "ok", lambda r: None, reactor, logging.getLogger("test"), 900)
    reloader.start()
    d = reloader.tick()
    assert not any(THREAD_NAME in t.name for t in threading.enumerate())
    reactor.pending_start()
    _made.append(reloader)
    try:
        assert _wait(d) is None
    finally:
        reactor.shutdown()


def test_the_thread_stops_after_the_before_shutdown_triggers() -> None:
    # The stop waits for a reload in progress: it must not hold up the "before" triggers
    # that save SUB_MAP and keys (the reactor's own pool also stops "during").
    reactor = _Reactor()
    reloader = AliasReloader(lambda: None, lambda r: None, reactor, logging.getLogger("test"), 900)
    reloader.start()
    _made.append(reloader)
    try:
        assert [(phase, event) for phase, event, _ in reactor.triggers] == [("during", "shutdown")]
    finally:
        reactor.shutdown()
