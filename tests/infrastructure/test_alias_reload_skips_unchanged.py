# ADN DMR Peer Server - tests infrastructure alias reload skips unchanged files
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

"""The reload loop ticks far more often than the files behind it change.

STALE_DAYS keeps the download to about once a day while the loop runs every few
minutes so a failed download is retried soon. Every tick in between reads the
same bytes, so it must not pay for them again.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from adn_server.infrastructure.persistence.alias_loader import DefaultAliasLoader


def _write(path: Path, name: str, rid: int, callsign: str) -> None:
    (path / name).write_text(
        json.dumps({"subscribers": [{"id": rid, "callsign": callsign}]}), encoding="utf-8"
    )


def _load(loader: DefaultAliasLoader, path: Path, name: str) -> dict:
    return loader._load_id_dict_with_backup(path, name, None, "subscriber_ids")


def test_an_unchanged_file_is_not_parsed_again(tmp_path: Path) -> None:
    _write(tmp_path, "subscriber_ids.json", 7300391, "CE5RPY")
    loader = DefaultAliasLoader()
    first = _load(loader, tmp_path, "subscriber_ids.json")

    parses = []
    original = loader._load_id_json
    loader._load_id_json = lambda p: (parses.append(p), original(p))[1]  # type: ignore[assignment]
    again = _load(loader, tmp_path, "subscriber_ids.json")

    assert parses == []
    assert again == first


def test_an_unchanged_file_is_not_copied_to_bak_again(tmp_path: Path) -> None:
    """The backup is 50MB in production; rewriting it every tick is pure disk wear."""
    _write(tmp_path, "subscriber_ids.json", 7300391, "CE5RPY")
    loader = DefaultAliasLoader()
    _load(loader, tmp_path, "subscriber_ids.json")
    bak = tmp_path / "subscriber_ids.json.bak"
    assert bak.is_file()

    os.utime(bak, (1_000_000_000, 1_000_000_000))
    _load(loader, tmp_path, "subscriber_ids.json")

    assert bak.stat().st_mtime == 1_000_000_000


def test_a_file_that_changed_is_parsed_again(tmp_path: Path) -> None:
    _write(tmp_path, "subscriber_ids.json", 7300391, "CE5RPY")
    loader = DefaultAliasLoader()
    assert _load(loader, tmp_path, "subscriber_ids.json") == {7300391: "CE5RPY"}

    _write(tmp_path, "subscriber_ids.json", 7300392, "CE5ABC")
    os.utime(tmp_path / "subscriber_ids.json", (2_000_000_000, 2_000_000_000))

    assert _load(loader, tmp_path, "subscriber_ids.json") == {7300392: "CE5ABC"}


def test_a_bad_primary_is_retried_rather_than_remembered(tmp_path: Path) -> None:
    """Falling back to .bak must not cache that result: the next tick has to look
    at the primary again, which is how a repaired download gets picked up.
    """
    name = "subscriber_ids.json"
    _write(tmp_path, name, 7300391, "CE5RPY")
    loader = DefaultAliasLoader()
    _load(loader, tmp_path, name)

    (tmp_path / name).write_text("{ not json", encoding="utf-8")
    assert _load(loader, tmp_path, name) == {7300391: "CE5RPY"}  # from .bak

    _write(tmp_path, name, 7300392, "CE5ABC")
    assert _load(loader, tmp_path, name) == {7300392: "CE5ABC"}


def test_subscriber_profiles_are_not_rebuilt_for_unchanged_files(tmp_path: Path) -> None:
    """This one runs to over a second on 300k subscribers, so it is the tick cost
    that matters most.
    """
    _write(tmp_path, "subscriber_ids.json", 7300391, "CE5RPY")
    cfg = {
        "ALIASES": {
            "PATH": str(tmp_path),
            "SUBSCRIBER_FILE": "subscriber_ids.json",
            "LOCAL_SUBSCRIBER_FILE": "subscriber_ids.json",
        }
    }
    loader = DefaultAliasLoader()
    first = loader.load_subscriber_profiles(cfg)
    assert 7300391 in first

    merges = []
    original = loader._merge_subscriber_profiles
    loader._merge_subscriber_profiles = lambda p, out: merges.append(p)  # type: ignore[assignment]
    assert loader.load_subscriber_profiles(cfg) is first
    assert merges == []

    loader._merge_subscriber_profiles = original  # type: ignore[assignment]
    _write(tmp_path, "subscriber_ids.json", 7300392, "CE5ABC")
    os.utime(tmp_path / "subscriber_ids.json", (2_000_000_000, 2_000_000_000))
    assert 7300392 in loader.load_subscriber_profiles(cfg)
