# ADN DMR Peer Server - tests infrastructure panel report client
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

"""POST of the server report and its log (once per failure, never the token)."""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from adn_server.application.report.panel_report import PanelReportSettings
from adn_server.infrastructure.panel.report_client import (
    PanelReportLog,
    send_panel_report,
)


@pytest.fixture
def panel():
    received: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path == "/moved":
                self.send_response(302)
                self.send_header("Location", "/api/ingest/server-report")
                self.end_headers()
                return
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append({"auth": self.headers["Authorization"], "body": json.loads(body)})
            self.send_response(200 if self.headers["Authorization"] == "Bearer adn_ok" else 401)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/api/ingest/server-report", received
    server.shutdown()


def test_report_is_posted_with_the_bearer_token(panel):
    url, received = panel
    status = send_panel_report(PanelReportSettings(url, "adn_ok", 300, None), {"version": "2.6.1"}, "adn-server/test")
    assert status == 200
    assert received == [{"auth": "Bearer adn_ok", "body": {"version": "2.6.1"}}]


def test_wrong_token_returns_the_status(panel):
    url, _ = panel
    assert send_panel_report(PanelReportSettings(url, "adn_bad", 300, None), {}, "adn-server/test") == 401


def test_failures_are_logged_once_and_the_recovery(caplog):
    log = PanelReportLog(logging.getLogger("test"))
    with caplog.at_level(logging.INFO):
        log.sent(401)
        log.sent(401)
        log.error(OSError("timed out"))
        log.sent(200)
    messages = [r.getMessage() for r in caplog.records]
    assert messages == [
        "(ADN_PANEL) Could not send the report: HTTP 401 (check ADN_PANEL.TOKEN in the panel)",
        "(ADN_PANEL) Report sent again (HTTP 200)",
    ]


def test_redirect_is_a_failure_and_is_not_followed(panel):
    url, received = panel
    moved = url.replace("/api/ingest/server-report", "/moved")
    assert send_panel_report(PanelReportSettings(moved, "adn_ok", 300, None), {}, "adn-server/test") == 302
    assert received == []  # the token never reached the redirect target
