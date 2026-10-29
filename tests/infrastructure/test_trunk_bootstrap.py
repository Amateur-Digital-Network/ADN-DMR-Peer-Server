# ADN DMR Peer Server - tests infrastructure trunk bootstrap
#
# Copyright (C) 2026  Rodrigo Pérez, CE5RPY <ce5rpy@qmd.cl>
# Copyright (C) 2026  Joaquin Madrid Belando, EA5GVK <ea5gvk@gmail.com>
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

"""Bootstrap builds the Tier III services only for a TRUNKING system, and only once."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from twisted.internet.defer import Deferred, maybeDeferred

import adn_server
from adn_server.application.routing_use_cases import RoutingUseCases
from adn_server.application.trunking import sites as sites_mod
from adn_server.application.trunking.sites import TICK_S
from adn_server.domain import bytes_4
from adn_server.infrastructure.bootstrap import trunking as bootstrap_trunking
from adn_server.infrastructure.bootstrap.trunking import build_trunk_sites
from adn_server.infrastructure.twisted_adapters import udp_hbp_trunk
from adn_server.infrastructure.twisted_adapters.udp_hbp_trunk import TrunkSiteProtocol
from adn_server.main import main
from tests.harness.deterministic import PEER_MAX, acl_permit_all
from tests.support.dmrtc_wire import GatewayLink, dmrt_registration
from tests.support.hbp_repeat_stack import RecordingTransport


_started_loops: list[tuple[str, float]] = []
_stopped_loops: list[str] = []


def _start_loop(f, every: float, *_a, **_k) -> SimpleNamespace:
    _started_loops.append((f.__name__, every))
    return SimpleNamespace(stop=lambda: _stopped_loops.append(f.__name__))


class _Quiet:
    def __getattr__(self, _name):
        return lambda *_a, **_k: None


class _FakeAliasLoader:
    def load_aliases(self, *_a, **_k):
        return ({}, {}, {}, {}, {}, {})

    def load_subscriber_profiles(self, *_a, **_k):
        return {}


def _boot(monkeypatch: pytest.MonkeyPatch, tmp_path, systems: dict[str, Any], build) -> SimpleNamespace:
    from twisted.internet import reactor

    import adn_server.infrastructure.bootstrap.peer_server as peer_mod
    import adn_server.main as main_mod

    booted = SimpleNamespace(routing=[], sites=[], triggers=set(), handlers={})
    _started_loops.clear()
    _stopped_loops.clear()

    class _Routing(RoutingUseCases):
        def __init__(self, *a, **k) -> None:
            super().__init__(*a, **k)
            booted.routing.append(self)

    class _Sites(TrunkSiteProtocol):
        def __init__(self, *a, **k) -> None:
            super().__init__(*a, **k)
            booted.sites.append(self)

    monkeypatch.setattr(main_mod, "run_echo", lambda *_a, **_k: None)
    monkeypatch.setattr(reactor, "run", lambda: None)
    monkeypatch.setattr(reactor, "addSystemEventTrigger", lambda _p, _e, f, *_a, **_k: booted.triggers.add(f.__name__))
    port = SimpleNamespace(socket=None, stopListening=lambda: None)
    monkeypatch.setattr(reactor, "listenUDP", lambda *_a, **_k: port)
    monkeypatch.setattr(peer_mod, "apply_udp_rcvbuf", lambda *_a, **_k: None)
    monkeypatch.setattr(peer_mod, "start_loop", _start_loop)
    # The reactor never runs here, so work for its thread pool runs at once.
    monkeypatch.setattr(peer_mod, "threads", SimpleNamespace(deferToThread=maybeDeferred))
    # Windows has no SIGUSR2 or SIGHUP, which bootstrap installs handlers for.
    signals = SimpleNamespace(SIGTERM=15, SIGINT=2, SIGUSR2=12, SIGHUP=1, signal=booted.handlers.__setitem__)
    monkeypatch.setattr(peer_mod, "signal", signals)
    monkeypatch.setattr(peer_mod, "RoutingUseCases", _Routing)
    monkeypatch.setattr(udp_hbp_trunk, "TrunkSiteProtocol", _Sites)
    monkeypatch.setattr(bootstrap_trunking, "build_trunk_sites", build)
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

    with patch("adn_server.main.YamlConfigLoader") as mock_loader_cls:
        loader = mock_loader_cls.return_value
        loader.load.return_value = _config(tmp_path, systems)
        loader.load_voice_config.return_value = None
        with patch("adn_server.main.setup_logging", return_value=_Quiet()):
            with patch.object(sys, "argv", ["adn-server", "--no-proxy", "-c", "/tmp/adn-server.yaml"]):
                main()
    booted.loader = loader
    return booted


def _config(tmp_path, systems: dict[str, Any]) -> dict[str, Any]:
    return {
        "GLOBAL": {"SERVER_ID": 1},
        "LOGGER": {},
        "ALIASES": {"PATH": str(tmp_path), "SUB_MAP_FILE": ""},
        "REPORTS": {"REPORT": False},
        "DATABASE": {
            "DB_SERVER": "localhost",
            "DB_USERNAME": "hbmon",
            "DB_PASSWORD": "x",
            "DB_NAME": "hbmon",
            "DB_PORT": 3306,
        },
        "SYSTEMS": systems,
    }


def _sighup(monkeypatch: pytest.MonkeyPatch, booted: SimpleNamespace, tmp_path, systems: dict[str, Any]) -> None:
    from twisted.internet import reactor

    monkeypatch.setattr(reactor, "callLater", lambda _delay, f, *a, **k: f(*a, **k))
    booted.loader.load.return_value = _config(tmp_path, systems)
    booted.handlers[1](1, None)


def _master(**extra: Any) -> dict[str, Any]:
    return {"MODE": "MASTER", "ENABLED": True, "MAX_PEERS": 4, "PORT": 62040, "PASSPHRASE": "x", **extra}


def _tier3_modules() -> list[str]:
    """Every Tier III module but the TRUNKING flag, which config validation and reload read."""
    return sorted(
        name for name in sys.modules
        if name.startswith("adn_server.") and ("trunk" in name or name.endswith(".site_tg_ops"))
        and name not in ("adn_server.application.trunking", "adn_server.application.trunking.config")
    )


def test_importing_the_server_loads_no_tier3_module() -> None:
    src = Path(adn_server.__file__).parents[1]
    code = "import sys, adn_server.main; print(sorted(m for m in sys.modules if 'trunk' in m or 'site_tg' in m))"
    env = {**os.environ, "PYTHONPATH": str(src)}
    run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=src, env=env)

    assert run.stdout.strip() == str(["adn_server.application.trunking", "adn_server.application.trunking.config"])


def test_without_trunking_nothing_tier3_is_built(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    def build(*_a, **_k):
        raise AssertionError("Tier III services built without a TRUNKING system")

    for name in _tier3_modules():
        monkeypatch.delitem(sys.modules, name)
        package, _, module = name.rpartition(".")
        monkeypatch.delattr(sys.modules[package], module)

    booted = _boot(monkeypatch, tmp_path, {"HOTSPOT": _master()}, build)

    assert _tier3_modules() == []
    assert booted.sites == []
    assert booted.routing[0]._concurrent_systems == frozenset()
    assert not {name for name, _ in _started_loops} & {"_trunk_tick", "_trunk_persist"}
    assert "_trunk_save" not in booted.triggers


def test_a_registrations_file_that_cannot_be_written_does_not_stop_a_reload(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    (tmp_path / "trunk_registrations.json").mkdir()
    systems = {"HOTSPOT": _master(), "TRUNK": _master(TRUNKING=True)}
    booted = _boot(monkeypatch, tmp_path, systems, build_trunk_sites)

    _sighup(monkeypatch, booted, tmp_path, {**systems, "TRUNK-2": _master(TRUNKING=True, PORT=62041)})

    assert booted.routing[0]._concurrent_systems == frozenset({"TRUNK", "TRUNK-2"})


@pytest.mark.parametrize(
    ("systems", "concurrent"),
    [
        ({"TRUNK": _master()}, frozenset()),
        ({"TRUNK": _master(TRUNKING=True), "TRUNK-2": _master(TRUNKING=True, PORT=62041)}, {"TRUNK", "TRUNK-2"}),
    ],
    ids=["trunking-off", "trunk-added"],
)
def test_a_reload_that_fails_after_creating_protocols_marks_the_systems_they_serve(
    monkeypatch: pytest.MonkeyPatch, tmp_path, systems: dict[str, Any], concurrent: set[str]
) -> None:
    """A port that cannot be bound fails the reload once its protocols exist: routing follows those protocols."""
    from twisted.internet import reactor
    from twisted.internet.error import CannotListenError

    booted = _boot(
        monkeypatch, tmp_path, {"HOTSPOT": _master(), "TRUNK": _master(TRUNKING=True)}, build_trunk_sites,
    )

    def listen(port: int, *_a, **_k) -> SimpleNamespace:
        if port == 62099:
            raise CannotListenError("", port, OSError("address in use"))
        return SimpleNamespace(socket=None, stopListening=lambda: None)

    monkeypatch.setattr(reactor, "listenUDP", listen)
    _sighup(monkeypatch, booted, tmp_path, {"HOTSPOT": _master(), **systems, "HOTSPOT-2": _master(PORT=62099)})

    protocols = booted.routing[0]._get_protocols()
    assert {name for name, proto in protocols.items() if isinstance(proto, TrunkSiteProtocol)} == concurrent
    assert booted.routing[0]._concurrent_systems == concurrent


def test_a_site_on_a_trunk_system_a_failed_reload_left_listening_has_its_dmrt_dropped(
    monkeypatch: pytest.MonkeyPatch, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    """A bind failing after TRUNK-2's leaves TRUNK-2 listening, though the live config never got it."""
    from twisted.internet import reactor
    from twisted.internet.error import CannotListenError

    booted = _boot(
        monkeypatch, tmp_path, {"HOTSPOT": _master(), "TRUNK": _master(TRUNKING=True)}, build_trunk_sites,
    )

    def listen(port: int, *_a, **_k) -> SimpleNamespace:
        if port == 62099:
            raise CannotListenError("", port, OSError("address in use"))
        return SimpleNamespace(socket=None, stopListening=lambda: None)

    monkeypatch.setattr(reactor, "listenUDP", listen)
    _sighup(monkeypatch, booted, tmp_path, {
        "HOTSPOT": _master(), "TRUNK": _master(TRUNKING=True),
        "TRUNK-2": _master(TRUNKING=True, PORT=62041), "ZHOT": _master(PORT=62099),
    })
    proto = booted.routing[0]._get_protocols()["TRUNK-2"]
    assert "TRUNK-2" not in booted.routing[0]._config["SYSTEMS"]
    proto._CONFIG["GLOBAL"]["REG_ACL"] = proto._config["REG_ACL"] = acl_permit_all(PEER_MAX)
    proto.transport = RecordingTransport()
    addr = ("10.0.2.1", 40001)
    gateway = GatewayLink(bytes_4(214000201), b"x")
    datagram: bytes | None = gateway.login()
    while datagram is not None:
        sent = len(proto.transport.sent)
        proto.datagramReceived(datagram, addr)
        datagram = None
        for reply, _ in proto.transport.sent[sent:]:
            datagram = gateway.received(reply) or datagram
    assert gateway.status == "RUNNING"
    sent = len(proto.transport.sent)

    with caplog.at_level(logging.DEBUG, logger=sites_mod.__name__):
        proto.datagramReceived(dmrt_registration(0x01, (2149050,), 0), addr)

    assert proto.transport.sent[sent:] == []
    assert "(TRUNK-2) DMRT from Tier III site 214000201 ignored" in caplog.text


