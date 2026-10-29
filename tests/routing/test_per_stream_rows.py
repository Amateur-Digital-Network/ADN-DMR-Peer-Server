# ADN DMR Peer Server - tests routing per-stream rows
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

"""Routing seams for sources with several calls per slot (``rx_row``) and concurrent targets."""

from __future__ import annotations

import dataclasses
from collections import Counter
from itertools import zip_longest
from typing import Any

import pytest
from bitarray import bitarray
from tests.harness.deterministic import (
    DeterministicScenario,
    PacketSpec,
    active_routing_table,
    add_openbridge_system,
    minimal_config,
    patch_routing_wall_time,
)
from tests.routing.unit_data_helpers import idle_hbp_slot

import adn_server.application.routing.hbp_forward as hbp_forward
from adn_server.application.server_voice import DEFAULT_SERVER_VOICE_ID
from adn_server.domain import HBPF_SLT_VHEAD, HBPF_VOICE, bytes_3, bytes_4
from adn_server.domain.dmr.bptc import decode_emblc

_ROW_PEER = 3140001  # the one peer of MASTER-A that carries several calls on slot 2
_TG1, _TG2, _TG3 = 2141, 2142, 2143
_X, _Y = 7123456, 7654321  # radios heard on MASTER-C and MASTER-D


def _scenario(
    tables: dict[int, tuple[tuple[str, int], ...]],
    systems: tuple[str, ...] = ("MASTER-A", "MASTER-B", "MASTER-C"),
    *,
    reporting: bool = False,
) -> DeterministicScenario:
    config = minimal_config(systems)
    config["SYSTEMS"]["MASTER-A"]["SINGLE_MODE"] = False  # a row source holds many talkgroups at once
    add_openbridge_system(config, "OBP-1")
    routing_table: dict[str, list[dict[str, Any]]] = {}
    for tg, entries in tables.items():
        routing_table.update(active_routing_table(tg, entries))
    return DeterministicScenario(config=config, routing_table=routing_table, enable_reporting=reporting)


def _row_spec(n: int, dst_id: int, call_type: str = "group") -> PacketSpec:
    return PacketSpec(
        peer_id=_ROW_PEER, rf_src=3140100 + n, dst_id=dst_id, stream_id=0x51000001 + n, call_type=call_type,
    )


def _call(spec: PacketSpec) -> list[PacketSpec]:
    """Header, bursts A-F and terminator: seq 0-7."""
    return [
        DeterministicScenario.voice_head_spec(spec),
        *(DeterministicScenario.voice_burst_spec(spec, seq=s, dtype_vseq=s - 1) for s in range(1, 7)),
        DeterministicScenario.voice_term_spec(spec, seq=7),
    ]


def _interleave(*calls: list[Any]) -> list[Any]:
    return [frame for frames in zip_longest(*calls) for frame in frames if frame is not None]


def _row_send(sc: DeterministicScenario, rows: dict[bytes, dict[str, Any]], spec: PacketSpec) -> bool | None:
    """One frame of MASTER-A routed the way a Tier III adapter routes it: one row per call."""
    args = spec.decoded_hbp_args()
    row = rows.setdefault(
        args["stream_id"], {"RX_PEER": args["peer_id"], "RX_LC": bytes_3(0) + args["dst_id"] + args["rf_src"]},
    )
    sc.clock.advance(0.06)
    ok = sc.routing.dmrd_received("MASTER-A", ingress_pkt_time=sc.clock.time(), rx_row=row, **args)
    if ok:
        row["RX_STREAM_ID"] = args["stream_id"]
    return ok


def _hotspot_send(sc: DeterministicScenario, system: str, spec: PacketSpec) -> bool | None:
    sc.clock.advance(0.06)
    return sc.inject_hbp(system, spec)


def _seqs(sc: DeterministicScenario, system: str, spec: PacketSpec) -> list[int]:
    sid = bytes_4(spec.stream_id)
    return [p.fields["seq"] for p in sc.capture.for_system(system) if p.fields["stream_id"] == sid]


def _events(sc: DeterministicScenario, prefix: str) -> list[list[str]]:
    return [e.split(",") for e in sc.report_factory.events if e.startswith(prefix)]


def _embedded_lc(sc: DeterministicScenario, system: str, spec: PacketSpec) -> bytes:
    """The LC that ``spec``'s bursts B-E carried to ``system`` in their embedded signalling."""
    sid = bytes_4(spec.stream_id)
    bursts = {
        p.fields["dtype_vseq"]: p.fields["dmr_payload"]
        for p in sc.capture.for_system(system)
        if p.fields["stream_id"] == sid and p.fields["frame_type"] == HBPF_VOICE
    }
    bits = bitarray(endian="big")
    for vseq in (1, 2, 3, 4):
        burst = bitarray(endian="big")
        burst.frombytes(bursts[vseq])
        bits += burst[116:148]
    return decode_emblc(bits)


