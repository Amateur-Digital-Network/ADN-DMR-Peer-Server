# ADN DMR Peer Server - tests infrastructure SUB_MAP_FILE default
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

"""An empty SUB_MAP_FILE writes SUB_MAP to the same default file it is loaded from."""

from __future__ import annotations

import os
import sys
import time
from unittest.mock import patch

import pytest

from adn_server.main import main


class _RecordingSubMapStore:
    saved_paths: list[str] = []

    def load(self, *_a, **_k):
        return {}

    def save(self, path, _sub_map):
        self.saved_paths.append(path)


_started_loops: dict[str, object] = {}


class _Quiet:
    def __getattr__(self, _name):
        return lambda *_a, **_k: None


class _FakeAliasLoader:
    def load_aliases(self, *_a, **_k):
        return ({}, {}, {}, {}, {}, {})

    def load_subscriber_profiles(self, *_a, **_k):
        return {}


def _boot(monkeypatch: pytest.MonkeyPatch) -> tuple[dict, dict]:
    from twisted.internet import reactor

    import adn_server.infrastructure.bootstrap.peer_server as peer_mod
    import adn_server.main as main_mod

    _RecordingSubMapStore.saved_paths = []
    _started_loops.clear()
    triggers: dict = {}

    monkeypatch.setattr(main_mod, "run_echo", lambda *_a, **_k: None)
    monkeypatch.setattr(reactor, "run", lambda: None)
    monkeypatch.setattr(
        reactor, "addSystemEventTrigger", lambda _phase, _event, f, *_a, **_k: triggers.setdefault(f.__name__, f)
    )
    monkeypatch.setattr(peer_mod, "start_loop", lambda f, *_a, **_k: _started_loops.setdefault(f.__name__, f))
    monkeypatch.setattr(peer_mod.signal, "signal", lambda *_a, **_k: None)
    monkeypatch.setattr(peer_mod, "PickleSubMapStore", _RecordingSubMapStore)
    monkeypatch.setattr(peer_mod, "ReportServerFactory", lambda *_a, **_k: _Quiet())
    monkeypatch.setattr(peer_mod, "create_report_mqtt_publisher", lambda *_a, **_k: None)
    monkeypatch.setattr(peer_mod, "start_report_queue_worker", lambda *_a, **_k: None)
    monkeypatch.setattr(peer_mod, "DefaultAliasLoader", _FakeAliasLoader)
    monkeypatch.setattr(peer_mod, "JsonKeysStore", lambda *_a, **_k: _Quiet())
    monkeypatch.setattr(peer_mod, "DefaultSecurityDownloader", lambda *_a, **_k: _Quiet())
    monkeypatch.setattr(peer_mod, "UserPasswordsLoader", lambda *_a, **_k: _Quiet())
    monkeypatch.setattr(peer_mod, "DefaultVoiceProvider", lambda *_a, **_k: _Quiet())
    monkeypatch.setattr(peer_mod, "RecordingHandler", lambda *_a, **_k: _Quiet())
    monkeypatch.setattr(peer_mod, "IdentUseCases", lambda *_a, **_k: _Quiet())
    monkeypatch.setattr(peer_mod, "HBPProtocolFactory", lambda *_a, **_k: object())
    monkeypatch.setattr(peer_mod, "ensure_database_sync", lambda *_a, **_k: True)
    monkeypatch.setattr(peer_mod, "create_mysql_pool", lambda *_a, **_k: object())
    monkeypatch.setattr(peer_mod, "MysqlDynamicTgRepository", lambda *_a, **_k: object())

    config = {
        "GLOBAL": {"SERVER_ID": 1},
        "LOGGER": {},
        "ALIASES": {"PATH": ".", "SUB_MAP_FILE": ""},
        "REPORTS": {"REPORT": False},
        "DATABASE": {
            "DB_SERVER": "localhost",
            "DB_USERNAME": "hbmon",
            "DB_PASSWORD": "x",
            "DB_NAME": "hbmon",
            "DB_PORT": 3306,
        },
        "SYSTEMS": {},
    }
    with patch("adn_server.main.YamlConfigLoader") as mock_loader_cls:
        loader = mock_loader_cls.return_value
        loader.load.return_value = config
        loader.load_voice_config.return_value = None
        with patch("adn_server.main.setup_logging", return_value=_Quiet()):
            with patch.object(sys, "argv", ["adn-server", "--no-proxy", "-c", "/tmp/adn-server.yaml"]):
                main()
    return config, triggers


def test_empty_sub_map_file_saves_on_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    _, triggers = _boot(monkeypatch)
    triggers["shutdown_handler"]()
    assert [os.path.basename(p) for p in _RecordingSubMapStore.saved_paths] == ["sub_map.pkl"]


def test_empty_sub_map_file_saves_changed_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    config, _ = _boot(monkeypatch)
    config["_SUB_MAP"][3120001] = ("SYSTEM", 1, time.time())
    _started_loops["sub_map_trimmer_loop"]()
    assert [os.path.basename(p) for p in _RecordingSubMapStore.saved_paths] == ["sub_map.pkl"]
