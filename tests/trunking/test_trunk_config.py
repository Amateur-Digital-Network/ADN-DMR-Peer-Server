# ADN DMR Peer Server - tests trunking config
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

"""``TRUNKING: true`` on a MASTER: the flag, the enabled trunk systems and the validator rules."""

from __future__ import annotations

from typing import Any

import pytest

from adn_server.application.trunking.config import system_is_trunking, trunk_systems
from adn_server.domain.errors import ConfigError
from adn_server.infrastructure.config_validator import validate_config
from tests.conftest import minimal_valid_config


def _trunk(**overrides: Any) -> dict[str, Any]:
    return {
        "MODE": "MASTER",
        "ENABLED": True,
        "TRUNKING": True,
        "PORT": 62040,
        "PASSPHRASE": "secret",
        "MAX_PEERS": 4,
        "REPEAT": True,
        **overrides,
    }


def _config(trunk: dict[str, Any], **proxy: Any) -> dict[str, Any]:
    return minimal_valid_config(SYSTEMS={"TRUNK": trunk}, PROXY=proxy)


def _errors(config: dict[str, Any]) -> str:
    try:
        validate_config(config)
    except ConfigError as e:
        return str(e)
    return ""


_RULES = [
    pytest.param({"MODE": "PEER"}, {}, "SYSTEMS.TRUNK.TRUNKING: only on MODE MASTER.", id="mode"),
    pytest.param(
        {"PORT": 0}, {"TARGET_SYSTEM": "TRUNK"},
        "SYSTEMS.TRUNK.TRUNKING: not supported on PROXY.TARGET_SYSTEM; a Tier III system needs its own MASTER",
        id="proxy-target",
    ),
    pytest.param({"GENERATOR": 2}, {}, "SYSTEMS.TRUNK.GENERATOR: must be 0 or 1 with TRUNKING.", id="generator"),
    pytest.param({"SINGLE_MODE": True}, {}, "SYSTEMS.TRUNK.SINGLE_MODE: not supported with TRUNKING", id="single-mode"),
    pytest.param({"TS1_STATIC": "91"}, {}, "SYSTEMS.TRUNK.TS1_STATIC: not supported with TRUNKING", id="ts1-static"),
    pytest.param({"TS2_STATIC": "214"}, {}, "SYSTEMS.TRUNK.TS2_STATIC: not supported with TRUNKING", id="ts2-static"),
    pytest.param(
        {"DEFAULT_REFLECTOR": 91}, {}, "SYSTEMS.TRUNK.DEFAULT_REFLECTOR: not supported with TRUNKING.",
        id="default-reflector",
    ),
    pytest.param({"OPTIONS": ""}, {}, "SYSTEMS.TRUNK.OPTIONS: not supported with TRUNKING.", id="options"),
    pytest.param(
        {"SUB_MAP_LEARN": False}, {}, "SYSTEMS.TRUNK.SUB_MAP_LEARN: must stay true with TRUNKING", id="sub-map-learn",
    ),
]


def test_a_trunk_master_with_its_own_port_is_valid() -> None:
    """The minimal Tier III system validates, with the allowed values of every ruled key set."""
    assert _errors(_config(_trunk())) == ""
    allowed = _trunk(
        GENERATOR=1, SINGLE_MODE=False, TS1_STATIC="", TS2_STATIC="", DEFAULT_REFLECTOR=0, SUB_MAP_LEARN=True,
    )
    assert _errors(_config(allowed)) == ""


@pytest.mark.parametrize(("change", "proxy", "error"), _RULES)
def test_each_rule_refuses_a_trunk_system(change: dict[str, Any], proxy: dict[str, Any], error: str) -> None:
    """Each setting that would give a site legs it did not subscribe is refused, enabled or not."""
    assert error in _errors(_config(_trunk(**change), **proxy))
    assert error in _errors(_config(_trunk(ENABLED=False, **change), **proxy))


@pytest.mark.parametrize(("change", "proxy", "error"), _RULES)
def test_the_rules_leave_other_systems_alone(change: dict[str, Any], proxy: dict[str, Any], error: str) -> None:
    """The same settings without TRUNKING validate as today."""
    assert error not in _errors(_config(_trunk(TRUNKING=False, **change), **proxy))
    assert error not in _errors(_config({key: v for key, v in _trunk(**change).items() if key != "TRUNKING"}, **proxy))


@pytest.mark.parametrize("value", ["true", 1, "yes"])
def test_trunking_must_be_a_bool(value: Any) -> None:
    """A quoted or numeric TRUNKING is refused, not read as on or off."""
    assert "SYSTEMS.TRUNK.TRUNKING: expected boolean" in _errors(_config(_trunk(TRUNKING=value)))


@pytest.mark.parametrize(("sys_cfg", "trunking"), [
    ({"MODE": "MASTER", "TRUNKING": True}, True),
    ({"MODE": "MASTER", "TRUNKING": False}, False),
    ({"MODE": "MASTER"}, False),
    ({"MODE": "MASTER", "TRUNKING": "true"}, False),
    ({"MODE": "PEER", "TRUNKING": True}, False),
    ({"MODE": "OPENBRIDGE", "TRUNKING": True}, False),
    ({}, False),
    (None, False),
])
def test_only_a_master_with_trunking_true_is_trunking(sys_cfg: dict[str, Any] | None, trunking: bool) -> None:
    """The one marker: TRUNKING exactly true on a MASTER."""
    assert system_is_trunking(sys_cfg) is trunking


def test_trunk_systems_are_the_enabled_trunking_masters() -> None:
    """A disabled trunk system and every other system are left out."""
    systems = {
        "TRUNK": {"MODE": "MASTER", "TRUNKING": True},
        "TRUNK-ON": {"MODE": "MASTER", "ENABLED": True, "TRUNKING": True},
        "TRUNK-OFF": {"MODE": "MASTER", "ENABLED": False, "TRUNKING": True},
        "HOTSPOT": {"MODE": "MASTER", "ENABLED": True},
        "OBP": {"MODE": "OPENBRIDGE", "ENABLED": True, "TRUNKING": True},
    }
    assert trunk_systems(systems) == frozenset({"TRUNK", "TRUNK-ON"})
    assert trunk_systems({}) == frozenset()