def _hold_with_announcement(sc: DeterministicScenario, system: str) -> dict[str, Any]:
    slot = sc.protocols[system].STATUS[2]
    slot.update(
        TX_TYPE=HBPF_SLT_VHEAD,
        TX_RFS=bytes_3(DEFAULT_SERVER_VOICE_ID),
        TX_TGID=bytes_3(9),
        TX_STREAM_ID=bytes_4(0xA0A0A0A0),
    )
    return slot


def _two_hotspots(sc: DeterministicScenario, *peers: int) -> None:
    sc.protocols["MASTER-B"]._peers = {bytes_4(p): {"CONNECTION": "YES"} for p in peers}


@pytest.mark.behavior
def test_interleaved_streams_with_their_own_rows_are_each_forwarded_whole() -> None:
    """Three calls on one slot of one peer, interleaved frame by frame, each reach their
    MASTER and the OpenBridge whole: every frame once, none lost to another call."""
    sc = _scenario(
        {
            _TG1: (("MASTER-A", 2), ("MASTER-B", 2), ("OBP-1", 1)),
            _TG2: (("MASTER-A", 2), ("MASTER-C", 2), ("OBP-1", 1)),
            _TG3: (("MASTER-A", 2), ("MASTER-D", 2), ("OBP-1", 1)),
        },
        ("MASTER-A", "MASTER-B", "MASTER-C", "MASTER-D"),
    )
    specs = [_row_spec(n, tg) for n, tg in enumerate((_TG1, _TG2, _TG3))]
    rows: dict[bytes, dict[str, Any]] = {}
    with patch_routing_wall_time(sc.clock):
        for spec in _interleave(*map(_call, specs)):
            assert _row_send(sc, rows, spec)

    for target, spec in zip(("MASTER-B", "MASTER-C", "MASTER-D"), specs):
        assert _seqs(sc, target, spec) == list(range(8))
        assert len(sc.capture.for_system(target)) == 8
        assert _seqs(sc, "OBP-1", spec) == list(range(8))


@pytest.mark.behavior
def test_packet_control_runs_per_row() -> None:
    """A repeated frame of one call is dropped by that call's packet control, and the call
    interleaved with it keeps its own sequence."""
    sc = _scenario({_TG1: (("MASTER-A", 2), ("MASTER-B", 2)), _TG2: (("MASTER-A", 2), ("MASTER-C", 2))})
    a, b = _row_spec(0, _TG1), _row_spec(1, _TG2)
    rows: dict[bytes, dict[str, Any]] = {}
    with patch_routing_wall_time(sc.clock):
        for spec in _interleave(_call(a)[:3], _call(b)[:3]):
            assert _row_send(sc, rows, spec)
        assert not _row_send(sc, rows, _call(b)[2])
        assert _row_send(sc, rows, _call(a)[3])

    assert _seqs(sc, "MASTER-B", a) == [0, 1, 2, 3]
    assert _seqs(sc, "MASTER-C", b) == [0, 1, 2]


@pytest.mark.behavior
def test_new_stream_checks_run_once_per_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """The new-stream checks run on each call's first frame only: interleaved calls of one
    peer neither re-check nor silence each other (no self-silencing)."""
    checked: Counter[bytes] = Counter()
    real = hbp_forward.loop_guard_drops

    def spy(config: dict[str, Any], protocols: dict[str, Any], **stream: Any) -> bool:
        checked[stream["stream_id"]] += 1
        return real(config, protocols, **stream)

    monkeypatch.setattr(hbp_forward, "loop_guard_drops", spy)
    sc = _scenario({tg: (("MASTER-A", 2), ("OBP-1", 1)) for tg in (_TG1, _TG2, _TG3)})
    specs = [_row_spec(n, tg) for n, tg in enumerate((_TG1, _TG2, _TG3))]
    rows: dict[bytes, dict[str, Any]] = {}
    with patch_routing_wall_time(sc.clock):
        for spec in _interleave(*map(_call, specs)):
            assert _row_send(sc, rows, spec)

    assert checked == Counter({bytes_4(spec.stream_id): 1 for spec in specs})
    assert len(sc.capture.for_system("OBP-1")) == 24


