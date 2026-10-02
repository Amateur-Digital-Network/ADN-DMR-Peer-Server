# ADN DMR Peer Server - tests routing loop guard
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

"""Loop guard: a caller's echo on the same TG from another ingress (transcoding bridge loop)."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from tests.harness.deterministic import (
    DeterministicScenario,
    PacketSpec,
    active_routing_table,
    add_openbridge_system,
    minimal_config,
    parse_dmr_fields,
    patch_routing_wall_time,
)

from adn_server.application.routing.helpers import hbp_master_ingress_repeat_allowed
from adn_server.application.routing.loop_guard import (
    LoopGuard,
    loop_guard,
    loop_guard_mode,
)
from adn_server.domain import bytes_3, bytes_4

_CALLER = 2130035
_TG = 91
_ORIGINAL = 0x0A000001
_ECHO = 0x0B000002


def _scenario(*, guard: Any = "log", tgs: tuple[int, ...] = (_TG,)) -> DeterministicScenario:
    config = minimal_config(("MASTER-A", "BRIDGE-B", "MASTER-C"))
    config["SYSTEMS"]["BRIDGE-B"]["LOOP_GUARD"] = guard
    scenario = DeterministicScenario(config=config)
    for tg in tgs:
        scenario.seed_routing_table(
            active_routing_table(tg, (("MASTER-A", 2), ("BRIDGE-B", 2), ("MASTER-C", 2)))
        )
    return scenario


def _inject(scenario: DeterministicScenario, system: str, spec: PacketSpec, at: float) -> bool:
    """Like udp_hbp: the slot only binds the stream when routing accepted the frame."""
    scenario.clock.advance(max(0.0, at - scenario.clock.time()))
    args = spec.decoded_hbp_args()
    ok = scenario.routing.dmrd_received(system, ingress_pkt_time=at, **args)
    if ok:
        scenario._sync_hbp_slot(system, args)
    return bool(ok)


def _over(scenario: DeterministicScenario, system: str, stream: int, at: float, *, tg: int = _TG,
          rf_src: int = _CALLER, peer: int = 1001) -> None:
    base = PacketSpec(dst_id=tg, stream_id=stream, rf_src=rf_src, peer_id=peer)
    _inject(scenario, system, DeterministicScenario.voice_head_spec(base), at)
    _inject(scenario, system, DeterministicScenario.voice_burst_spec(base, seq=1, dtype_vseq=1), at + 0.06)


def _routed(scenario: DeterministicScenario) -> set[int]:
    """Stream IDs routing sent anywhere (a busy target slot may refuse one, that is not the guard)."""
    return {
        int.from_bytes(parse_dmr_fields(p.packet)["stream_id"], "big")
        for p in scenario.capture.packets
    }


def _loop_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if "*LoopGuard*" in r.getMessage()]


def test_mode_parsing() -> None:
    assert loop_guard_mode({}) == "log"
    assert loop_guard_mode(None) == "log"
    assert loop_guard_mode({"LOOP_GUARD": "log"}) == "log"
    assert loop_guard_mode({"LOOP_GUARD": True}) == "drop"
    assert loop_guard_mode({"LOOP_GUARD": False}) == "off"


def test_echo_on_same_tg_from_another_ingress_is_dropped(caplog: pytest.LogCaptureFixture) -> None:
    scenario = _scenario(guard=True)
    t0 = scenario.clock.time()
    with caplog.at_level(logging.WARNING):
        _over(scenario, "MASTER-A", _ORIGINAL, t0)
        _over(scenario, "BRIDGE-B", _ECHO, t0 + 0.5)  # comes back through the bridge

    assert _routed(scenario) == {_ORIGINAL}, "the echo must not reach anyone"
    lines = _loop_lines(caplog)
    assert len(lines) == 1, "one line per echo stream, not per frame"
    assert "dropped" in lines[0] and f"SUB {_CALLER}" in lines[0] and f"TG {_TG}" in lines[0]


def test_log_mode_is_the_default_and_only_logs(caplog: pytest.LogCaptureFixture) -> None:
    scenario = _scenario(guard="log")
    del scenario.config["SYSTEMS"]["BRIDGE-B"]["LOOP_GUARD"]  # default
    t0 = scenario.clock.time()
    with caplog.at_level(logging.WARNING):
        _over(scenario, "MASTER-A", _ORIGINAL, t0)
        _over(scenario, "BRIDGE-B", _ECHO, t0 + 0.5)

    assert _routed(scenario) == {_ORIGINAL, _ECHO}, "log mode drops nothing"
    lines = _loop_lines(caplog)
    assert len(lines) == 1 and "logged only" in lines[0]


def test_bridge_to_another_tg_is_not_a_loop(caplog: pytest.LogCaptureFixture) -> None:
    """A bridge relaying TG 91 to TG 92 with the caller's ID is legitimate."""
    scenario = _scenario(guard=True, tgs=(_TG, 92))
    t0 = scenario.clock.time()
    with caplog.at_level(logging.WARNING):
        _over(scenario, "MASTER-A", _ORIGINAL, t0)
        _over(scenario, "BRIDGE-B", _ECHO, t0 + 0.5, tg=92)

    assert _ECHO in _routed(scenario)
    assert _loop_lines(caplog) == []


