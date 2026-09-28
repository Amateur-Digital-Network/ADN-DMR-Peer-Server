# ADN DMR Peer Server - OBP monitor voice END reporting
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

"""OBP monitor voice END,RX / END,TX report timing (no premature cascade)."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from tests.harness.deterministic import (
    FakeClock,
    FakeObpProtocol,
    FakeReportFactory,
    FakeReportSender,
    add_openbridge_system,
    minimal_config,
)

from adn_server.application.reporting_use_cases import ReportingUseCases
from adn_server.application.routing_use_cases import RoutingUseCases
from adn_server.domain import bytes_3, bytes_4
from adn_server.domain.dmr.bptc import encode_emblc
from adn_server.infrastructure.acl_router import InMemoryAclRouter
from adn_server.infrastructure.subscription_store import InMemorySubscriptionStore
from adn_server.infrastructure.talker_alias_emblc import default_ta_emblc_encoder


def _event_system(event: str) -> str:
    parts = event.split(",")
    return parts[3] if len(parts) > 3 else ""


def _end_tx_for(events: list[str], system: str) -> list[str]:
    return [e for e in events if ",END,TX," in e and _event_system(e) == system]


def _end_rx_for(events: list[str], system: str) -> list[str]:
    return [e for e in events if ",END,RX," in e and _event_system(e) == system]


@contextmanager
def _patch_wall_time(clock: FakeClock):
    import adn_server.application.routing.timers as timers_mod
    import adn_server.application.routing_use_cases as buc

    orig_t = timers_mod.time.time
    orig_b = buc.time.time
    timers_mod.time.time = clock.time
    buc.time.time = clock.time
    try:
        yield
    finally:
        timers_mod.time.time = orig_t
        buc.time.time = orig_b


def _obp_stack(
    names: tuple[str, ...],
) -> tuple[RoutingUseCases, FakeReportFactory, FakeClock, dict[str, FakeObpProtocol]]:
    config = minimal_config()
    for name in names:
        add_openbridge_system(config, name)
    config["REPORTS"]["REPORT"] = True
    clock = FakeClock()
    report_factory = FakeReportFactory()
    protocols = {name: FakeObpProtocol(name) for name in names}
    routing = RoutingUseCases(
        InMemoryAclRouter(),
        config,
        InMemorySubscriptionStore(),
        get_protocols=lambda: protocols,
        reporting=ReportingUseCases(FakeReportSender(report_factory), config),
        encode_emblc=encode_emblc,
        ta_emblc_encoder=default_ta_emblc_encoder,
    )
    return routing, report_factory, clock, protocols


def _forward_leg(
    *,
    peer_id: bytes,
    rf_src: bytes,
    tgid: bytes,
    start: float,
    last: float,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "H_LC": b"\x01",
        "RX_PEER": peer_id,
        "RFS": rf_src,
        "TGID": tgid,
        "START": start,
        "LAST": last,
        **extra,
    }


def _ingress_leg(
    *,
    peer_id: bytes,
    rf_src: bytes,
    tgid: bytes,
    start: float,
    last: float,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "RX_PEER": peer_id,
        "RFS": rf_src,
        "TGID": tgid,
        "START": start,
        "LAST": last,
        "_monitor_canonical_rx": True,
        **extra,
    }


def test_bcsq_emits_end_tx_only_on_quenched_leg() -> None:
    routing, factory, clock, protocols = _obp_stack(("OBP-USA", "OBP-ES", "OBP-CU"))
    stream_id = bytes_4(0x1199AABB)
    tgid = bytes_3(52090)
    now = clock.time()
    protocols["OBP-ES"].STATUS[stream_id] = _forward_leg(
        peer_id=bytes_4(71411),
        rf_src=bytes_3(3120001),
        tgid=tgid,
        start=now,
        last=now,
    )

    with _patch_wall_time(clock):
        routing.on_obp_bcsq_received("OBP-ES", tgid, stream_id)

    assert len(_end_tx_for(factory.events, "OBP-ES")) == 1
    assert _end_tx_for(factory.events, "OBP-CU") == []
    assert _end_tx_for(factory.events, "OBP-USA") == []
    assert protocols["OBP-ES"].STATUS[stream_id].get("_bcsq_quenched") is True
    assert protocols["OBP-ES"].STATUS[stream_id].get("_end_tx_sent") is True


def test_forward_leg_idle_does_not_cascade_end_tx() -> None:
    routing, factory, clock, protocols = _obp_stack(("OBP-USA", "OBP-ES", "OBP-CU"))
    stream_id = bytes_4(0x297999345)
    tgid = bytes_3(52090)
    now = clock.time()
    protocols["OBP-USA"].STATUS[stream_id] = _ingress_leg(
        peer_id=bytes_4(31031),
        rf_src=bytes_3(3120001),
        tgid=tgid,
        start=now,
        last=now,
    )
    protocols["OBP-ES"].STATUS[stream_id] = _forward_leg(
        peer_id=bytes_4(71411),
        rf_src=bytes_3(3120001),
        tgid=tgid,
        start=now - 6,
        last=now - 6,
        _end_tx_sent=True,
        _bcsq_quenched=True,
    )
    protocols["OBP-CU"].STATUS[stream_id] = _forward_leg(
        peer_id=bytes_4(31031),
        rf_src=bytes_3(3120001),
        tgid=tgid,
        start=now,
        last=now,
    )

    with _patch_wall_time(clock):
        routing.stream_trimmer_loop()

    assert _end_rx_for(factory.events, "OBP-ES") == []
    assert _end_tx_for(factory.events, "OBP-CU") == []
    assert _end_tx_for(factory.events, "OBP-ES") == []
    assert _end_rx_for(factory.events, "OBP-USA") == []
    assert protocols["OBP-ES"].STATUS[stream_id].get("_to") is True


def test_ingress_idle_emits_end_rx_and_active_forward_end_tx_only() -> None:
    routing, factory, clock, protocols = _obp_stack(("OBP-USA", "OBP-ES", "OBP-CU"))
    stream_id = bytes_4(0x297999345)
    tgid = bytes_3(52090)
    now = clock.time()
    protocols["OBP-USA"].STATUS[stream_id] = _ingress_leg(
        peer_id=bytes_4(31031),
        rf_src=bytes_3(3120001),
        tgid=tgid,
        start=now - 10,
        last=now - 6,
    )
    protocols["OBP-ES"].STATUS[stream_id] = _forward_leg(
        peer_id=bytes_4(71411),
        rf_src=bytes_3(3120001),
        tgid=tgid,
        start=now - 10,
        last=now - 1,
        _end_tx_sent=True,
    )
    protocols["OBP-CU"].STATUS[stream_id] = _forward_leg(
        peer_id=bytes_4(31031),
        rf_src=bytes_3(3120001),
        tgid=tgid,
        start=now - 10,
        last=now - 1,
    )

    with _patch_wall_time(clock):
        routing.stream_trimmer_loop()

    assert len(_end_rx_for(factory.events, "OBP-USA")) == 1
    assert _end_tx_for(factory.events, "OBP-ES") == []
    assert len(_end_tx_for(factory.events, "OBP-CU")) == 1
    usa_event = _end_rx_for(factory.events, "OBP-USA")[0]
    assert usa_event.endswith("4.00") or usa_event.endswith("4.0")


def test_bcsq_end_tx_not_duplicated() -> None:
    routing, factory, clock, protocols = _obp_stack(("OBP-ES",))
    stream_id = bytes_4(0xAABBCCDD)
    tgid = bytes_3(52090)
    now = clock.time()
    protocols["OBP-ES"].STATUS[stream_id] = _forward_leg(
        peer_id=bytes_4(71411),
        rf_src=bytes_3(3120001),
        tgid=tgid,
        start=now,
        last=now,
        _end_tx_sent=True,
    )

    with _patch_wall_time(clock):
        routing.on_obp_bcsq_received("OBP-ES", tgid, stream_id)

    assert factory.events == []


def test_idle_forward_leg_without_end_tx_is_trimmed() -> None:
    """A forward leg whose VTERM/BCSQ never flagged it must still age out (was leaking one row per call)."""
    routing, factory, clock, protocols = _obp_stack(("OBP-ES",))
    stream_id = bytes_4(0x01020304)
    tgid = bytes_3(213)
    now = clock.time()
    protocols["OBP-ES"].STATUS[stream_id] = _forward_leg(
        peer_id=bytes_4(71411),
        rf_src=bytes_3(2130035),
        tgid=tgid,
        start=now - 6,
        last=now - 6,
    )

    with _patch_wall_time(clock):
        routing.stream_trimmer_loop()
        assert protocols["OBP-ES"].STATUS[stream_id].get("_to") is True
        assert _end_tx_for(factory.events, "OBP-ES") == []
        clock.advance(181)
        routing.stream_trimmer_loop()

    assert stream_id not in protocols["OBP-ES"].STATUS
    assert len(_end_tx_for(factory.events, "OBP-ES")) == 1
