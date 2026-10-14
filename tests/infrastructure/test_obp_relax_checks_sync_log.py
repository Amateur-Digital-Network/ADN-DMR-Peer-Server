# ADN DMR Peer Server - OBP RELAX_CHECKS peer sync moves the session, logs once per stream

from __future__ import annotations

import logging
from collections import deque
from types import SimpleNamespace

from adn_server.domain.mesh_session import ObpBridgeSession
from adn_server.infrastructure.twisted_adapters.udp_hbp import HBPProtocol

_SYNC = HBPProtocol._obp_sync_target_sock_from_peer


def _fake_obp(configured=("1.1.1.1", 62044)) -> SimpleNamespace:
    return SimpleNamespace(
        _system="OBP-USA",
        _config={"MODE": "OPENBRIDGE", "RELAX_CHECKS": True},
        _session=ObpBridgeSession(system_name="OBP-USA", configured_peer=configured),
        _obp_target_sync_log_once=deque(maxlen=1024),
    )


def test_sync_follows_the_peer_on_every_packet() -> None:
    fake = _fake_obp()
    _SYNC(fake, ("2.2.2.2", 62044), b"strm")
    assert fake._session.peer == ("2.2.2.2", 62044)
    _SYNC(fake, ("1.1.1.1", 62044), b"strm")
    assert fake._session.peer == ("1.1.1.1", 62044)


def test_sync_never_moves_the_configured_peer() -> None:
    """What the operator wrote in the YAML is not what the wire says."""
    fake = _fake_obp()
    _SYNC(fake, ("2.2.2.2", 62044), b"strm")
    assert fake._session.configured_peer == ("1.1.1.1", 62044)
    assert fake._session.learned_peer == ("2.2.2.2", 62044)


def test_sync_logs_debug_once_per_stream_even_when_flapping(caplog) -> None:
    fake = _fake_obp()
    with caplog.at_level(logging.DEBUG, logger="adn_server.infrastructure.twisted_adapters.udp_hbp"):
        for _ in range(20):
            _SYNC(fake, ("2.2.2.2", 62044), b"strm-1")
            _SYNC(fake, ("1.1.1.1", 62044), b"strm-1")
    sync_records = [r for r in caplog.records if "OBP peer address sync" in r.message]
    assert len(sync_records) == 1
    assert sync_records[0].levelno == logging.DEBUG
    # still tracked the address on every flap despite logging once
    assert fake._session.peer == ("1.1.1.1", 62044)


def test_sync_logs_again_for_a_new_stream(caplog) -> None:
    fake = _fake_obp()
    with caplog.at_level(logging.DEBUG, logger="adn_server.infrastructure.twisted_adapters.udp_hbp"):
        _SYNC(fake, ("2.2.2.2", 62044), b"strm-1")
        _SYNC(fake, ("1.1.1.1", 62044), b"strm-1")
        _SYNC(fake, ("2.2.2.2", 62044), b"strm-2")
    sync_records = [r for r in caplog.records if "OBP peer address sync" in r.message]
    assert len(sync_records) == 2


def test_no_relax_checks_no_sync() -> None:
    fake = _fake_obp()
    fake._config["RELAX_CHECKS"] = False
    _SYNC(fake, ("2.2.2.2", 62044), b"strm")
    assert fake._session.peer == ("1.1.1.1", 62044)
    assert fake._session.learned_peer is None
