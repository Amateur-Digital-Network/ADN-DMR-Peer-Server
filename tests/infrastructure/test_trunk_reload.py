# ADN DMR Peer Server - tests infrastructure trunk reload
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

"""SIGHUP reload: only a TRUNKING flip recreates a system's protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from twisted.internet import defer, reactor

from adn_server.infrastructure.config_loader import YamlConfigLoader
from adn_server.infrastructure.config_reload import ReloadResult, reload_server_config

_SITE = {b"\x0c\xc1\x7a\x01": {"CONNECTION": "YES"}}


class _FakePort:
    def __init__(self) -> None:
        self.stop_calls = 0

    def stopListening(self) -> defer.Deferred:
        self.stop_calls += 1
        return defer.succeed(None)


class _FakeProto:
    def __init__(self, name: str) -> None:
        self.name = name
        self.dereg_called = False
        self.applied = 0

    def dereg(self) -> None:
        self.dereg_called = True

    def apply_system_config(self, _config: dict[str, Any]) -> None:
        self.applied += 1


@dataclass
class _Reload:
    config: dict[str, Any]
    protocols: dict[str, Any]
    transports: dict[str, Any]
    old_protos: dict[str, _FakeProto]
    old_ports: dict[str, _FakePort]
    listened: list[tuple[str, int, Any]] = field(default_factory=list)
    removed: list[tuple[str, Any]] = field(default_factory=list)
    results: list[ReloadResult] = field(default_factory=list)


def _master(port: int, **overrides: Any) -> dict[str, Any]:
    return {"MODE": "MASTER", "ENABLED": True, "IP": "", "PORT": port, "PASSPHRASE": "s", "MAX_PEERS": 4, **overrides}


def _hotspot() -> dict[str, Any]:
    return _master(62031, PEERS={})


def _reload(old_trunk: dict[str, Any], new_trunk: dict[str, Any]) -> _Reload:
    return _reload_systems({"TRUNK": old_trunk, "HOTSPOT": _hotspot()}, {"TRUNK": new_trunk, "HOTSPOT": _hotspot()})


def _reload_systems(old_systems: dict[str, Any], new_systems: dict[str, Any]) -> _Reload:
    old_protos = {name: _FakeProto(name) for name in old_systems}
    old_ports = {name: _FakePort() for name in old_systems}
    run = _Reload(
        config={"GLOBAL": {}, "SYSTEMS": dict(old_systems)},
        protocols=dict(old_protos),
        transports=dict(old_ports),
        old_protos=old_protos,
        old_ports=old_ports,
    )

    class _Loader(YamlConfigLoader):
        def load(self, _path: str) -> dict[str, Any]:
            return {"GLOBAL": {}, "SYSTEMS": new_systems}

    def _listen(name: str, bind: Any, proto: Any) -> _FakePort:
        run.listened.append((name, bind.port, proto))
        return _FakePort()

    reload_server_config(
        run.config,
        "adn-server.yaml",
        _Loader(),
        run.protocols,
        run.transports,
        create_protocol=lambda name, _cfg: _FakeProto(name),
        listen_udp=_listen,
        stop_listener=lambda port: port.stopListening(),
        on_system_removed=lambda name, proto: run.removed.append((name, proto)),
    ).addCallback(run.results.append)
    reactor.runUntilCurrent()
    assert len(run.results) == 1
    return run


def _assert_hotspot_untouched(run: _Reload) -> None:
    assert run.protocols["HOTSPOT"] is run.old_protos["HOTSPOT"]
    assert not run.old_protos["HOTSPOT"].dereg_called
    assert run.old_ports["HOTSPOT"].stop_calls == 0
    assert run.transports["HOTSPOT"] is run.old_ports["HOTSPOT"]


@pytest.mark.parametrize(("before", "after"), [(False, True), (True, False)], ids=["on", "off"])
def test_a_trunking_flip_recreates_the_protocol(before: bool, after: bool) -> None:
    """Peers logged in under the other protocol: the system is removed and added again, with no peers,
    a new protocol on a restarted listener, and a bridge reset for the legs of its previous role."""
    run = _reload(_master(62040, TRUNKING=before, PEERS=dict(_SITE)), _master(62040, TRUNKING=after))

    old = run.old_protos["TRUNK"]
    new = run.protocols["TRUNK"]
    assert new is not old and old.dereg_called
    assert run.removed == [("TRUNK", old)]
    assert run.old_ports["TRUNK"].stop_calls == 1
    assert run.listened == [("TRUNK", 62040, new)]
    assert run.transports["TRUNK"] is not run.old_ports["TRUNK"]
    sys_cfg = run.config["SYSTEMS"]["TRUNK"]
    assert sys_cfg["TRUNKING"] is after and sys_cfg["PEERS"] == {} and sys_cfg["_reset"] is True
    assert (run.results[0].removed, run.results[0].added) == (["TRUNK"], ["TRUNK"])
    _assert_hotspot_untouched(run)


def test_a_mode_change_without_trunking_still_merges() -> None:
    """A MODE change that does not flip TRUNKING keeps today's merge: same protocol, same listener."""
    run = _reload(_master(62040, PEERS=dict(_SITE)), {"MODE": "PEER", "ENABLED": True, "IP": "", "PORT": 62040})

    proto = run.old_protos["TRUNK"]
    assert run.protocols["TRUNK"] is proto
    assert not proto.dereg_called and proto.applied == 1
    assert run.removed == [] and run.listened == []
    assert run.old_ports["TRUNK"].stop_calls == 0
    assert run.config["SYSTEMS"]["TRUNK"]["MODE"] == "PEER"
    assert "_reset" not in run.config["SYSTEMS"]["TRUNK"]
    assert run.results[0].updated == ["HOTSPOT", "TRUNK"]
    _assert_hotspot_untouched(run)


