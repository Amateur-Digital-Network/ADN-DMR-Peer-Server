# ADN DMR Peer Server - tests hbp timeout collision
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

"""HBP ingress 180s timeout and stream collision."""

from __future__ import annotations

import logging

import pytest
from tests.harness.assertions import assert_forwarded, assert_inject_ok
from tests.harness.deterministic import DeterministicScenario, PacketSpec, active_routing_table

from adn_server.domain import bytes_3, bytes_4
from adn_server.domain.hbp_protocol import HBPF_SLT_VHEAD


def test_hbp_source_timeout_drops_after_180_seconds() -> None:
    bridges = active_routing_table(91, (("MASTER-A", 2), ("MASTER-B", 2)))
    scenario = DeterministicScenario(routing_table=bridges)
    base = PacketSpec(dst_id=91, stream_id=0x90909090)
    t0 = scenario.clock.time()

    scenario.inject_hbp(
        "MASTER-A",
        DeterministicScenario.voice_head_spec(base),
        ingress_pkt_time=t0,
    )
    ok = scenario.inject_hbp(
        "MASTER-A",
        DeterministicScenario.voice_burst_spec(base, seq=1, dtype_vseq=1),
        ingress_pkt_time=t0 + 181.0,
    )

    assert not ok
    assert scenario.protocols["MASTER-A"].STATUS[2].get("LOOPLOG") is True


def test_hbp_stream_collision_silent_activation_on_busy_tg() -> None:
    """Spec §3 divergence: TX onto a TG with an active QSO is not rejected.

    The stream passes the ingress gate (silent activation), but uplink audio is
    suppressed — no forwarding to other systems. The downlink of the active QSO
    continues to reach the peer independently.
    """
    bridges = active_routing_table(91, (("MASTER-A", 2), ("MASTER-B", 2)))
    scenario = DeterministicScenario(routing_table=bridges)
    t0 = scenario.clock.time()
    slot = scenario.protocols["MASTER-A"].STATUS[2]
    slot.update(
        {
            "RX_STREAM_ID": bytes_4(0x80808080),
            "RX_TYPE": HBPF_SLT_VHEAD,
            "RX_RFS": bytes_3(1111111),
            "RX_TGID": bytes_3(91),
            "RX_TIME": t0,
            "RX_START": t0,
        }
    )
    base = PacketSpec(dst_id=91, stream_id=0x70707070, rf_src=2222222)

    ok = scenario.inject_hbp(
        "MASTER-A",
        DeterministicScenario.voice_head_spec(base),
        ingress_pkt_time=t0 + 0.1,
    )

    assert_inject_ok(ok)
    # Uplink is suppressed: no packets forwarded to MASTER-B
    assert len(scenario.capture.for_system("MASTER-B")) == 0
    # Silent activation marker is set on the source slot
    assert scenario.protocols["MASTER-A"].STATUS[2].get("_suppress_uplink") is True


def test_hbp_silent_activation_logs_once_per_peer_tg(caplog: pytest.LogCaptureFixture) -> None:
    """Connect-PTT style bursts may open many stream IDs; log silent activation once."""
    bridges = active_routing_table(91, (("MASTER-A", 2), ("MASTER-B", 2)))
    scenario = DeterministicScenario(routing_table=bridges)
    t0 = scenario.clock.time()
    slot = scenario.protocols["MASTER-A"].STATUS[2]
    slot.update(
        {
            "RX_STREAM_ID": bytes_4(0x80808080),
            "RX_TYPE": HBPF_SLT_VHEAD,
            "RX_RFS": bytes_3(1111111),
            "RX_TGID": bytes_3(91),
            "RX_TIME": t0,
            "RX_START": t0,
        }
    )
    peer = 2222222
    streams = (0x70707070, 0x71717171, 0x72727272)

    with caplog.at_level(logging.INFO):
        for sid in streams:
            base = PacketSpec(dst_id=91, stream_id=sid, rf_src=peer)
            ok = scenario.inject_hbp(
                "MASTER-A",
                DeterministicScenario.voice_head_spec(base),
                ingress_pkt_time=t0 + 0.1,
            )
            assert_inject_ok(ok)
            burst = DeterministicScenario.voice_burst_spec(base, seq=1, dtype_vseq=1)
            assert_inject_ok(
                scenario.inject_hbp("MASTER-A", burst, ingress_pkt_time=t0 + 0.12)
            )

    hits = [
        r.message
        for r in caplog.records
        if "activating dynamic TG silently" in r.message
    ]
    assert len(hits) == 1


@pytest.mark.behavior
def test_hbp_collision_allows_same_subscriber_rekey() -> None:
    """Regression: same RF source may start a new stream while prior call is still open."""
    bridges = active_routing_table(91, (("MASTER-A", 2), ("MASTER-B", 2)))
    scenario = DeterministicScenario(routing_table=bridges)
    t0 = scenario.clock.time()
    rf_src = 3120001
    slot = scenario.protocols["MASTER-A"].STATUS[2]
    slot.update(
        {
            "RX_STREAM_ID": bytes_4(0x60606060),
            "RX_TYPE": HBPF_SLT_VHEAD,
            "RX_RFS": bytes_3(rf_src),
            "RX_TGID": bytes_3(91),
            "RX_TIME": t0,
            "RX_START": t0,
        }
    )
    base = PacketSpec(dst_id=91, stream_id=0x50505050, rf_src=rf_src)

    ok = scenario.inject_hbp(
        "MASTER-A",
        DeterministicScenario.voice_head_spec(base),
        ingress_pkt_time=t0 + 0.1,
    )

    assert_inject_ok(ok)
    assert_forwarded(scenario, "MASTER-B", count=1, dst_id=91)