def test_next_over_from_the_same_ingress_is_not_an_echo(caplog: pytest.LogCaptureFixture) -> None:
    scenario = _scenario(guard=True)
    t0 = scenario.clock.time()
    with caplog.at_level(logging.WARNING):
        _over(scenario, "BRIDGE-B", _ORIGINAL, t0)
        _over(scenario, "BRIDGE-B", _ECHO, t0 + 0.5)

    assert _loop_lines(caplog) == []


@pytest.mark.parametrize(("gap", "echo"), [(0.5, True), (1.5, False)])
def test_hold_window_after_the_original_ends(caplog: pytest.LogCaptureFixture, gap: float, echo: bool) -> None:
    scenario = _scenario(guard=True)
    t0 = scenario.clock.time()
    with caplog.at_level(logging.WARNING):
        _over(scenario, "MASTER-A", _ORIGINAL, t0)  # last frame at t0 + 0.06
        _over(scenario, "BRIDGE-B", _ECHO, t0 + 0.06 + gap)

    assert bool(_loop_lines(caplog)) is echo
    assert (_ECHO in _routed(scenario)) is not echo


def test_parrot_replay_is_never_an_echo_whatever_its_name(caplog: pytest.LogCaptureFixture) -> None:
    """The parrot replays 2.0 s after the over ends; the hold (< 2 s) keeps it out on its own,
    even with the guard dropping on that system and no LOOP_GUARD: false."""
    scenario = _scenario(guard=True)  # BRIDGE-B plays the parrot here, under any name
    t0 = scenario.clock.time()
    with caplog.at_level(logging.WARNING):
        _over(scenario, "MASTER-A", _ORIGINAL, t0)  # last frame at t0 + 0.06
        _over(scenario, "BRIDGE-B", _ECHO, t0 + 0.06 + 2.0)

    assert _loop_lines(caplog) == []
    assert _ECHO in _routed(scenario)


def test_server_voice_ids_are_exempt(caplog: pytest.LogCaptureFixture) -> None:
    """Several servers run beacons as 1000001 at the same time."""
    scenario = _scenario(guard=True)
    t0 = scenario.clock.time()
    with caplog.at_level(logging.WARNING):
        _over(scenario, "MASTER-A", _ORIGINAL, t0, rf_src=1000001)
        _over(scenario, "BRIDGE-B", _ECHO, t0 + 0.5, rf_src=1000001)

    assert _loop_lines(caplog) == []
    assert _ECHO in _routed(scenario)


def test_loop_guard_false_neither_checks_nor_records(caplog: pytest.LogCaptureFixture) -> None:
    """The parrot replays each over with the caller's ID about 2 s after it ends."""
    scenario = _scenario(guard=True)
    scenario.config["SYSTEMS"]["MASTER-C"]["LOOP_GUARD"] = False  # the parrot
    t0 = scenario.clock.time()
    with caplog.at_level(logging.WARNING):
        _over(scenario, "MASTER-A", _ORIGINAL, t0)
        _over(scenario, "MASTER-C", _ECHO, t0 + 0.5)  # inside the hold: not checked either
        # the caller keys up again while the replay is still on: not an echo of the replay
        _over(scenario, "MASTER-A", 0x0C000003, t0 + 0.7)

    assert _loop_lines(caplog) == []


