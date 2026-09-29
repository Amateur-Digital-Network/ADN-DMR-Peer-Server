# ADN DMR Peer Server - tests infrastructure alias reload memory
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

"""Every real change to the 300k-subscriber file raised the server's RSS by ~130 MB."""

from __future__ import annotations

import gc
import json
from pathlib import Path

from adn_server.application.talker_alias_use_cases import format_talker_alias_text
from adn_server.infrastructure.persistence.alias_loader import DefaultAliasLoader


def _config(path: Path, local_file: str = "local_subscriber_ids.json") -> dict:
    (path / "subscriber_ids.json").write_text(json.dumps({"count": 2, "results": [
        {"id": "7300391", "callsign": "CE5RPY", "fname": "Rodrigo", "surname": "Perez", "city": "Talca"},
        {"id": "7300392", "callsign": "CE5ABC", "fname": "Rodrigo", "surname": "Soto", "city": "Talca"},
    ]}), encoding="utf-8")
    (path / local_file).write_text(json.dumps({"results": [
        {"id": 7300392, "callsign": "CE5ABC", "fname": "Juan", "surname": "Soto"},
    ]}), encoding="utf-8")
    return {"GLOBAL": {"TALKER_ALIAS_FORMAT": "{callsign} {fname}"}, "ALIASES": {
        "PATH": str(path), "SUBSCRIBER_FILE": "subscriber_ids.json", "LOCAL_SUBSCRIBER_FILE": local_file,
    }}


def _reload(loader: DefaultAliasLoader, config: dict) -> tuple:
    loaded = loader.load_aliases(config)
    DefaultAliasLoader.merge_reload_into_config(
        config, loader, *loaded, profiles=loader.load_subscriber_profiles(config)
    )
    return loaded


def test_the_subscriber_file_is_parsed_once_per_reload(tmp_path: Path) -> None:
    config = _config(tmp_path)
    loader = DefaultAliasLoader()
    parsed = []
    original = loader._load_subscriber_json
    loader._load_subscriber_json = lambda p: (parsed.append(p.name), original(p))[1]  # type: ignore[assignment]

    _reload(loader, config)

    assert parsed.count("subscriber_ids.json") == 1


def test_ids_and_profiles_come_out_of_that_one_parse(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _reload(DefaultAliasLoader(), config)

    assert config["_SUB_IDS"][7300391] == "CE5RPY"
    assert config["_SUB_IDS"][900999] == "D-APRS"
    assert format_talker_alias_text(config, (7300391).to_bytes(3, "big")) == "CE5RPY Rodrigo"
    # The local subscriber file still overrides the downloaded one.
    assert format_talker_alias_text(config, (7300392).to_bytes(3, "big")) == "CE5ABC Juan"


def test_the_live_subscriber_ids_are_not_a_second_copy(tmp_path: Path) -> None:
    config = _config(tmp_path)
    loader = DefaultAliasLoader()
    subscriber_ids = _reload(loader, config)[1]

    assert config["_SUB_IDS"] is subscriber_ids


def test_profiles_stay_out_of_the_garbage_collector(tmp_path: Path) -> None:
    """300k tracked objects add ~40 ms to every full collection, on the reactor thread."""
    config = _config(tmp_path)
    _reload(DefaultAliasLoader(), config)
    gc.collect()

    assert not any(gc.is_tracked(p) for p in config["_SUB_PROFILES"].values())


def test_the_default_local_file_is_not_parsed_again(tmp_path: Path) -> None:
    """LOCAL_SUBSCRIBER_FILE defaults to the subscriber file itself."""
    config = _config(tmp_path, local_file="subscriber_ids.json")
    loader = DefaultAliasLoader()
    loader._load_id_json = lambda p: {} if p.name != "subscriber_ids.json" else 1 / 0  # type: ignore[assignment]

    peer_ids, subscriber_ids, _tg, local_ids, _srv, _chk = loader.load_aliases(config)

    assert local_ids is subscriber_ids