def _busy_slot(scenario: DeterministicScenario, t0: float) -> dict:
    """MASTER-A slot 2 carrying another caller's live QSO on TG 91."""
    slot = scenario.protocols["MASTER-A"].STATUS[2]
    other = bytes_4(0x80808080)
    slot.update(
        {
            "RX_STREAM_ID": other,
            "RX_TYPE": HBPF_SLT_VHEAD,
            "RX_RFS": bytes_3(1111111),
            "RX_TGID": bytes_3(91),
            "RX_TIME": t0,
            "RX_START": t0,
            "RX_STREAMS": {other: [bytes_4(1), bytes_3(1111111), bytes_3(91), t0, t0]},
        }
    )
    return slot


def test_hbp_silent_activation_lifts_when_blocking_qso_ends(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A reply keyed right after the other station's last frame must not stay muted.

    Seen on a production server: the reply started 0.2 s after the previous over ended
    (inside STREAM_TO), was silently activated and nobody heard any of its 22 s.
    Once the blocking QSO is over, the stream is forwarded from that frame on.
    """
    bridges = active_routing_table(91, (("MASTER-A", 2), ("MASTER-B", 2)))
    scenario = DeterministicScenario(routing_table=bridges)
    t0 = scenario.clock.time()
    slot = _busy_slot(scenario, t0)
    base = PacketSpec(dst_id=91, stream_id=0x70707070, rf_src=2222222)

    assert_inject_ok(
        scenario.inject_hbp(
            "MASTER-A", DeterministicScenario.voice_head_spec(base), ingress_pkt_time=t0 + 0.2,
        )
    )
    assert slot.get("_suppress_uplink") is True
    assert len(scenario.capture.for_system("MASTER-B")) == 0

    # The other caller ended (its terminator removed it from RX_STREAMS); a second later
    # the slot holds nothing live on TG 91.
    slot["RX_STREAMS"].pop(bytes_4(0x80808080), None)
    with caplog.at_level(logging.INFO):
        ok = scenario.inject_hbp(
            "MASTER-A",
            DeterministicScenario.voice_burst_spec(base, seq=1, dtype_vseq=1),
            ingress_pkt_time=t0 + 1.2,
        )
    assert_inject_ok(ok)
    assert not slot.get("_suppress_uplink")
    assert_forwarded(scenario, "MASTER-B", count=1, dst_id=91)
    assert any("resuming uplink" in r.message for r in caplog.records)


def test_hbp_silent_activation_stays_while_other_caller_keeps_talking() -> None:
    """The other QSO is still live (its stream got a frame within STREAM_TO): stay muted."""
    bridges = active_routing_table(91, (("MASTER-A", 2), ("MASTER-B", 2)))
    scenario = DeterministicScenario(routing_table=bridges)
    t0 = scenario.clock.time()
    slot = _busy_slot(scenario, t0)
    base = PacketSpec(dst_id=91, stream_id=0x70707070, rf_src=2222222)

    assert_inject_ok(
        scenario.inject_hbp(
            "MASTER-A", DeterministicScenario.voice_head_spec(base), ingress_pkt_time=t0 + 0.2,
        )
    )
    # The other caller's latest frame, just before ours.
    slot["RX_STREAMS"][bytes_4(0x80808080)][4] = t0 + 1.1
    scenario.inject_hbp(
        "MASTER-A",
        DeterministicScenario.voice_burst_spec(base, seq=1, dtype_vseq=1),
        ingress_pkt_time=t0 + 1.2,
    )
    assert slot.get("_suppress_uplink") is True
    assert len(scenario.capture.for_system("MASTER-B")) == 0


def test_hbp_silent_stream_ending_after_the_qso_ends_stays_silent() -> None:
    """A terminator does not resume a silent stream: nothing to report or forward."""
    bridges = active_routing_table(91, (("MASTER-A", 2), ("MASTER-B", 2)))
    scenario = DeterministicScenario(routing_table=bridges, enable_reporting=True)
    t0 = scenario.clock.time()
    slot = _busy_slot(scenario, t0)
    base = PacketSpec(dst_id=91, stream_id=0x70707070, rf_src=2222222)
    scenario.inject_hbp("MASTER-A", DeterministicScenario.voice_head_spec(base), ingress_pkt_time=t0 + 0.2)
    assert slot.get("_suppress_uplink") is True
    slot["RX_STREAMS"].pop(bytes_4(0x80808080), None)
    scenario.inject_hbp("MASTER-A", DeterministicScenario.voice_term_spec(base), ingress_pkt_time=t0 + 1.2)
    assert [e for e in scenario.report_factory.events if "GROUP VOICE" in e] == []
    assert len(scenario.capture.for_system("MASTER-B")) == 0