def test_obp_echo_is_dropped_but_obp_multipath_is_not(caplog: pytest.LogCaptureFixture) -> None:
    config = minimal_config(("MASTER-A", "MASTER-C"))
    add_openbridge_system(config, "OBP-1")
    add_openbridge_system(config, "OBP-2")
    config["SYSTEMS"]["OBP-1"]["LOOP_GUARD"] = True
    config["SYSTEMS"]["OBP-2"]["LOOP_GUARD"] = True
    scenario = DeterministicScenario(config=config)
    scenario.seed_routing_table(active_routing_table(_TG, (("MASTER-A", 2), ("MASTER-C", 2), ("OBP-1", 1))))
    t0 = scenario.clock.time()
    with caplog.at_level(logging.WARNING):
        # a call from another server arrives on two OBP paths with the same stream ID
        with patch_routing_wall_time(scenario.clock):
            for obp in ("OBP-1", "OBP-2"):
                scenario.inject_obp(obp, DeterministicScenario.voice_head_spec(
                    PacketSpec(dst_id=_TG, stream_id=_ORIGINAL, rf_src=_CALLER, slot=1)))
        assert _loop_lines(caplog) == [], "OBP multipath keeps the stream ID: not an echo"
        # a local caller on MASTER-A comes back over OBP-1 with a new stream ID
        _over(scenario, "MASTER-A", 0x0D000004, t0 + 3.0, rf_src=7300001)
        scenario.clock.advance(0.5)
        with patch_routing_wall_time(scenario.clock):
            scenario.inject_obp("OBP-1", DeterministicScenario.voice_head_spec(
                PacketSpec(dst_id=_TG, stream_id=_ECHO, rf_src=7300001, slot=1)))

    lines = _loop_lines(caplog)
    assert len(lines) == 1 and "dropped" in lines[0]
    assert _ECHO not in _routed(scenario)


def test_master_repeat_does_not_fan_out_a_dropped_echo() -> None:
    """MASTER REPEAT decides before routing: it must follow the same verdict."""
    scenario = _scenario(guard=True)
    t0 = scenario.clock.time()
    _over(scenario, "MASTER-A", _ORIGINAL, t0)
    kwargs = dict(
        protocols=scenario.protocols, systems_cfg=scenario.config["SYSTEMS"],
        system_cfg=scenario.config["SYSTEMS"]["BRIDGE-B"], config=scenario.config,
        system_name="BRIDGE-B", slot=2,
    )
    assert not hbp_master_ingress_repeat_allowed(
        {}, bytes_4(1001), bytes_3(_CALLER), bytes_3(_TG), bytes_4(_ECHO), t0 + 0.5, **kwargs,
    )
    # routing then gets the same (cached) verdict
    assert not _inject(scenario, "BRIDGE-B", DeterministicScenario.voice_head_spec(
        PacketSpec(dst_id=_TG, stream_id=_ECHO, rf_src=_CALLER)), t0 + 0.5)


def test_trim_forgets_old_streams() -> None:
    guard = LoopGuard()
    config: dict[str, Any] = {"SYSTEMS": {"A": {"MODE": "MASTER"}}, "GLOBAL": {}}
    guard.check(config, {}, system_name="A", peer_id=bytes_4(1), slot=2, rf_src=bytes_3(_CALLER),
                dst_id=bytes_3(_TG), stream_id=bytes_4(_ORIGINAL), pkt_time=1000.0)
    guard.trim(1200.0)
    assert guard._legs and guard._verdicts
    guard.trim(1400.0)
    assert not guard._legs and not guard._verdicts


def test_one_guard_per_config() -> None:
    config: dict[str, Any] = {}
    assert loop_guard(config) is loop_guard(config)
