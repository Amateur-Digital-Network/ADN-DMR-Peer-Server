# ADN DMR Peer Server - OBP RELAX_CHECKS target-address sync log is debug, once per stream

from __future__ import annotations

import logging
from collections import deque
from types import SimpleNamespace

from adn_server.infrastructure.twisted_adapters.udp_hbp import HBPProtocol

_SYNC = HBPProtocol._obp_sync_target_sock_from_peer


def _fake_obp(target_sock=None) -> SimpleNamespace:
    return SimpleNamespace(
        _system="OBP-USA",
        _config={"MODE": "OPENBRIDGE", "RELAX_CHECKS": True, "TARGET_SOCK": target_sock},
        _obp_target_sync_log_once=deque(maxlen=1024),
    )


def test_sync_updates_target_sock_every_packet() -> None:
    fake = _fake_obp(target_sock=("1.1.1.1", 62044))
    _SYNC(fake, ("2.2.2.2", 62044), b"strm")
    assert fake._config["TARGET_SOCK"] == ("2.2.2.2", 62044)
    _SYNC(fake, ("1.1.1.1", 62044), b"strm")
    assert fake._config["TARGET_SOCK"] == ("1.1.1.1", 62044)


def test_sync_logs_debug_once_per_stream_even_when_flapping(caplog) -> None:
    fake = _fake_obp(target_sock=("1.1.1.1", 62044))
    with caplog.at_level(logging.DEBUG, logger="adn_server.infrastructure.twisted_adapters.udp_hbp"):
        for _ in range(20):
            _SYNC(fake, ("2.2.2.2", 62044), b"strm-1")
            _SYNC(fake, ("1.1.1.1", 62044), b"strm-1")
    sync_records = [r for r in caplog.records if "OBP peer address sync" in r.message]
    assert len(sync_records) == 1
    assert sync_records[0].levelno == logging.DEBUG
    # still tracked the address on every flap despite logging once
    assert fake._config["TARGET_SOCK"] == ("1.1.1.1", 62044)


def test_sync_logs_again_for_a_new_stream(caplog) -> None:
    fake = _fake_obp(target_sock=("1.1.1.1", 62044))
    with caplog.at_level(logging.DEBUG, logger="adn_server.infrastructure.twisted_adapters.udp_hbp"):
        _SYNC(fake, ("2.2.2.2", 62044), b"strm-1")
        _SYNC(fake, ("1.1.1.1", 62044), b"strm-1")
        _SYNC(fake, ("2.2.2.2", 62044), b"strm-2")
    sync_records = [r for r in caplog.records if "OBP peer address sync" in r.message]
    assert len(sync_records) == 2


def test_no_relax_checks_no_sync() -> None:
    fake = _fake_obp(target_sock=("1.1.1.1", 62044))
    fake._config["RELAX_CHECKS"] = False
    _SYNC(fake, ("2.2.2.2", 62044), b"strm")
    assert fake._config["TARGET_SOCK"] == ("1.1.1.1", 62044)
