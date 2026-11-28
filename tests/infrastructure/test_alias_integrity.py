# ADN DMR Peer Server - alias lists keep their integrity when a download fails
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

"""A failed download never costs the list we had; a corrupt list still never loads.

The published checksum describes the newest list. The one on disk has to describe
the lists on disk, or every list a download failed to replace reads as corrupt.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError
from urllib.request import Request

import pytest

from adn_server.infrastructure.persistence.alias_loader import DefaultAliasLoader

_URLOPEN = "adn_server.infrastructure.persistence.alias_loader.urlopen"

OLD_SERVERS = b"Country\tOPB Net ID\nADN_7301_Chile\t7301\n"
NEW_SERVERS = OLD_SERVERS + b"ADN_7141_Panama\t7141\n"
# One flipped digit: still a readable list, so only the checksum can refuse it.
ROTTEN_SERVERS = OLD_SERVERS.replace(b"\t7301", b"\t7309")


def _b2(data: bytes) -> str:
    return hashlib.blake2b(data).hexdigest()


def _checksums(**lists: bytes) -> bytes:
    return json.dumps({key: _b2(data) for key, data in lists.items()}).encode()


class _Resp:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self.headers: dict[str, str] = {}

    def read(self) -> bytes:
        return self._data

    def __enter__(self) -> _Resp:  # noqa: PYI034 (typing.Self needs 3.11)
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _Origin:
    """What the file server answers; a name in ``down`` fails as a network error."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.down: set[str] = set()

    def urlopen(self, request: Request, context=None, timeout=None) -> _Resp:
        name = request.full_url.rsplit("/", 1)[-1]
        if name in self.down:
            raise URLError("timed out")
        return _Resp(self.files[name])


def _cfg(path: Path, *, try_download: bool = True) -> dict:
    return {
        "ALIASES": {
            "TRY_DOWNLOAD": try_download,
            "PATH": str(path),
            "STALE_DAYS": 1,
            "SERVER_ID_FILE": "server_ids.tsv",
            "SERVER_ID_URL": "https://example.invalid/server_ids.tsv",
            "CHECKSUM_FILE": "file_checksums.json",
            "CHECKSUM_URL": "https://example.invalid/file_checksums.json",
        }
    }


def _start(path: Path, origin: _Origin, *, try_download: bool = True) -> dict[str, str]:
    """A fresh process: no table in memory, only what is on disk."""
    with patch(_URLOPEN, side_effect=origin.urlopen), patch(
        "adn_server.infrastructure.persistence.alias_loader.time.sleep"
    ):
        return DefaultAliasLoader().load_aliases(_cfg(path, try_download=try_download))[4]


def _on_disk(path: Path, *, servers: bytes, checksum_of: bytes | None = None) -> None:
    """Lists and checksum as another tool (or an earlier run) left them; no .bak."""
    (path / "server_ids.tsv").write_bytes(servers)
    (path / "file_checksums.json").write_bytes(_checksums(server_ids=checksum_of or servers))


@pytest.fixture
def newer_published() -> _Origin:
    return _Origin({"server_ids.tsv": NEW_SERVERS, "file_checksums.json": _checksums(server_ids=NEW_SERVERS)})


class TestAFailedDownloadKeepsTheListWeHad:
    """The published checksum moved on, but the new list could not be fetched."""

    def test_the_previous_list_loads(self, tmp_path: Path, newer_published: _Origin) -> None:
        """Lists left by another tool, a newer list published, its download times out:
        the server starts with the list it found instead of an empty one."""
        _on_disk(tmp_path, servers=OLD_SERVERS)
        newer_published.down.add("server_ids.tsv")

        assert _start(tmp_path, newer_published) == {"7301": "ADN_7301_Chile"}

    def test_it_still_loads_after_a_restart(self, tmp_path: Path, newer_published: _Origin) -> None:
        """The checksum on disk is not replaced by one the list on disk can't match."""
        _on_disk(tmp_path, servers=OLD_SERVERS)
        newer_published.down.add("server_ids.tsv")
        _start(tmp_path, newer_published)
        (tmp_path / "server_ids.tsv.bak").unlink()

        assert _start(tmp_path, newer_published) == {"7301": "ADN_7301_Chile"}
        assert _start(tmp_path, newer_published, try_download=False) == {"7301": "ADN_7301_Chile"}

    def test_a_download_that_never_matches_keeps_it_too(self, tmp_path: Path, newer_published: _Origin) -> None:
        """The origin serves an older list than the checksum it publishes."""
        _on_disk(tmp_path, servers=OLD_SERVERS)
        newer_published.files["server_ids.tsv"] = b"Country\tOPB Net ID\nADN_2131_Andorra\t2131\n"

        assert _start(tmp_path, newer_published) == {"7301": "ADN_7301_Chile"}
        assert (tmp_path / "server_ids.tsv").read_bytes() == OLD_SERVERS

    def test_the_new_list_is_used_once_its_download_succeeds(
        self, tmp_path: Path, newer_published: _Origin
    ) -> None:
        _on_disk(tmp_path, servers=OLD_SERVERS)
        newer_published.down.add("server_ids.tsv")
        _start(tmp_path, newer_published)
        newer_published.down.clear()

        assert set(_start(tmp_path, newer_published)) == {"7301", "7141"}
        on_disk = json.loads((tmp_path / "file_checksums.json").read_bytes())
        assert on_disk["server_ids"] == _b2(NEW_SERVERS)


class TestACorruptListNeverLoads:
    """The checksum is there to refuse damaged files, and it still does."""

    def test_a_damaged_list_is_refused_when_the_download_fails(
        self, tmp_path: Path, newer_published: _Origin
    ) -> None:
        _on_disk(tmp_path, servers=ROTTEN_SERVERS, checksum_of=OLD_SERVERS)
        newer_published.down.add("server_ids.tsv")

        assert _start(tmp_path, newer_published) == {}

    def test_a_damaged_list_is_refused_offline_too(self, tmp_path: Path, newer_published: _Origin) -> None:
        _on_disk(tmp_path, servers=ROTTEN_SERVERS, checksum_of=OLD_SERVERS)

        assert _start(tmp_path, newer_published, try_download=False) == {}


class TestTheChecksumFileItselfFails:
    """Without the published checksum, the one on disk still describes our lists."""

    def test_a_list_past_stale_days_is_not_replaced_unverified(
        self, tmp_path: Path, newer_published: _Origin
    ) -> None:
        """The checksum URL is down but the list URL answers: the download is checked
        against the checksum on disk, so a list nothing can verify never replaces ours."""
        _on_disk(tmp_path, servers=OLD_SERVERS)
        os.utime(tmp_path / "server_ids.tsv", (1_000_000_000, 1_000_000_000))
        newer_published.down.add("file_checksums.json")

        assert _start(tmp_path, newer_published) == {"7301": "ADN_7301_Chile"}
        assert (tmp_path / "server_ids.tsv").read_bytes() == OLD_SERVERS

    def test_a_checksum_reply_that_is_not_json_keeps_the_lists(
        self, tmp_path: Path, newer_published: _Origin
    ) -> None:
        """A captive portal or a proxy error page answering with HTML."""
        _on_disk(tmp_path, servers=OLD_SERVERS)
        newer_published.files["file_checksums.json"] = b"<html>502 Bad Gateway</html>"
        newer_published.down.add("server_ids.tsv")

        assert _start(tmp_path, newer_published) == {"7301": "ADN_7301_Chile"}
        on_disk = json.loads((tmp_path / "file_checksums.json").read_bytes())
        assert on_disk["server_ids"] == _b2(OLD_SERVERS)
