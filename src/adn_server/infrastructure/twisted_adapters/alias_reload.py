# ADN DMR Peer Server - infrastructure twisted adapters alias reload
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

"""Alias reload on one dedicated thread, one reload at a time."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from twisted.internet import threads
from twisted.internet.defer import Deferred
from twisted.python.threadpool import ThreadPool

THREAD_NAME = "alias-reload"


class AliasReloader:
    """Run ``load`` off the reactor and hand its result to ``apply`` on the reactor.

    Always the same thread: glibc gives each thread its own malloc arena, and reloads spread
    over the reactor pool left every arena holding the fragmented peak of one reload. A tick
    that finds a reload still running (a slow download) is skipped, not started beside it.
    """

    def __init__(
        self,
        load: Callable[[], Any],
        apply: Callable[[Any], None],
        reactor: Any,
        logger: logging.Logger,
        interval: float,
    ) -> None:
        self._load = load
        self._apply = apply
        self._reactor = reactor
        self._logger = logger
        self._interval = interval
        self._pool = ThreadPool(minthreads=1, maxthreads=1, name=THREAD_NAME)
        self._pending = False

    def start(self) -> None:
        """Start the thread with the reactor and stop it during shutdown, as its own pool does.

        A tick before the reactor runs is queued and runs when it starts. The stop waits for a
        reload in progress, so it goes in the "during" phase: after the "before" triggers that
        save SUB_MAP and keys.
        """
        self._reactor.callWhenRunning(self._pool.start)
        self._reactor.addSystemEventTrigger("during", "shutdown", self.stop)

    def stop(self) -> None:
        """Stop the thread (waits for a reload in progress)."""
        if self._pool.started:
            self._pool.stop()

    @property
    def pending(self) -> bool:
        """A reload is running."""
        return self._pending

    def tick(self) -> Deferred | None:
        """Start a reload, or skip this tick if the previous one hasn't finished."""
        if self._pending:
            self._logger.warning(
                "(ALIAS) previous alias reload still running after %gs, skipping this tick",
                self._interval,
            )
            return None
        self._pending = True
        self._logger.debug("(ALIAS) starting alias reload on the %s thread", THREAD_NAME)
        d = threads.deferToThreadPool(self._reactor, self._pool, self._load)
        d.addCallback(self._apply)
        d.addErrback(self._failed)
        d.addBoth(self._done)
        return d

    def _failed(self, failure: Any) -> None:
        self._logger.warning("(ALIAS) alias reload failed: %s", failure.getErrorMessage())

    def _done(self, result: Any) -> Any:
        self._pending = False
        return result
