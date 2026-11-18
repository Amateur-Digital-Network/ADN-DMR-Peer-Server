# ADN DMR Peer Server - infrastructure twisted adapters looping
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

"""Periodic tasks that survive an unhandled error."""

from __future__ import annotations

import logging
from typing import Any, Callable

from twisted.internet import task


def start_loop(
    fn: Callable[[], Any],
    interval: float,
    logger: logging.Logger,
    *,
    now: bool = True,
    name: str | None = None,
) -> task.LoopingCall:
    """Run ``fn`` every ``interval`` s; an unhandled error is logged and the loop starts again."""
    loop = task.LoopingCall(fn)
    label = name or getattr(fn, "__qualname__", repr(fn))

    # A LoopingCall stops for good at its first error: a dead stream trimmer or maintenance
    # loop would leave streams and peers that never time out.
    def restart(failure: Any) -> None:
        logger.error("(GLOBAL) Unhandled error in timed loop %s, starting it again:\n%s", label, failure.getTraceback())
        loop.start(interval, now=False).addErrback(restart)

    loop.start(interval, now=now).addErrback(restart)
    return loop