@pytest.mark.behavior
def test_start_and_end_rx_once_per_row() -> None:
    """Each call reports START RX once, however many headers it sends, and END RX once
    with its own duration."""
    sc = _scenario({_TG1: (("MASTER-A", 2), ("OBP-1", 1)), _TG2: (("MASTER-A", 2), ("OBP-1", 1))}, reporting=True)
    a, b = _row_spec(0, _TG1), _row_spec(1, _TG2)

    def frames(spec: PacketSpec, bursts: int) -> list[PacketSpec]:
        head = DeterministicScenario.voice_head_spec(spec)
        voice = [
            DeterministicScenario.voice_burst_spec(spec, seq=s, dtype_vseq=(s - 2) % 6) for s in range(2, 2 + bursts)
        ]
        term = DeterministicScenario.voice_term_spec(spec, seq=2 + bursts)
        return [head, dataclasses.replace(head, seq=1), *voice, term]

    rows: dict[bytes, dict[str, Any]] = {}
    first: dict[str, float] = {}
    last: dict[str, float] = {}
    with patch_routing_wall_time(sc.clock):
        for spec in _interleave(frames(a, 6), frames(b, 8)):
            assert _row_send(sc, rows, spec)
            sid = str(spec.stream_id)
            first.setdefault(sid, sc.clock.time())
            last[sid] = sc.clock.time()

    starts = Counter(e[4] for e in _events(sc, "GROUP VOICE,START,RX,MASTER-A,"))
    ends = [(e[4], e[9]) for e in _events(sc, "GROUP VOICE,END,RX,MASTER-A,")]
    assert starts == Counter({sid: 1 for sid in first})
    assert sorted(ends) == sorted((sid, f"{last[sid] - first[sid]:.2f}") for sid in first)
    assert len({duration for _, duration in ends}) == 2


@pytest.mark.behavior
def test_silent_activation_suppresses_only_its_row() -> None:
    """A call keyed onto a talkgroup with a live QSO elsewhere is activated silently and its
    frames go nowhere; a call on another talkgroup from the same slot still flows."""
    sc = _scenario(
        {
            _TG1: (("MASTER-A", 2), ("MASTER-C", 2), ("OBP-1", 1)),
            _TG2: (("MASTER-A", 2), ("MASTER-B", 2), ("OBP-1", 1)),
        },
    )
    hotspot = PacketSpec(peer_id=1003, rf_src=3120003, dst_id=_TG1, stream_id=0x0C0C0C0C)
    busy, other = _row_spec(0, _TG1), _row_spec(1, _TG2)
    rows: dict[bytes, dict[str, Any]] = {}
    with patch_routing_wall_time(sc.clock):
        assert _hotspot_send(sc, "MASTER-C", DeterministicScenario.voice_head_spec(hotspot))
        for spec in _interleave(_call(busy), _call(other)):
            assert _row_send(sc, rows, spec)

    assert [p for p in sc.capture.packets if p.fields["stream_id"] == bytes_4(busy.stream_id)] == []
    assert _seqs(sc, "MASTER-B", other) == list(range(8))
    assert _seqs(sc, "OBP-1", other) == list(range(8))


@pytest.mark.behavior
def test_a_concurrent_target_takes_two_calls_on_one_slot() -> None:
    """A single-hotspot MASTER in the concurrent set takes two calls on one slot at once,
    each with its own START TX and END TX (that call's peer and duration)."""
    sc = _scenario(
        {_TG1: (("MASTER-A", 2), ("MASTER-B", 2)), _TG2: (("MASTER-C", 2), ("MASTER-B", 2))}, reporting=True,
    )
    sc.routing.set_concurrent_systems(frozenset({"MASTER-B"}))
    calls = {
        "MASTER-A": PacketSpec(peer_id=1001, rf_src=3120001, dst_id=_TG1, stream_id=0x0A0A0A0A),
        "MASTER-C": PacketSpec(peer_id=1003, rf_src=3120003, dst_id=_TG2, stream_id=0x0C0C0C0C),
    }
    times: dict[int, list[float]] = {}
    with patch_routing_wall_time(sc.clock):
        for system, spec in _interleave(*([(system, s) for s in _call(c)] for system, c in calls.items())):
            assert _hotspot_send(sc, system, spec)
            times.setdefault(spec.stream_id, []).append(sc.clock.time())

    for spec in calls.values():
        assert _seqs(sc, "MASTER-B", spec) == list(range(8))
    expected_starts = [
        f"GROUP VOICE,START,TX,MASTER-B,{c.stream_id},{c.peer_id},{c.rf_src},2,{c.dst_id},0" for c in calls.values()
    ]
    expected_ends = [
        f"GROUP VOICE,END,TX,MASTER-B,{c.stream_id},{c.peer_id},{c.rf_src},2,{c.dst_id},"
        f"{times[c.stream_id][-1] - times[c.stream_id][0]:.2f},0"
        for c in calls.values()
    ]
    events = sc.report_factory.events
    assert sorted(e for e in events if e.startswith("GROUP VOICE,START,TX,MASTER-B,")) == sorted(expected_starts)
    assert sorted(e for e in events if e.startswith("GROUP VOICE,END,TX,MASTER-B,")) == sorted(expected_ends)


