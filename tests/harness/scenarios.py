# ADN DMR Peer Server - tests harness scenarios
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

"""Shared deterministic scenario builders for domain tests."""

from __future__ import annotations

from typing import Any

from adn_server.application.talker_alias_use_cases import TalkerAliasUseCases
from adn_server.infrastructure.talker_alias_emblc import default_ta_emblc_encoder

from .deterministic import (
    DeterministicScenario,
    active_routing_table,
    add_openbridge_system,
)


def obp_bridge_scenario(*obp_names: str, tg: int = 52090) -> DeterministicScenario:
    entries = [(name, 1) for name in obp_names] + [("MASTER-A", 2)]
    bridges = active_routing_table(tg, tuple(entries))
    config = DeterministicScenario().config
    for name in obp_names:
        add_openbridge_system(config, name)
    config["SYSTEMS"]["MASTER-A"]["TS2_STATIC"] = str(tg)
    return DeterministicScenario(config=config, routing_table=bridges)


def talker_alias_config() -> dict[str, Any]:
    config = DeterministicScenario().config
    config["GLOBAL"]["TALKER_ALIAS"] = True
    config["GLOBAL"]["TALKER_ALIAS_MODE"] = "inject"
    config["GLOBAL"]["TALKER_ALIAS_FORMAT"] = "{callsign} {fname}"
    # Tests that assert dmra_capture need explicit opt-in (production default is false).
    config["GLOBAL"]["TALKER_ALIAS_SEND_DMRA"] = True
    rid = 3120001
    config["_SUB_PROFILES"] = {
        rid: ("CE5RPY", "Rodrigo", "Perez", ""),
    }
    config["_SUB_IDS"] = {rid: "CE5RPY"}
    return config


def make_talker_alias_use_cases(config: dict[str, Any]) -> TalkerAliasUseCases:
    """Talker Alias use cases with production embedded-LC encoder (test harness)."""
    return TalkerAliasUseCases(config, ta_emblc_encoder=default_ta_emblc_encoder)
