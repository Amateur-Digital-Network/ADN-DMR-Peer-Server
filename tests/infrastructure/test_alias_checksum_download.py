# ADN DMR Peer Server - alias downloads driven by the published checksum
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

"""A DMR ID issued today must be usable once the server publishes it, not a day later.

The checksum file is a few hundred bytes, so it is fetched on every tick; the 44MB
lists only when their published checksum stops matching the file on disk.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

from adn_server.infrastructure.persistence.alias_loader import DefaultAliasLoader

_URLOPEN = "adn_server.infrastructure.persistence.alias_loader.urlopen"


class _Resp:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self) -> _Resp:  # noqa: PYI034 (typing.Self needs 3.11)
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _subs(*pairs: tuple[int, str]) -> bytes:
    return json.dumps(
        {"count": len(pairs), "results": [{"id": i, "callsign": c} for i, c in pairs]}
    ).encode()


def _b2(data: bytes) -> str:
    return hashlib.blake2b(data).hexdigest()


class _Server:
    """Serves whatever the test last published, and counts what was fetched."""

    def __init__(self, subs: bytes) -> None:
        self.fetched: list[str] = []
        self.publish(subs)

    def publish(self, subs: bytes, checksum: str | None = None) -> None:
        self.files = {
            "subscriber_ids.json": subs,
            "file_checksums.json": json.dumps(
                {"subscriber_ids": checksum or _b2(subs)}
            ).encode(),
        }

    def urlopen(self, url: str, context=None, timeout=None) -> _Resp:
        name = url.rsplit("/", 1)[-1]
        self.fetched.append(name)
        return _Resp(self.files[name])


def _cfg(path: Path) -> dict:
    return {
        "ALIASES": {
            "TRY_DOWNLOAD": True,
            "PATH": str(path),
            "STALE_DAYS": 1,
            "SUBSCRIBER_FILE": "subscriber_ids.json",
            "LOCAL_SUBSCRIBER_FILE": "subscriber_ids.json",
            "SUBSCRIBER_URL": "https://example.invalid/subscriber_ids.json",
            "CHECKSUM_FILE": "file_checksums.json",
            "CHECKSUM_URL": "https://example.invalid/file_checksums.json",
        }
    }


def _tick(loader: DefaultAliasLoader, server: _Server, cfg: dict) -> dict:
    server.fetched.clear()
    with patch(_URLOPEN, side_effect=server.urlopen):
        return loader.load_aliases(cfg)[1]


def test_a_new_id_is_picked_up_on_the_next_tick_not_after_stale_days(tmp_path: Path) -> None:
    server = _Server(_subs((7300391, "CE5RPY")))
    loader, cfg = DefaultAliasLoader(), _cfg(tmp_path)
    assert 7300391 in _tick(loader, server, cfg)

    server.publish(_subs((7300391, "CE5RPY"), (7300392, "CE5NEW")))
    subs = _tick(loader, server, cfg)

    assert subs.get(7300392) == "CE5NEW"
    assert "subscriber_ids.json" in server.fetched


def test_an_unchanged_list_is_not_fetched_again_however_old(tmp_path: Path) -> None:
    server = _Server(_subs((7300391, "CE5RPY")))
    loader, cfg = DefaultAliasLoader(), _cfg(tmp_path)
    _tick(loader, server, cfg)
    os.utime(tmp_path / "subscriber_ids.json", (1_000_000_000, 1_000_000_000))

    _tick(loader, server, cfg)

    assert server.fetched == ["file_checksums.json"]


def test_a_list_that_never_matches_its_checksum_is_retried_hourly_not_every_tick(
    tmp_path: Path,
) -> None:
    good = _subs((7300391, "CE5RPY"))
    server = _Server(good)
    loader, cfg = DefaultAliasLoader(), _cfg(tmp_path)
    _tick(loader, server, cfg)

    server.publish(_subs((7300392, "CE5NEW")), checksum="0" * 128)
    with patch("adn_server.infrastructure.persistence.alias_loader.time.sleep"):
        _tick(loader, server, cfg)
        assert server.fetched.count("subscriber_ids.json") == 3  # the attempts
        subs = _tick(loader, server, cfg)

    assert server.fetched == ["file_checksums.json"]
    assert subs == {7300391: "CE5RPY"}  # the last good list stays in use
    assert (tmp_path / "subscriber_ids.json").read_bytes() == good
