# ADN DMR Peer Server - alias downloads over gzip and If-Modified-Since
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

from __future__ import annotations

import gzip
import hashlib
import os
import threading
from collections.abc import Iterator
from email.message import Message
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from adn_server.infrastructure.persistence.alias_loader import try_download

_PLAIN = b'{"count": 1, "results": [{"id": 7300391, "callsign": "CE5RPY"}]}' * 200


class _Origin:
    """What the local HTTP server answers, and the headers of every request it got."""

    def __init__(self) -> None:
        self.body = _PLAIN
        self.content_encoding: str | None = None
        self.not_modified = False
        self.requests: list[Message] = []


@pytest.fixture
def origin() -> Iterator[tuple[_Origin, str]]:
    state = _Origin()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            state.requests.append(self.headers)
            if state.not_modified and self.headers.get("If-Modified-Since"):
                self.send_response(304)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            if state.content_encoding:
                self.send_header("Content-Encoding", state.content_encoding)
            self.send_header("Content-Length", str(len(state.body)))
            self.end_headers()
            self.wfile.write(state.body)

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_address[1]}/subscriber_ids.json"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _download(tmp_path: Path, url: str, **kwargs: object) -> str:
    kwargs.setdefault("stale_sec", 0)
    return try_download(
        tmp_path, "subscriber_ids.json", url, max_attempts=3, retry_delay_sec=0, **kwargs
    )


def test_request_asks_for_gzip(tmp_path: Path, origin: tuple[_Origin, str]) -> None:
    state, url = origin
    _download(tmp_path, url)
    assert state.requests[0].get("Accept-Encoding") == "gzip"


def test_gzip_response_is_stored_decompressed_and_checked_on_plain_content(
    tmp_path: Path, origin: tuple[_Origin, str]
) -> None:
    state, url = origin
    state.body, state.content_encoding = gzip.compress(_PLAIN), "gzip"

    result = _download(tmp_path, url, expected_checksum=hashlib.blake2b(_PLAIN).hexdigest())

    assert "successfully downloaded" in result
    assert (tmp_path / "subscriber_ids.json").read_bytes() == _PLAIN


def test_uncompressed_response_is_stored_as_is(tmp_path: Path, origin: tuple[_Origin, str]) -> None:
    _, url = origin
    result = _download(tmp_path, url, expected_checksum=hashlib.blake2b(_PLAIN).hexdigest())

    assert "successfully downloaded" in result
    assert (tmp_path / "subscriber_ids.json").read_bytes() == _PLAIN


def test_corrupt_gzip_is_retried_then_fails_without_writing(
    tmp_path: Path, origin: tuple[_Origin, str]
) -> None:
    state, url = origin
    state.body, state.content_encoding = gzip.compress(_PLAIN)[:-40], "gzip"

    result = _download(tmp_path, url)

    assert "could not be downloaded" in result
    assert len(state.requests) == 3
    assert list(tmp_path.iterdir()) == []


def test_not_modified_keeps_file_touches_it_and_reports_current(
    tmp_path: Path, origin: tuple[_Origin, str]
) -> None:
    state, url = origin
    state.not_modified = True
    full = tmp_path / "subscriber_ids.json"
    full.write_bytes(b'{"old": true}')
    old_mtime = 1_700_000_000
    os.utime(full, (old_mtime, old_mtime))

    result = _download(tmp_path, url, stale_sec=86400)

    assert state.requests[0].get("If-Modified-Since") == formatdate(old_mtime, usegmt=True)
    assert len(state.requests) == 1
    assert "is current, not downloaded" in result
    assert full.read_bytes() == b'{"old": true}'
    assert full.stat().st_mtime > old_mtime
