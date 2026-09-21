# ADN DMR Peer Server - OBP peer anchored to DNS: only a re-resolution may move it

from __future__ import annotations

import logging
from collections import deque
from types import SimpleNamespace

from twisted.internet import defer

from adn_server.domain.mesh_session import ObpBridgeSession
from adn_server.infrastructure.twisted_adapters import udp_hbp
from adn_server.infrastructure.twisted_adapters.udp_hbp import HBPProtocol

_RESOLVE = HBPProtocol._obp_resolve_target
_RESOLVED = HBPProtocol._obp_target_resolved
_REJECT = HBPProtocol._obp_reject_source

_HOST = "peer.example.net"
_CONFIGURED = ("74.132.44.239", 62059)
_MOVED = ("203.0.113.9", 62059)
_ZOMBIE = ("129.80.176.29", 62059)
_NOW = 1_800_000_000.0


def _fake_obp(*, dns_host: str | None = _HOST) -> SimpleNamespace:
    fake = SimpleNamespace(
        _system="OBP-USA",
        _config={"MODE": "OPENBRIDGE", "RELAX_CHECKS": True},
        _session=ObpBridgeSession(
            system_name="OBP-USA", configured_peer=_CONFIGURED, dns_host=dns_host
        ),
        _obp_foreign_source_log_once=deque(maxlen=1024),
    )
    fake._obp_resolve_target = lambda: _RESOLVE(fake)
    fake._obp_target_resolved = lambda host: _RESOLVED(fake, host)
    fake._obp_target_resolve_failed = lambda failure: None
    return fake


class _FakeResolver:
    """Stands in for the reactor: records the names asked for, answers on demand."""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.asked: list[str] = []

    def resolve(self, name: str):
        self.asked.append(name)
        return defer.succeed(self.answer)


def test_a_bridge_configured_with_an_address_never_asks_dns(monkeypatch) -> None:
    resolver = _FakeResolver(_MOVED[0])
    monkeypatch.setattr(udp_hbp, "reactor", resolver)
    fake = _fake_obp(dns_host=None)
    _RESOLVE(fake)
    assert resolver.asked == []


def test_resolving_is_rate_limited(monkeypatch) -> None:
    """An unknown source must not be able to drive one lookup per packet."""
    resolver = _FakeResolver(_CONFIGURED[0])
    monkeypatch.setattr(udp_hbp, "reactor", resolver)
    fake = _fake_obp()
    for _ in range(50):
        _RESOLVE(fake)
    assert len(resolver.asked) == 1


def test_the_peer_follows_the_name_when_it_resolves_elsewhere() -> None:
    fake = _fake_obp()
    _RESOLVED(fake, _MOVED[0])
    assert fake._session.peer == _MOVED


def test_the_peer_stays_put_when_the_name_still_resolves_to_it() -> None:
    fake = _fake_obp()
    _RESOLVED(fake, _CONFIGURED[0])
    assert fake._session.peer == _CONFIGURED


def test_a_frame_from_elsewhere_is_refused_and_asks_dns_again(monkeypatch, caplog) -> None:
    resolver = _FakeResolver(_CONFIGURED[0])
    monkeypatch.setattr(udp_hbp, "reactor", resolver)
    fake = _fake_obp()
    with caplog.at_level(logging.INFO, logger="adn_server.infrastructure.twisted_adapters.udp_hbp"):
        _REJECT(fake, b"BCKA", _ZOMBIE)
    assert resolver.asked == [_HOST]
    assert fake._session.peer == _CONFIGURED, "the zombie took the link over"
    assert "discarded" in caplog.text and _HOST in caplog.text


def test_a_refused_source_is_logged_once(monkeypatch, caplog) -> None:
    resolver = _FakeResolver(_CONFIGURED[0])
    monkeypatch.setattr(udp_hbp, "reactor", resolver)
    fake = _fake_obp()
    with caplog.at_level(logging.INFO, logger="adn_server.infrastructure.twisted_adapters.udp_hbp"):
        for _ in range(30):
            _REJECT(fake, b"BCKA", _ZOMBIE)
    assert sum("discarded" in line for line in caplog.text.splitlines()) == 1


def test_a_peer_that_really_moved_is_adopted_on_the_next_resolution(monkeypatch) -> None:
    """The whole point: a frame from an unknown address is refused, but it makes us
    ask the name again — and when the name answers with that address, we migrate."""
    resolver = _FakeResolver(_MOVED[0])
    monkeypatch.setattr(udp_hbp, "reactor", resolver)
    fake = _fake_obp()

    _REJECT(fake, b"DMRD", _MOVED)  # refused, and the lookup runs inline here

    assert resolver.asked == [_HOST]
    assert fake._session.peer == _MOVED, "the peer did not follow the name"
