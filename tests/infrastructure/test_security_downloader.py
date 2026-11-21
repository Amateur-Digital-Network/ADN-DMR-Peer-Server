# ADN DMR Peer Server - tests infrastructure security downloader
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

"""SecurityDownloader stub, password_crypto round-trip and offline-server resilience."""

from __future__ import annotations

import socket
from urllib.error import URLError

import pytest

from adn_server import __version__
from adn_server.infrastructure.security import password_download
from adn_server.infrastructure.security.password_crypto import decrypt_password
from adn_server.infrastructure.security.password_download import (
    DefaultSecurityDownloader,
    StubSecurityDownloader,
)

pytest.importorskip("cryptography")
from cryptography.fernet import Fernet  # noqa: E402


def test_password_crypto_roundtrip(tmp_path) -> None:
    key_path = tmp_path / "encryption_key.secret"
    key = Fernet.generate_key()
    key_path.write_bytes(key)
    plaintext = "peer-secret-42"
    encrypted = Fernet(key).encrypt(plaintext.encode("utf-8")).decode("utf-8")
    assert decrypt_password(encrypted, str(key_path)) == plaintext


def test_stub_security_downloader_is_noop() -> None:
    stub = StubSecurityDownloader()
    config = {"GLOBAL": {"URL_SECURITY": "", "PORT_SECURITY": "", "PASS_SECURITY": ""}}
    stub.init_downloads(config)
    stub.periodic_download(config)


def test_resolve_hostname_restores_default_socket_timeout() -> None:
    """A failed lookup must not leave its timeout on every other socket in the process."""
    before = socket.getdefaulttimeout()
    assert password_download._resolve_hostname("no-such-host.invalid") is None
    assert socket.getdefaulttimeout() == before


def _offline_config(tmp_path) -> dict:
    return {
        "GLOBAL": {
            "URL_SECURITY": "127.0.0.1",
            "PORT_SECURITY": "1",
            "PASS_SECURITY": "s3cr3t",
            "USERS_PASS": "user_passwords.json",
            "CONFIG_PATH": "config",
        },
        "ALIASES": {"PATH": "data"},
    }


def test_offline_server_keeps_existing_passwords_and_retries_every_call(
    tmp_path, monkeypatch
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pw_file = data_dir / "user_passwords.json"
    pw_file.write_text('{"passwords": {"7300391": "enc"}}', encoding="utf-8")

    attempts = []

    def _refused(*args, **kwargs):
        attempts.append(1)
        raise URLError("Connection refused")

    monkeypatch.setattr(password_download, "urlopen", _refused)
    downloader = DefaultSecurityDownloader(str(tmp_path))
    config = _offline_config(tmp_path)

    assert downloader.periodic_download(config) is False
    assert downloader.periodic_download(config) is False
    # No internal interval gate: every scheduled cycle really attempts the download.
    assert len(attempts) == 2
    assert pw_file.read_text(encoding="utf-8") == '{"passwords": {"7300391": "enc"}}'


def test_pass_security_is_never_logged(tmp_path, monkeypatch, caplog) -> None:
    """LOG_LEVEL DEBUG in production must not write PASS_SECURITY into adn-server.log."""
    (tmp_path / "data").mkdir()
    (tmp_path / "config").mkdir()

    def _refused(*args, **kwargs):
        raise URLError("Connection refused")

    monkeypatch.setattr(password_download, "urlopen", _refused)
    downloader = DefaultSecurityDownloader(str(tmp_path))
    config = _offline_config(tmp_path)

    with caplog.at_level("DEBUG"):
        downloader.init_downloads(config)
        downloader.periodic_download(config)

    assert caplog.text, "expected the downloader to log something at DEBUG"
    assert config["GLOBAL"]["PASS_SECURITY"] not in caplog.text
    assert "pass=" not in caplog.text
    # The host is still there so operators can tell which server failed.
    assert "127.0.0.1" in caplog.text


def test_invalid_payload_does_not_overwrite_existing_file(tmp_path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pw_file = data_dir / "user_passwords.json"
    pw_file.write_text('{"passwords": {"7300391": "enc"}}', encoding="utf-8")

    class _Response:
        def read(self):
            return b"<html>502 Bad Gateway</html>"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(password_download, "urlopen", lambda *a, **k: _Response())
    downloader = DefaultSecurityDownloader(str(tmp_path))

    assert downloader.periodic_download(_offline_config(tmp_path)) is False
    assert pw_file.read_text(encoding="utf-8") == '{"passwords": {"7300391": "enc"}}'


def test_every_download_names_the_server_and_its_version(tmp_path, monkeypatch) -> None:
    requests = []

    def _capture(req, **kwargs):
        requests.append(req)
        raise URLError("offline")

    monkeypatch.setattr(password_download, "urlopen", _capture)
    DefaultSecurityDownloader(str(tmp_path)).init_downloads(_offline_config(tmp_path))

    assert len(requests) == 2
    assert {req.get_header("User-agent") for req in requests} == {f"adn-server/{__version__}"}
