# ADN DMR Peer Server - alias reload resilience when selfcare download fails

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from unittest.mock import patch

from adn_server.infrastructure.persistence.alias_loader import DefaultAliasLoader, try_download


def _write_subscriber_file(path: Path, file_name: str, rid: int, callsign: str) -> None:
    data = {"subscribers": [{"id": rid, "callsign": callsign}]}
    (path / file_name).write_text(json.dumps(data), encoding="utf-8")


def test_try_download_failure_does_not_erase_existing_file(tmp_path: Path) -> None:
    file_name = "subscriber_ids.json"
    full = tmp_path / file_name
    full.write_bytes(b'{"subscribers":[{"id":7300391,"callsign":"CE5RPY"}]}')
    # stale_sec=0 forces download attempt; bad URL simulates selfcare down.
    # max_attempts=1 keeps the test fast; retry behavior is covered separately below.
    result = try_download(
        tmp_path, file_name, "http://127.0.0.1:1/nope.json", stale_sec=0, max_attempts=1
    )
    assert "could not be downloaded" in result or "IOError" in result
    assert full.read_bytes().startswith(b"{")


def test_try_download_retries_and_recovers_from_transient_failure(tmp_path: Path) -> None:
    file_name = "peer_ids.json"
    calls: list[float] = []

    def _flaky_urlopen(url, context=None, timeout=None):
        calls.append(timeout)
        if len(calls) < 3:
            raise OSError("connection refused")
        return _FakeResponse(b'{"peers":[{"id":1,"callsign":"X"}]}')

    with patch(
        "adn_server.infrastructure.persistence.alias_loader.urlopen", side_effect=_flaky_urlopen
    ):
        result = try_download(
            tmp_path,
            file_name,
            "https://example.invalid/peer_ids.json",
            stale_sec=0,
            max_attempts=3,
            retry_delay_sec=0,
        )
    assert "successfully downloaded" in result
    assert len(calls) == 3
    # first attempt uses the longer timeout, retries use the shorter one
    assert calls[0] == 30
    assert calls[1] == 10


def test_try_download_rejects_data_that_fails_checksum(tmp_path: Path) -> None:
    file_name = "server_ids.tsv"
    full = tmp_path / file_name
    full.write_bytes(b"old-good-content")

    with patch(
        "adn_server.infrastructure.persistence.alias_loader.urlopen",
        return_value=_FakeResponse(b"corrupted-content"),
    ):
        result = try_download(
            tmp_path,
            file_name,
            "https://example.invalid/server_ids.tsv",
            stale_sec=0,
            max_attempts=1,
            expected_checksum="deadbeef",
        )
    assert "checksum mismatch" in result
    assert full.read_bytes() == b"old-good-content"
    assert list(tmp_path.glob(f"{file_name}.tmp.*")) == []


def test_try_download_accepts_data_matching_checksum(tmp_path: Path) -> None:
    file_name = "server_ids.tsv"
    payload = b"good-content"
    digest = hashlib.blake2b(payload).hexdigest()

    with patch(
        "adn_server.infrastructure.persistence.alias_loader.urlopen",
        return_value=_FakeResponse(payload),
    ):
        result = try_download(
            tmp_path,
            file_name,
            "https://example.invalid/server_ids.tsv",
            stale_sec=0,
            max_attempts=1,
            expected_checksum=digest,
        )
    assert "successfully downloaded" in result
    assert (tmp_path / file_name).read_bytes() == payload


def test_try_download_gives_up_after_max_attempts(tmp_path: Path) -> None:
    file_name = "peer_ids.json"
    calls: list[float] = []

    def _always_fails(url, context=None, timeout=None):
        calls.append(timeout)
        raise OSError("connection refused")

    with patch(
        "adn_server.infrastructure.persistence.alias_loader.urlopen", side_effect=_always_fails
    ):
        result = try_download(
            tmp_path,
            file_name,
            "https://example.invalid/peer_ids.json",
            stale_sec=0,
            max_attempts=3,
            retry_delay_sec=0,
        )
    assert "could not be downloaded" in result
    assert len(calls) == 3


class _FakeResponse:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_merge_reload_keeps_previous_sub_ids_on_empty_reload() -> None:
    loader = DefaultAliasLoader()
    config = {
        "_SUB_IDS": {7300391: "CE5RPY"},
        "_PEER_IDS": {730039101: "CE5RPY"},
        "ALIASES": {"PATH": "."},
    }
    DefaultAliasLoader.merge_reload_into_config(
        config,
        loader,
        {},
        {},
        {},
        {},
        {},
        {},
    )
    assert config["_SUB_IDS"] == {7300391: "CE5RPY"}
    assert config["_PEER_IDS"] == {730039101: "CE5RPY"}


def test_load_with_backup_uses_bak_on_checksum_mismatch(tmp_path: Path) -> None:
    loader = DefaultAliasLoader()
    file_name = "subscriber_ids.json"
    _write_subscriber_file(tmp_path, file_name, 1111111, "BAD")
    _write_subscriber_file(tmp_path, f"{file_name}.bak", 7300391, "GOOD")
    loaded = loader._load_with_backup(
        tmp_path, file_name, "deadbeef", "subscriber_ids", loader._load_id_json,
    )
    assert loaded.get(7300391) == "GOOD"


def test_load_with_backup_uses_bak_when_primary_missing(tmp_path: Path) -> None:
    loader = DefaultAliasLoader()
    file_name = "subscriber_ids.json"
    # No primary file at all (e.g. first boot, download never succeeded), only a .bak
    # from a previous successful run.
    _write_subscriber_file(tmp_path, f"{file_name}.bak", 7300391, "GOOD")
    loaded = loader._load_with_backup(
        tmp_path, file_name, None, "subscriber_ids", loader._load_id_json,
    )
    assert loaded.get(7300391) == "GOOD"


def test_load_with_backup_uses_bak_for_the_server_tsv_too(tmp_path: Path) -> None:
    loader = DefaultAliasLoader()
    file_name = "server_ids.tsv"
    (tmp_path / f"{file_name}.bak").write_text(
        "OPB Net ID\tCountry\n1234\tChile\n", encoding="utf-8"
    )
    loaded = loader._load_with_backup(
        tmp_path, file_name, None, "server_ids",
        lambda f: loader._load_server_tsv(f.parent, f.name),
    )
    assert loaded.get("1234") == "Chile"


def test_concurrent_downloads_of_same_file_never_corrupt_it(tmp_path: Path) -> None:
    file_name = "peer_ids.json"
    payload_a = b"A" * 500_000
    payload_b = b"B" * 700_000

    def _urlopen_for(payload):
        def _fake(url, context=None, timeout=None):
            return _FakeResponse(payload)
        return _fake

    barrier = threading.Barrier(2)

    def _run(payload):
        with patch(
            "adn_server.infrastructure.persistence.alias_loader.urlopen",
            side_effect=_urlopen_for(payload),
        ):
            barrier.wait()
            try_download(tmp_path, file_name, "https://example.invalid/peer_ids.json", stale_sec=0)

    t1 = threading.Thread(target=_run, args=(payload_a,))
    t2 = threading.Thread(target=_run, args=(payload_b,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    final = (tmp_path / file_name).read_bytes()
    assert final == payload_a or final == payload_b
    assert list(tmp_path.glob(f"{file_name}.tmp.*")) == []