@pytest.mark.behavior
def test_each_stream_keeps_its_own_lc_on_a_concurrent_target() -> None:
    """Two calls on one slot of a concurrent target each carry their own talkgroup and
    source in the embedded LC: one TX leg per stream, so the LC never mixes."""
    sc = _scenario({_TG1: (("MASTER-A", 2), ("MASTER-B", 2)), _TG2: (("MASTER-C", 2), ("MASTER-B", 2))})
    sc.routing.set_concurrent_systems(frozenset({"MASTER-B"}))
    calls = {
        "MASTER-A": PacketSpec(peer_id=1001, rf_src=3120001, dst_id=_TG1, stream_id=0x0A0A0A0A),
        "MASTER-C": PacketSpec(peer_id=1003, rf_src=3120003, dst_id=_TG2, stream_id=0x0C0C0C0C),
    }
    with patch_routing_wall_time(sc.clock):
        for system, spec in _interleave(*([(system, s) for s in _call(c)] for system, c in calls.items())):
            assert _hotspot_send(sc, system, spec)

    for spec in calls.values():
        assert _embedded_lc(sc, "MASTER-B", spec)[3:] == bytes_3(spec.dst_id) + bytes_3(spec.rf_src)


@pytest.mark.behavior
def test_a_row_source_keeps_lc_per_stream_on_a_shared_master() -> None:
    """Two calls of the row source to a MASTER shared by two hotspots each keep their own
    embedded LC there, as OpenBridge calls do."""
    sc = _scenario({_TG1: (("MASTER-A", 2), ("MASTER-B", 2)), _TG2: (("MASTER-A", 2), ("MASTER-B", 2))})
    _two_hotspots(sc, 1004, 1005)
    a, b = _row_spec(0, _TG1), _row_spec(1, _TG2)
    rows: dict[bytes, dict[str, Any]] = {}
    with patch_routing_wall_time(sc.clock):
        for spec in _interleave(_call(a), _call(b)):
            assert _row_send(sc, rows, spec)

    for spec in (a, b):
        assert _seqs(sc, "MASTER-B", spec) == list(range(8))
        assert _embedded_lc(sc, "MASTER-B", spec)[3:] == bytes_3(spec.dst_id) + bytes_3(spec.rf_src)


@pytest.mark.behavior
def test_an_announcement_does_not_hold_a_concurrent_target() -> None:
    """A server announcement on a concurrent target's slot does not keep a call off it:
    each of its peers takes every call at once."""
    sc = _scenario({_TG1: (("MASTER-A", 2), ("MASTER-B", 2))})
    sc.routing.set_concurrent_systems(frozenset({"MASTER-B"}))
    slot = _hold_with_announcement(sc, "MASTER-B")
    call = PacketSpec(peer_id=1001, rf_src=3120001, dst_id=_TG1, stream_id=0x0A0A0A0A)
    with patch_routing_wall_time(sc.clock):
        for spec in _call(call):
            slot["TX_TIME"] = sc.clock.time()
            assert _hotspot_send(sc, "MASTER-A", spec)

    assert _seqs(sc, "MASTER-B", call) == list(range(8))


@pytest.mark.behavior
def test_a_row_source_to_a_single_hotspot_master_starts_tx_once() -> None:
    """A call of the row source to a one-hotspot MASTER opens its TX once, on the call's
    first frame, not on every frame."""
    sc = _scenario({_TG1: (("MASTER-A", 2), ("MASTER-B", 2))}, reporting=True)
    a = _row_spec(0, _TG1)
    rows: dict[bytes, dict[str, Any]] = {}
    with patch_routing_wall_time(sc.clock):
        for spec in _call(a):
            assert _row_send(sc, rows, spec)

    assert _seqs(sc, "MASTER-B", a) == list(range(8))
    assert len(_events(sc, "GROUP VOICE,START,TX,MASTER-B,")) == 1


