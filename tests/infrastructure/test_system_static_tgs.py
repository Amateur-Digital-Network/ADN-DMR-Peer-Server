# ADN DMR Peer Server - tests infrastructure TS1_STATIC / TS2_STATIC of a MASTER
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

"""TS1_STATIC / TS2_STATIC in adn-server.yaml only reached a MASTER's hotspots when one was connected."""

from __future__ import annotations

from tests.harness.deterministic import DeterministicScenario, PacketSpec, minimal_config
from tests.support.hbp_repeat_stack import build_hbp_repeat_stack

from adn_server.application.report.payloads import system_static_tgs
from adn_server.application.routing.peer_downlink_index import invalidate_peer_options_cache
from adn_server.domain import bytes_4
from adn_server.infrastructure.config_normalizer import ensure_system_runtime_config
from adn_server.infrastructure.hbp_constants import DMRD, RPTC, RPTK, RPTL
from adn_server.infrastructure.twisted_adapters.udp_hbp import _calc_hash, _get_passphrase_bytes

_A, _B, _C = bytes_4(730044401), bytes_4(730044402), bytes_4(730044403)
_ADDR = {_A: ("10.0.0.1", 62001), _B: ("10.0.0.2", 62002), _C: ("10.0.0.3", 62003)}
_STATIC_TG = 730777


def _stack(ts2_static: str = str(_STATIC_TG), other_master_static: str = "", ts1_static: str = ""):
    stack = build_hbp_repeat_stack(talker_alias=False, system_name="MASTER-A")
    stack.config["PROXY"] = {"TARGET_SYSTEM": "MASTER-A"}
    stack.hbp._CONFIG = stack.config
    stack.config["SYSTEMS"]["MASTER-A"].update(TS1_STATIC=ts1_static, TS2_STATIC=ts2_static, PASSPHRASE="passw0rd")
    if other_master_static:
        stack.config["SYSTEMS"]["MASTER-B"] = {"MODE": "MASTER", "TS2_STATIC": other_master_static}
    ensure_system_runtime_config(stack.config)
    return stack


def _login(stack, peer_id: bytes, options: str | None = None) -> None:
    addr = _ADDR[peer_id]
    stack.hbp.datagramReceived(RPTL + peer_id, addr)
    salt = bytes_4(stack.hbp._peers[peer_id]["SALT"])
    stack.hbp.datagramReceived(RPTK + peer_id + _calc_hash(salt, _get_passphrase_bytes(stack.hbp._config)), addr)
    stack.hbp.datagramReceived(RPTC + peer_id + b"CE5RPY  " + b"\x00" * 85 + b"4", addr)
    peer = stack.hbp._peers[peer_id]
    assert peer["CONNECTION"] == "YES"
    if options is not None:
        peer["OPTIONS"] = options.encode()
        invalidate_peer_options_cache(peer)
    stack.hbp._mark_downlink_index_dirty()


def _heard(stack, tg: int, slot: int = 2) -> set[bytes]:
    stack.transport.clear()
    spec = PacketSpec(peer_id=714009901, rf_src=7140099, dst_id=tg, slot=slot, stream_id=0x0D0D0D0D, payload=b"\x00" * 33)
    stack.hbp.send_peers(DeterministicScenario.voice_burst_spec(spec, seq=1, dtype_vseq=1).data())
    return {p for p, addr in _ADDR.items() if any(pkt[:4] == DMRD for pkt in stack.transport.for_addr(addr))}


def test_a_yaml_static_tg_reaches_every_hotspot_of_its_master() -> None:
    stack = _stack()
    _login(stack, _A, "TS2=91;")
    _login(stack, _B)
    _login(stack, _C, "TS2=730778;")
    assert _heard(stack, _STATIC_TG) == {_A, _B, _C}


def test_a_yaml_ts1_static_tg_reaches_every_hotspot_on_ts1() -> None:
    stack = _stack(ts2_static="", ts1_static="730555")
    _login(stack, _A, "TS2=91;")
    _login(stack, _B)
    assert _heard(stack, 730555, slot=1) == {_A, _B}


def test_each_hotspots_own_options_still_apply() -> None:
    stack = _stack()
    _login(stack, _A, "TS2=91;")
    _login(stack, _B)
    _login(stack, _C, "TS2=730778;")
    assert _heard(stack, 730778) == {_C}
    assert _heard(stack, 91) == {_A}


def test_another_masters_static_tgs_stay_on_that_master() -> None:
    stack = _stack(ts2_static="", other_master_static=str(_STATIC_TG))
    _login(stack, _A, "TS2=91;")
    _login(stack, _B)
    assert _heard(stack, _STATIC_TG) == set()


def test_a_reload_gives_connected_hotspots_the_new_static_tgs() -> None:
    stack = _stack(ts2_static="")
    _login(stack, _A, "TS2=91;")
    _login(stack, _B)
    assert _heard(stack, _STATIC_TG) == set()
    stack.config["SYSTEMS"]["MASTER-A"]["TS2_STATIC"] = str(_STATIC_TG)
    ensure_system_runtime_config(stack.config)
    stack.hbp.apply_system_config(stack.config)
    assert _heard(stack, _STATIC_TG) == {_A, _B}


def test_service_tgs_are_never_static_for_every_hotspot() -> None:
    assert system_static_tgs({"TS1_STATIC": "9990,730, 214", "TS2_STATIC": 9}) == (("730", "214"), ())


def test_the_master_keeps_its_yaml_static_bridge_leg_after_hotspots_rewrite_ts2_static() -> None:
    config = minimal_config(("SYSTEM",))
    config["SYSTEMS"]["SYSTEM"].update(TS1_STATIC="730555", TS2_STATIC=str(_STATIC_TG))
    ensure_system_runtime_config(config)
    config["SYSTEMS"]["SYSTEM"].update(TS1_STATIC="", TS2_STATIC="91")  # union of the hotspots' OPTIONS, as options_config writes it
    sc = DeterministicScenario(config=config)
    sc.routing.apply_startup_subscriptions()
    table = sc.routing.routing_table_for_report()
    for tg, ts in ((_STATIC_TG, 2), (730555, 1)):
        legs = table.get(str(tg), [])
        assert any(leg.get("SYSTEM") == "SYSTEM" and leg.get("TS") == ts and leg.get("ACTIVE") for leg in legs), tg


def test_the_monitor_gets_the_masters_yaml_statics_on_both_slots_as_each_hotspots_own() -> None:
    from adn_server.application.report.payloads import build_topology

    stack = _stack(ts1_static="730555")
    _login(stack, _A, "TS2=91;VOICE=0;TIMER=300;")
    _login(stack, _B)
    stack.config["SYSTEMS"]["MASTER-A"].update(TS1_STATIC="", TS2_STATIC="91")  # as options_config rewrites it
    rows = {p["id"]: p for s in build_topology(stack.config["SYSTEMS"], seq=1)["systems"] for p in s["peers"]}
    assert rows[730044401]["ts1_static"] == ["730555"]
    assert rows[730044401]["ts2_static"] == [str(_STATIC_TG), "91"]  # the yaml ones first
    # the monitor builds its table from the OPTIONS text: it carries every slot the hotspot gets
    assert rows[730044401]["options"] == f"TS1=730555;TS2={_STATIC_TG},91;VOICE=0;TIMER=300;"
    assert "730555" in rows[730044402]["ts1_static"] and str(_STATIC_TG) in rows[730044402]["ts2_static"]
