# ADN DMR Peer Server - HTTP client for the ADN panel report
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

"""POST of the server report. Blocking: run it off the reactor (deferToThread)."""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from adn_server.application.report.panel_report import PanelReportSettings

TIMEOUT_S = 10


def send_panel_report(settings: PanelReportSettings, payload: dict[str, Any], user_agent: str) -> int:
    """Send the report; returns the HTTP status. Raises on network errors."""
    request = Request(
        settings.url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {settings.token}",
            "Content-Type": "application/json",
            "User-Agent": user_agent,
        },
    )
    try:
        with urlopen(request, timeout=TIMEOUT_S) as response:
            return response.status
    except HTTPError as exc:
        return exc.code


class PanelReportLog:
    """Logs a failure once and the recovery, not every 5 minutes. Never logs the token."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        self._failing = False

    def sent(self, status: int) -> None:
        if 200 <= status < 300:
            if self._failing:
                self._logger.info("(ADN_PANEL) Report sent again (HTTP %s)", status)
            else:
                self._logger.debug("(ADN_PANEL) Report sent (HTTP %s)", status)
            self._failing = False
            return
        hint = " (check ADN_PANEL.TOKEN in the panel)" if status in (401, 403) else ""
        self._fail(f"HTTP {status}{hint}")

    def error(self, exc: BaseException) -> None:
        reason = exc.reason if isinstance(exc, URLError) else exc
        self._fail(str(reason))

    def _fail(self, reason: str) -> None:
        if not self._failing:
            self._logger.warning("(ADN_PANEL) Could not send the report: %s", reason)
        self._failing = True