@pytest.mark.behavior
def test_private_calls_with_rows_are_not_collided() -> None:
    """Two private calls from one slot of the row source, each with its own row, are both
    delivered: the second is not dropped as colliding with the first."""
    sc = _scenario({}, ("MASTER-A", "MASTER-C", "MASTER-D"))
    sc.config["_SUB_MAP"] = {bytes_3(_X): ("MASTER-C", 2, 1000.0), bytes_3(_Y): ("MASTER-D", 2, 1000.0)}
    call_1, call_2 = _row_spec(0, _X, "unit"), _row_spec(1, _Y, "unit")

    def frames(spec: PacketSpec) -> list[PacketSpec]:
        bursts = [DeterministicScenario.unit_voice_burst_spec(spec, seq=s) for s in range(1, 4)]
        return [DeterministicScenario.unit_voice_head_spec(spec), *bursts]

    rows: dict[bytes, dict[str, Any]] = {}
    with patch_routing_wall_time(sc.clock):
        for spec in _interleave(frames(call_1), frames(call_2)):
            assert _row_send(sc, rows, spec)

    assert _seqs(sc, "MASTER-C", call_1) == [0, 1, 2, 3]
    assert _seqs(sc, "MASTER-D", call_2) == [0, 1, 2, 3]


def test_voice_burst_d_of_a_row_call_is_not_taken_for_unit_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """Burst D carries dtype 3, as a unit CSBK does: whether its stream is known is asked of the call's row."""
    sc = _scenario({}, ("MASTER-A", "MASTER-C", "MASTER-D"))
    sc.config["_SUB_MAP"] = {bytes_3(_X): ("MASTER-C", 2, 1000.0), bytes_3(_Y): ("MASTER-D", 2, 1000.0)}
    taken: list[bytes] = []
    monkeypatch.setattr(sc.routing, "_unit_data_received", lambda *args, **_k: taken.append(args[8]))
    call_1, call_2 = _row_spec(0, _X, "unit"), _row_spec(1, _Y, "unit")

    def frames(spec: PacketSpec) -> list[PacketSpec]:
        bursts = [DeterministicScenario.unit_voice_burst_spec(spec, seq=s, dtype_vseq=s - 1) for s in range(1, 6)]
        return [DeterministicScenario.unit_voice_head_spec(spec), *bursts]

    rows: dict[bytes, dict[str, Any]] = {}
    with patch_routing_wall_time(sc.clock):
        for spec in _interleave(frames(call_1), frames(call_2)):
            assert _row_send(sc, rows, spec)

    assert taken == []
    assert _seqs(sc, "MASTER-C", call_1) == [0, 1, 2, 3, 4, 5]
    assert _seqs(sc, "MASTER-D", call_2) == [0, 1, 2, 3, 4, 5]


@pytest.mark.behavior
def test_off_by_default_nothing_changes() -> None:
    """With no concurrent system and no row, a MASTER shared by two hotspots still keeps a
    call off a slot that a server announcement holds, as before."""
    sc = _scenario({_TG1: (("MASTER-A", 2), ("MASTER-B", 2))})
    _two_hotspots(sc, 1004, 1005)
    slot = _hold_with_announcement(sc, "MASTER-B")
    call = PacketSpec(peer_id=1001, rf_src=3120001, dst_id=_TG1, stream_id=0x0A0A0A0A)
    with patch_routing_wall_time(sc.clock):
        for spec in _call(call):
            slot["TX_TIME"] = sc.clock.time()
            assert _hotspot_send(sc, "MASTER-A", spec)

    assert _seqs(sc, "MASTER-B", call) == []


@pytest.mark.behavior
@pytest.mark.parametrize("target_slot", ["idle", "busy"])
def test_unit_data_to_a_radio_on_a_concurrent_target_goes_out_once(target_slot: str) -> None:
    """Unit data to a 7-digit radio goes by SUB_MAP and by the private-call path, so a hotspot may get a frame
    twice (legacy); a concurrent target gets each frame once, whichever path sends it."""
    sc = _scenario({}, ("MASTER-A", "MASTER-B"))
    sc.routing.set_concurrent_systems(frozenset({"MASTER-B"}))
    sc.config["_SUB_MAP"] = {bytes_3(_X): ("MASTER-B", 2, 1000.0)}
    if target_slot == "idle":
        sc.protocols["MASTER-B"].STATUS[2] = idle_hbp_slot()
    head = DeterministicScenario.unit_data_header_spec(
        PacketSpec(call_type="unit", dst_id=_X, stream_id=0x52525252, slot=2),
    )
    blocks = [dataclasses.replace(head, seq=n, dtype_vseq=7, payload=bytes([n]) * 33) for n in (1, 2)]

    for spec in (head, *blocks):
        sc.clock.advance(0.06)
        sc.inject_unit("MASTER-A", spec)

    assert _seqs(sc, "MASTER-B", head) == [0, 1, 2]