def test_the_tier3_loops_run_only_while_a_trunking_system_does(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """The last TRUNKING system going stops the tick and the periodic write, after a last write; the next starts them."""
    path = tmp_path / "trunk_registrations.json"
    path.write_text(json.dumps([[2141234, "TRUNK", 214000101, time.time() - 60]]))
    trunk = {"HOTSPOT": _master(), "TRUNK": _master(TRUNKING=True)}
    loops = ["_trunk_tick", "_trunk_persist"]
    booted = _boot(monkeypatch, tmp_path, trunk, build_trunk_sites)

    _sighup(monkeypatch, booted, tmp_path, trunk)
    assert _stopped_loops == []

    _sighup(monkeypatch, booted, tmp_path, {"HOTSPOT": _master()})
    assert sorted(_stopped_loops) == sorted(loops)
    assert json.loads(path.read_text()) == []
    path.unlink()
    _sighup(monkeypatch, booted, tmp_path, {"HOTSPOT": _master()})
    assert not path.exists()

    _sighup(monkeypatch, booted, tmp_path, trunk)
    assert [name for name, _ in _started_loops if name in loops] == loops + loops
    assert sorted(_stopped_loops) == sorted(loops)


def test_a_trunking_system_a_reload_adds_first_reads_the_file_as_an_earlier_run_left_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """With no Tier III services nothing was registered since the file was written: the system takes back its own
    registrations still within the TTL, as a restart would, and the next write holds only those."""
    path = tmp_path / "trunk_registrations.json"
    now = time.time()
    path.write_text(json.dumps([
        [2141234, "TRUNK", 214000101, now - 600],
        [2141235, "TRUNK", 214000101, now - 90000],
        [2141236, "TRUNK-2", 214000201, now - 600],
    ]))
    built: list[Any] = []

    def build(*args, **kwargs):
        built.append(build_trunk_sites(*args, **kwargs))
        return built[-1]

    booted = _boot(monkeypatch, tmp_path, {"HOTSPOT": _master()}, build)
    _sighup(monkeypatch, booted, tmp_path, {"HOTSPOT": _master(), "TRUNK": _master(TRUNKING=True)})
    built[0].save()

    assert [entry[:3] for entry in json.loads(path.read_text())] == [[2141234, "TRUNK", 214000101]]


def test_a_trunking_system_builds_the_site_services_once(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    built: list[str] = []

    def build(*args, **kwargs):
        built.append(args[6])
        return build_trunk_sites(*args, **kwargs)

    systems = {"TRUNK": _master(TRUNKING=True), "TRUNK-2": _master(TRUNKING=True, PORT=62041), "HOTSPOT": _master()}
    booted = _boot(monkeypatch, tmp_path, systems, build)

    assert built == [str(tmp_path / "trunk_registrations.json")]
    assert sorted(proto._system for proto in booted.sites) == ["TRUNK", "TRUNK-2"]
    assert booted.sites[0]._sites is booted.sites[1]._sites
    assert ("_trunk_tick", TICK_S) in _started_loops and ("_trunk_persist", 300) in _started_loops
    assert "_trunk_save" in booted.triggers
    assert booted.routing[0]._concurrent_systems == frozenset({"TRUNK", "TRUNK-2"})


def test_the_shutdown_write_holds_the_reactor_until_it_is_done(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """The write runs in a worker thread; the shutdown trigger gives the reactor that write to wait for."""
    from twisted.internet import reactor

    import adn_server.infrastructure.bootstrap.peer_server as peer_mod

    path = tmp_path / "trunk_registrations.json"
    path.write_text(json.dumps([[2141234, "TRUNK", 214000101, time.time() - 60]]))
    triggers: list[Any] = []
    jobs: list[tuple[Any, tuple[Any, ...], Deferred]] = []

    def worker(f, *args) -> Deferred:
        jobs.append((f, args, Deferred()))
        return jobs[-1][2]

    booted = _boot(monkeypatch, tmp_path, {"HOTSPOT": _master()}, build_trunk_sites)
    monkeypatch.setattr(reactor, "addSystemEventTrigger", lambda _p, _e, f, *_a, **_k: triggers.append(f))
    monkeypatch.setattr(peer_mod, "threads", SimpleNamespace(deferToThread=worker))
    _sighup(monkeypatch, booted, tmp_path, {"HOTSPOT": _master(), "TRUNK": _master(TRUNKING=True)})
    path.unlink()

    [save] = [f for f in triggers if f.__name__ == "_trunk_save"]
    stopping = save()

    assert not stopping.called and not path.exists()
    f, args, done = jobs.pop()
    done.callback(f(*args))
    assert stopping.called and [entry[:3] for entry in json.loads(path.read_text())] == [[2141234, "TRUNK", 214000101]]


def test_a_reload_that_turns_trunking_on_keeps_the_saved_registrations(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A reload creates the site protocols before its config goes live: each reads back its own registrations."""
    path = tmp_path / "trunk_registrations.json"
    saved = time.time() - 43200
    path.write_text(json.dumps([[2141234, "TRUNK", 214000101, saved], [2141235, "TRUNK-2", 214000201, saved]]))
    built: list[Any] = []

    def build(*args, **kwargs):
        built.append(build_trunk_sites(*args, **kwargs))
        return built[-1]

    disabled = {"HOTSPOT": _master(), "TRUNK": _master(TRUNKING=True, ENABLED=False)}
    booted = _boot(monkeypatch, tmp_path, disabled, build)
    assert built == []

    enabled = {"HOTSPOT": _master(), "TRUNK": _master(TRUNKING=True), "TRUNK-2": _master(TRUNKING=True, PORT=62041)}
    _sighup(monkeypatch, booted, tmp_path, enabled)
    built[0].save()

    assert [entry[:3] for entry in json.loads(path.read_text())] == [
        [2141234, "TRUNK", 214000101],
        [2141235, "TRUNK-2", 214000201],
    ]


def _with_saved_registration(tmp_path, build_list: list[Any]):
    (tmp_path / "trunk_registrations.json").write_text(json.dumps([[2141234, "TRUNK", 214000101, time.time() - 60]]))

    def build(*args, **kwargs):
        build_list.append(build_trunk_sites(*args, **kwargs))
        return build_list[-1]

    return build


def _saved(tmp_path) -> list[list[Any]]:
    return [entry[:3] for entry in json.loads((tmp_path / "trunk_registrations.json").read_text())]


def test_a_reload_sets_the_concurrent_systems_again(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    build = _with_saved_registration(tmp_path, [])
    booted = _boot(monkeypatch, tmp_path, {"HOTSPOT": _master(), "TRUNK": _master(TRUNKING=True)}, build)
    assert booted.routing[0]._concurrent_systems == frozenset({"TRUNK"})

    systems = {"HOTSPOT": _master(), "TRUNK": _master(TRUNKING=True), "TRUNK-2": _master(TRUNKING=True, PORT=62041)}
    _sighup(monkeypatch, booted, tmp_path, systems)

    assert booted.routing[0]._concurrent_systems == frozenset({"TRUNK", "TRUNK-2"})


def test_a_reload_that_removes_a_trunking_system_drops_its_registrations(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    built: list[Any] = []
    build = _with_saved_registration(tmp_path, built)
    booted = _boot(monkeypatch, tmp_path, {"HOTSPOT": _master(), "TRUNK": _master(TRUNKING=True)}, build)

    _sighup(monkeypatch, booted, tmp_path, {"HOTSPOT": _master()})
    built[0].save()

    assert _saved(tmp_path) == []


def test_a_reload_writes_the_registrations_first(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    systems = {"HOTSPOT": _master(), "TRUNK": _master(TRUNKING=True)}
    booted = _boot(monkeypatch, tmp_path, systems, _with_saved_registration(tmp_path, []))
    (tmp_path / "trunk_registrations.json").unlink()

    _sighup(monkeypatch, booted, tmp_path, systems)

    assert _saved(tmp_path) == [[2141234, "TRUNK", 214000101]]
