# ADN DMR Peer Server - tests talker alias default enabled
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

"""Talker Alias is on when TALKER_ALIAS is omitted, and off only when set false."""

from __future__ import annotations

from adn_server.application.talker_alias_use_cases import talker_alias_settings
from adn_server.infrastructure.config_normalizer import apply_talker_alias_defaults


def test_omitted_key_defaults_to_enabled() -> None:
    config: dict = {"GLOBAL": {}}
    apply_talker_alias_defaults(config)
    assert config["GLOBAL"]["TALKER_ALIAS"] is True


def test_settings_enabled_without_key() -> None:
    assert talker_alias_settings({"GLOBAL": {}, "SYSTEMS": {"SYSTEM": {}}}, "SYSTEM")["enabled"] is True


def test_explicit_false_still_disables() -> None:
    config: dict = {"GLOBAL": {"TALKER_ALIAS": False}}
    apply_talker_alias_defaults(config)
    assert talker_alias_settings(config)["enabled"] is False
