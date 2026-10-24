# ADN DMR Peer Server - tests infrastructure trunk registration store
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

"""Tier III registrations on disk: JSON, atomic writes, and a bad file never stops the server."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import IO

import pytest

from adn_server.application.trunking.registry import Registration, Site, SiteRegistry
from adn_server.infrastructure.persistence import trunk_registration_store as store_mod
from adn_server.infrastructure.persistence.trunk_registration_store import JsonTrunkRegistrationStore

A = Site("TRUNK", (214000101).to_bytes(4, "big"))
B = Site("TRUNK", (214000102).to_bytes(4, "big"))
T0 = 1_761_300_000.0
SAVED = [Registration(2149999, B, T0 - 999.5), Registration(2141234, A, T0)]


def test_saved_file_is_the_documented_json(tmp_path: Path) -> None:
    """A list sorted by radio id of [ms_id, system, peer_id as an integer, unix time]."""
    path = tmp_path / "trunk_registrations.json"
    JsonTrunkRegistrationStore().save(str(path), SAVED)
    assert json.loads(path.read_text(encoding="utf-8")) == [
        [2141234, "TRUNK", 214000101, T0],
        [2149999, "TRUNK", 214000102, T0 - 999.5],
    ]


def test_round_trip(tmp_path: Path) -> None:
    """What is saved loads back unchanged, and a missing file is no registrations."""
    store = JsonTrunkRegistrationStore()
    path = str(tmp_path / "data" / "trunk_registrations.json")
    assert store.load(path) == []
    store.save(path, SAVED)
    assert sorted(store.load(path)) == sorted(SAVED)


def test_restart_restores(tmp_path: Path) -> None:
    """Registrations survive a restart: dmrtc never sends them again."""
    path = str(tmp_path / "trunk_registrations.json")
    before = SiteRegistry()
    before.register(2141234, A, T0)
    before.register(2149999, B, T0 + 1)
    JsonTrunkRegistrationStore().save(path, before.registrations())
    after = SiteRegistry()
    after.restore(JsonTrunkRegistrationStore().load(path), frozenset({"TRUNK"}), T0 + 60)
    assert sorted(after.registrations()) == sorted(before.registrations())


def test_an_atomic_replace_leaves_no_temp_file(tmp_path: Path) -> None:
    """Each save renames its temp file over the old one."""
    path = tmp_path / "trunk_registrations.json"
    store = JsonTrunkRegistrationStore()
    store.save(str(path), SAVED)
    store.save(str(path), SAVED[:1])
    assert [p.name for p in tmp_path.iterdir()] == [path.name]
    assert store.load(str(path)) == SAVED[:1]


def test_a_crash_mid_write_keeps_the_old_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A write that dies half way leaves the previous registrations readable and no temp file behind."""
    path = tmp_path / "trunk_registrations.json"
    store = JsonTrunkRegistrationStore()
    store.save(str(path), SAVED)

    def _dies(rows: object, f: IO[str]) -> None:
        f.write('[[2141234, "TRU')
        raise OSError("disk full")

    monkeypatch.setattr(store_mod.json, "dump", _dies)
    with pytest.raises(OSError):
        store.save(str(path), SAVED[:1])
    monkeypatch.undo()
    assert sorted(store.load(str(path))) == sorted(SAVED)
    assert [p.name for p in tmp_path.iterdir()] == [path.name]


@pytest.mark.parametrize("content", ["[[2141234, \"TRUNK\"", "{\"2141234\": 1}", "\xff\xfe", "null"])
def test_a_corrupt_file_gives_no_registrations_and_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, content: str,
) -> None:
    """Truncated JSON, another shape or bad bytes start the server without registrations, and say so."""
    path = tmp_path / "trunk_registrations.json"
    path.write_bytes(content.encode("latin-1"))
    with caplog.at_level(logging.WARNING, logger=store_mod.__name__):
        assert JsonTrunkRegistrationStore().load(str(path)) == []
    assert "starting without Tier III registrations" in caplog.text


def test_bad_entries_are_skipped(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Entries of the wrong shape or type, or out of range, are dropped; the good ones load."""
    path = tmp_path / "trunk_registrations.json"
    good = [2141234, "TRUNK", 214000101, T0]
    bad = [
        [2141234, "TRUNK", 214000101],
        "2141234",
        ["2141234", "TRUNK", 214000101, T0],
        [True, "TRUNK", 214000101, T0],
        [0, "TRUNK", 214000101, T0],
        [0x1000000, "TRUNK", 214000101, T0],
        [2141234, 7, 214000101, T0],
        [2141234, "TRUNK", 0x100000000, T0],
        [2141234, "TRUNK", 214000101, "now"],
        [2141234, "TRUNK", 214000101, float("nan")],
    ]
    path.write_text(json.dumps([good, *bad]), encoding="utf-8")
    with caplog.at_level(logging.DEBUG, logger=store_mod.__name__):
        assert JsonTrunkRegistrationStore().load(str(path)) == [Registration(2141234, A, T0)]
    assert f"{len(bad)} malformed registrations skipped" in caplog.text
