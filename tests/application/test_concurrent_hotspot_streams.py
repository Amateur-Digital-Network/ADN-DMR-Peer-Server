# ADN DMR Peer Server - tests application concurrent hotspot streams on one MASTER slot
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

"""Hotspots sharing the inject MASTER overwrite its slot RX state: a stream that lost its
terminator while another hotspot talked on the same slot never timed out (stuck dashboard)."""

from __future__ import annotations

from types import SimpleNamespace

from tests.application.test_obp_monitor_voice_end import _end_rx_for, _patch_wall_time
from tests.harness.deterministic import FakeClock, FakeReportFactory, FakeReportSender, minimal_config

from adn_server.application.reporting_use_cases import ReportingUseCases
from adn_server.application.routing.helpers import track_rx_stream
from adn_server.application.routing_use_cases import RoutingUseCases
from adn_server.domain import HBPF_SLT_VHEAD, HBPF_SLT_VTERM, bytes_3, bytes_4
from adn_server.domain.dmr.bptc import encode_emblc
from adn_server.infrastructure.acl_router import InMemoryAclRouter
from adn_server.infrastructure.subscription_store import InMemorySubscriptionStore
from adn_server.infrastructure.talker_alias_emblc import default_ta_emblc_encoder

A, B = b"\x0a" * 4, b"\x0b" * 4


def _stack():
    config = minimal_config(("SYSTEM",))
    config["REPORTS"]["REPORT"] = True
    clock, factory = FakeClock(), FakeReportFactory()
    slot = {"RX_TYPE": HBPF_SLT_VTERM, "RX_TIME": 0.0, "RX_STREAM_ID": b"\x00"}
    protocols = {"SYSTEM": SimpleNamespace(STATUS={1: {"RX_TYPE": HBPF_SLT_VTERM}, 2: slot})}
    routing = RoutingUseCases(
        InMemoryAclRouter(), config, InMemorySubscriptionStore(), get_protocols=lambda: protocols,
        reporting=ReportingUseCases(FakeReportSender(factory), config),
        encode_emblc=encode_emblc, ta_emblc_encoder=default_ta_emblc_encoder,
    )
    return routing, factory, clock, slot


def _frame(slot: dict, stream: bytes, peer: int, tg: int, now: float) -> None:
    """What udp_hbp records for an accepted hotspot frame."""
    slot.update(RX_STREAM_ID=stream, RX_PEER=bytes_4(peer), RX_RFS=bytes_3(peer // 100), RX_TGID=bytes_3(tg),
                RX_TYPE=HBPF_SLT_VHEAD, RX_TIME=now, RX_START=slot.get("RX_START", now))
    track_rx_stream(slot, stream, bytes_4(peer), bytes_3(peer // 100), bytes_3(tg), now, ended=False)


def test_a_stream_that_lost_its_terminator_times_out_while_another_hotspot_talks() -> None:
    routing, factory, clock, slot = _stack()
    now = clock.time()
    _frame(slot, A, 730039101, 730801, now - 10)  # A stopped without a terminator
    _frame(slot, B, 730039102, 730802, now)  # B owns the slot's RX fields now
    with _patch_wall_time(clock):
        routing.stream_trimmer_loop()
    ends = _end_rx_for(factory.events, "SYSTEM")
    assert len(ends) == 1 and ends[0].split(",")[4] == str(int.from_bytes(A, "big"))
    assert ends[0].split(",")[8] == "730801"
    assert A not in slot["RX_STREAMS"] and B in slot["RX_STREAMS"]


def test_the_last_stream_on_the_slot_gets_one_end_not_two() -> None:
    routing, factory, clock, slot = _stack()
    now = clock.time()
    _frame(slot, B, 730039102, 730802, now - 10)
    with _patch_wall_time(clock):
        routing.stream_trimmer_loop()
    assert len(_end_rx_for(factory.events, "SYSTEM")) == 1
    assert slot["RX_TYPE"] == HBPF_SLT_VTERM


def test_a_stream_ended_by_its_terminator_does_not_time_out_again() -> None:
    routing, factory, clock, slot = _stack()
    now = clock.time()
    _frame(slot, A, 730039101, 730801, now - 10)
    track_rx_stream(slot, A, bytes_4(730039101), bytes_3(7300391), bytes_3(730801), now - 10, ended=True)
    slot["RX_TYPE"] = HBPF_SLT_VTERM  # the terminator's END RX was already sent by routing
    with _patch_wall_time(clock):
        routing.stream_trimmer_loop()
    assert _end_rx_for(factory.events, "SYSTEM") == []

