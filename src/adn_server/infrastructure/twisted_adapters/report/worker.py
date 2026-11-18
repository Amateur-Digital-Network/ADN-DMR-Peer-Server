# ADN DMR Peer Server - infrastructure twisted adapters report worker
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

"""Twisted LoopingCall worker that drains the bounded report queue."""

from __future__ import annotations

import logging

from twisted.internet import task

from adn_server.application.ports import ReportSender
from adn_server.application.report.queue import BoundedReportQueue
from adn_server.infrastructure.twisted_adapters.looping import start_loop

logger = logging.getLogger(__name__)

DEFAULT_DRAIN_INTERVAL_SEC = 0.05


def start_report_queue_worker(
    queue: BoundedReportQueue,
    sender: ReportSender,
    *,
    interval_sec: float = DEFAULT_DRAIN_INTERVAL_SEC,
) -> task.LoopingCall:
    """Start periodic drain of ``queue`` into ``sender`` (synchronous TCP send on reactor)."""

    def _drain() -> None:
        try:
            queue.drain(sender)
        except Exception as e:
            logger.warning("(REPORT) queue drain failed: %s", e)

    loop = start_loop(_drain, interval_sec, logger, now=False)
    logger.info(
        "(REPORT) queue worker started interval=%.3fs max_events=%s drain_per_tick=%s",
        interval_sec,
        queue.max_events,
        queue.max_drain_per_tick,
    )
    return loop