def test_a_non_flip_change_keeps_the_trunk_protocol_and_its_sites() -> None:
    """Changing MAX_PEERS, REPEAT or an ACL on a trunk system keeps its protocol and its logged-in sites."""
    run = _reload(
        _master(62040, TRUNKING=True, PEERS=dict(_SITE)),
        _master(62040, TRUNKING=True, MAX_PEERS=8, REPEAT=False, SUB_ACL="DENY:1"),
    )

    proto = run.old_protos["TRUNK"]
    assert run.protocols["TRUNK"] is proto
    assert not proto.dereg_called and proto.applied == 1
    assert run.removed == [] and run.listened == []
    assert run.old_ports["TRUNK"].stop_calls == 0
    sys_cfg = run.config["SYSTEMS"]["TRUNK"]
    assert sys_cfg["PEERS"] == _SITE and sys_cfg["MAX_PEERS"] == 8 and sys_cfg["REPEAT"] is False
    assert "_reset" not in sys_cfg
    _assert_hotspot_untouched(run)


def test_a_collapsed_generator_that_turns_trunking_on_gets_a_new_protocol() -> None:
    """GENERATOR 3 -> 1 keeps SYS-0's listener as SYS; turning TRUNKING on as well is a flip of SYS-0 all the same."""
    run = _reload_systems(
        {
            "SYS-0": _master(62040, PEERS=dict(_SITE)),
            "SYS-1": _master(62041),
            "SYS-2": _master(62042),
            "HOTSPOT": _hotspot(),
        },
        {"SYS": _master(62040, TRUNKING=True), "HOTSPOT": _hotspot()},
    )

    old = run.old_protos["SYS-0"]
    new = run.protocols["SYS"]
    assert new is not old and old.dereg_called
    assert ("SYS-0", old) in run.removed and "SYS-0" not in run.protocols
    assert run.old_ports["SYS-0"].stop_calls == 1
    assert run.listened == [("SYS", 62040, new)]
    sys_cfg = run.config["SYSTEMS"]["SYS"]
    assert sys_cfg["TRUNKING"] is True and sys_cfg["PEERS"] == {} and sys_cfg["_reset"] is True
    assert "SYS-0" not in run.config["SYSTEMS"]
    assert run.results[0].added == ["SYS"] and sorted(run.results[0].removed) == ["SYS-0", "SYS-1", "SYS-2"]
    _assert_hotspot_untouched(run)
