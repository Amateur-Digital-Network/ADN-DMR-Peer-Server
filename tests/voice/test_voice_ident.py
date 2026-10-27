# ADN DMR Peer Server - tests voice ident per hotspot
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

"""Voice ident on a MASTER several hotspots share (inject-only proxy, MAX_PEERS > 1).

VOICE_IDENT in the YAML allows it; each hotspot whose OPTIONS say VOICE=1 (or IDENT=1) gets
its own ident, with its callsign, LANG and VOICETG, to that hotspot only, once it has been
quiet for 30 s. A hotspot's OPTIONS never change the MASTER's VOICE_IDENT for the others.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from unittest.mock import patch

from tests.harness.deterministic import DeterministicScenario, PacketSpec
from tests.support.hbp_repeat_stack import build_hbp_repeat_stack

from adn_server.application.ident_use_cases import IdentUseCases
from adn_server.application.routing.downlink import PEER_STREAM_END
from adn_server.domain import bytes_3, bytes_4

_SYSTEM = "MASTER-A"
_A = bytes_4(213003507)
_B = bytes_4(213003591)
_NOW = 1_800_000_000.0
_ALL_CALL = 16777215


class _Voice:
    """Words whose frames say which language and TG they were made for."""

    def __init__(self, languages: tuple[str, ...] = ("en_GB", "el_GR", "es_ES")) -> None:
        self._languages = languages
        self.to_peer: list[tuple[bytes, int, list[Any]]] = []
        self.on_slot: list[bytes] = []

    def get_ambe_words(self, lang: str, _path: str) -> dict[str, dict[str, Any]]:
        if lang not in self._languages:
            return {}
        return {lang: {"silence": "_", "this-is": f"this-is:{lang}", "adn": "adn", "C": "C", "E": "E", "5": "5"}}

    def pkt_gen(self, _src: bytes, dst: bytes, _peer: bytes, _slot: int, say: list[Any]) -> list[Any]:
        return [(int.from_bytes(dst, "big"), word) for word in say]

    def play_to_peer(self, _system: str, peer_id: bytes, slot: int, frames: list[Any], _what: str) -> bool:
        self.to_peer.append((peer_id, slot, frames))
        return True

    def play_on_slot(self, _protocol: Any, _system: str, _speech: Any, _src: bytes, dst: bytes) -> int:
        self.on_slot.append(dst)
        return 1


class _Master:
    def __init__(self, peers: dict[bytes, dict[str, Any]], voice_slots: dict[bytes, Any] | None = None) -> None:
        self._peers = peers
        self._peer_voice_slots = voice_slots or {}


def _peer(options: str | None, *, callsign: bytes = b"CE5RPY  ", quiet_s: float = 60.0) -> dict[str, Any]:
    peer: dict[str, Any] = {"CONNECTION": "YES", "CALLSIGN": callsign, PEER_STREAM_END: _NOW - quiet_s}
    if options is not None:
        peer["OPTIONS"] = options.encode()
    return peer


def _run(peers: dict[bytes, dict[str, Any]], *, voice_ident: Any = True, proxy: bool = True,
         max_peers: int = 102, languages: str = "", voice_slots: dict[bytes, Any] | None = None,
         **sys_extra: Any) -> _Voice:
    voice = _Voice()
    sys_cfg = {"MODE": "MASTER", "VOICE_IDENT": voice_ident, "MAX_PEERS": max_peers,
               "ANNOUNCEMENT_LANGUAGE": "en_GB", "OVERRIDE_IDENT_TG": "", **sys_extra}
    config: dict[str, Any] = {"GLOBAL": {"SERVER_ID": 2141}, "SYSTEMS": {_SYSTEM: sys_cfg},
                              "VOICE": {"ANNOUNCEMENT_LANGUAGES": languages}}
    if proxy:
        config["PROXY"] = {"TARGET_SYSTEM": _SYSTEM}
    ident = IdentUseCases(config, voice, "/audio", get_protocols=lambda: {_SYSTEM: _Master(peers, voice_slots)},
                          call_from_reactor=lambda fn, *a: fn(*a))
    with patch("adn_server.application.ident_use_cases.time.time", return_value=_NOW):
        ident.run_ident()
    return voice


def _spoken(frames: list[Any]) -> list[str]:
    return [word for _tg, word in frames if word != "_"]


def test_only_the_hotspots_that_ask_get_an_ident_each_to_itself() -> None:
    voice = _run({_A: _peer("TS2=730;VOICE=1;"), _B: _peer("TS2=730;")})
    assert [(peer, slot) for peer, slot, _f in voice.to_peer] == [(_A, 2)]
    assert _spoken(voice.to_peer[0][2]) == ["this-is:en_GB", "C", "E", "5", "adn"]  # CE5RPY: R, P, Y have no word here
    assert voice.on_slot == []  # nothing goes out on the shared MASTER's slot


def test_ident_1_asks_like_voice_1_and_voice_0_does_not() -> None:
    voice = _run({_A: _peer("IDENT=1;"), _B: _peer("VOICE=0;")})
    assert [peer for peer, _s, _f in voice.to_peer] == [_A]


def test_the_yaml_must_allow_it() -> None:
    assert _run({_A: _peer("VOICE=1;")}, voice_ident=False).to_peer == []


def test_a_hotspot_whose_last_stream_ended_in_the_last_30_s_is_left_alone() -> None:
    voice = _run({_A: _peer("VOICE=1;", quiet_s=30.0), _B: _peer("VOICE=1;", quiet_s=31.0)})
    assert [peer for peer, _s, _f in voice.to_peer] == [_B]


def test_a_hotspot_with_a_stream_still_going_is_left_alone() -> None:
    slots = {_A: {1: {"stream_id": b"s", "tgid": 730, "time": _NOW - 30.0}},
             _B: {2: {"stream_id": b"t", "tgid": 214, "time": _NOW - 31.0}}}  # B's stream was lost without its end
    voice = _run({_A: _peer("VOICE=1;"), _B: _peer("VOICE=1;")}, voice_slots=slots)
    assert [peer for peer, _s, _f in voice.to_peer] == [_B]


def test_a_hotspot_never_heard_is_quiet() -> None:
    peer = _peer("VOICE=1;")
    del peer[PEER_STREAM_END]
    assert len(_run({_A: peer}).to_peer) == 1


def test_each_hotspot_in_its_own_language_else_the_masters() -> None:
    voice = _run({_A: _peer("VOICE=1;LANG=el_GR;"), _B: _peer("VOICE=1;")})
    assert {peer: _spoken(frames)[0] for peer, _s, frames in voice.to_peer} == {
        _A: "this-is:el_GR", _B: "this-is:en_GB",
    }


def test_a_language_without_words_is_skipped() -> None:
    assert _run({_A: _peer("VOICE=1;LANG=xx_XX;")}).to_peer == []


def test_announcement_languages_limits_the_languages() -> None:
    voice = _run({_A: _peer("VOICE=1;LANG=el_GR;"), _B: _peer("VOICE=1;")}, languages="es_ES, en_GB")
    assert [peer for peer, _s, _f in voice.to_peer] == [_B]


def test_the_tg_is_the_hotspots_voicetg_else_the_yaml_override_else_all_call() -> None:
    voice = _run({_A: _peer("VOICE=1;VOICETG=9;"), _B: _peer("VOICE=1;")}, OVERRIDE_IDENT_TG=91)
    assert {peer: frames[0][0] for peer, _s, frames in voice.to_peer} == {_A: 9, _B: 91}
    voice = _run({_A: _peer("VOICE=1;IDENTTG=214;"), _B: _peer("VOICE=1;")})
    assert {peer: frames[0][0] for peer, _s, frames in voice.to_peer} == {_A: 214, _B: _ALL_CALL}


def test_a_hotspot_without_callsign_or_not_connected_is_skipped() -> None:
    gone = _peer("VOICE=1;")
    gone["CONNECTION"] = "RPTL-RECEIVED"
    assert _run({_A: _peer("VOICE=1;", callsign=b"        "), _B: gone}).to_peer == []


def test_max_peers_above_one_shares_the_master_without_the_proxy() -> None:
    assert len(_run({_A: _peer("VOICE=1;")}, proxy=False, max_peers=2).to_peer) == 1


def test_a_master_with_one_hotspot_still_idents_on_its_slot_with_an_empty_override() -> None:
    """``OVERRIDE_IDENT_TG: ""`` (as the example YAML ships) means all-call, not an error."""
    voice = _Voice()
    master = _Master({})
    master.STATUS = {2: {"RX_TYPE": 2, "TX_TYPE": 2, "RX_TIME": 0, "TX_TIME": 0}}  # type: ignore[attr-defined]
    config: dict[str, Any] = {"GLOBAL": {}, "SYSTEMS": {_SYSTEM: {
        "MODE": "MASTER", "VOICE_IDENT": True, "MAX_PEERS": 1, "ANNOUNCEMENT_LANGUAGE": "en_GB",
        "OVERRIDE_IDENT_TG": "", "PEERS": {_A: {"CALLSIGN": b"CE5RPY  "}},
    }}}
    ident = IdentUseCases(config, voice, "/audio", get_protocols=lambda: {_SYSTEM: master},
                          call_from_reactor=lambda fn, *a: fn(*a))
    with patch("adn_server.application.ident_use_cases.time.sleep"):
        ident.run_ident()
    assert voice.on_slot == [bytes_3(_ALL_CALL)]
    assert voice.to_peer == []


# --- a hotspot's OPTIONS on a shared MASTER ------------------------------------------------------

def _options_scenario(*, shared: bool) -> DeterministicScenario:
    config = DeterministicScenario().config
    sys_cfg = config["SYSTEMS"][_SYSTEM]
    sys_cfg.update({"VOICE_IDENT": True, "OVERRIDE_IDENT_TG": 91, "TS1_STATIC": "", "TS2_STATIC": ""})
    sys_cfg.pop("OPTIONS", None)
    if shared:
        config["PROXY"] = {"TARGET_SYSTEM": _SYSTEM}
    return DeterministicScenario(config=config, routing_table={})


def test_on_a_shared_master_a_hotspots_options_leave_the_yaml_permission_alone() -> None:
    scenario = _options_scenario(shared=True)
    scenario.routing.options_config_for_system(_SYSTEM, peer_options=b"VOICE=0;VOICETG=9;")
    sys_cfg = scenario.config["SYSTEMS"][_SYSTEM]
    assert (sys_cfg["VOICE_IDENT"], sys_cfg["OVERRIDE_IDENT_TG"]) == (True, 91)


def test_on_a_master_of_its_own_the_hotspots_options_still_override_the_yaml() -> None:
    scenario = _options_scenario(shared=False)
    scenario.routing.options_config_for_system(_SYSTEM, peer_options=b"VOICE=0;VOICETG=9;")
    sys_cfg = scenario.config["SYSTEMS"][_SYSTEM]
    assert (sys_cfg["VOICE_IDENT"], sys_cfg["OVERRIDE_IDENT_TG"]) == (False, 9)


# --- when a hotspot was last on the air -----------------------------------------------------------

def _frame(dtype_vseq: int, *, frame_type: int = 2, dst: int = 730, peer: bytes = _A, stream: int = 0x01010101) -> bytes:
    return replace(
        PacketSpec(dst_id=dst, slot=2, stream_id=stream, peer_id=int.from_bytes(peer, "big"), rf_src=7300001),
        frame_type=frame_type, dtype_vseq=dtype_vseq,
    ).data()


def test_on_the_real_master_a_hotspot_is_identified_30_s_after_its_qso() -> None:
    stack = build_hbp_repeat_stack(talker_alias=False, system_name=_SYSTEM)
    stack.config["PROXY"] = {"TARGET_SYSTEM": _SYSTEM}
    stack.config["SYSTEMS"][_SYSTEM].update({"VOICE_IDENT": True, "ANNOUNCEMENT_LANGUAGE": "en_GB"})
    stack.register_peer(_A, ("10.0.0.7", 62031), options="TS2=730;VOICE=1;")
    voice = _Voice()
    ident = IdentUseCases(stack.config, voice, "/audio", get_protocols=lambda: {_SYSTEM: stack.hbp},
                          call_from_reactor=lambda fn, *a: fn(*a))

    def ident_at(t: float) -> int:
        voice.to_peer.clear()
        with patch("adn_server.application.ident_use_cases.time.time", return_value=t):
            ident.run_ident()
        return len(voice.to_peer)

    clock = {"t": _NOW}
    with patch("adn_server.infrastructure.twisted_adapters.udp_hbp.time.time", side_effect=lambda: clock["t"]):
        stack.hbp._master_datagram_received(_frame(1), ("10.0.0.7", 62031))  # A keys up
        clock["t"] = _NOW + 40
        stack.hbp._master_datagram_received(_frame(3, frame_type=0), ("10.0.0.7", 62031))  # still talking 40 s later
        assert ident_at(_NOW + 60) == 0  # the over is still on
        clock["t"] = _NOW + 70
        stack.hbp._master_datagram_received(_frame(2), ("10.0.0.7", 62031))  # A unkeys
    assert ident_at(_NOW + 100) == 0
    assert ident_at(_NOW + 101) == 1


def test_hearing_a_stream_end_marks_the_hotspot_too() -> None:
    stack = build_hbp_repeat_stack(talker_alias=False, system_name=_SYSTEM)
    stack.register_peer(_B, ("10.0.0.8", 62031))
    with patch("adn_server.application.routing.downlink.time.time", return_value=_NOW):
        assert stack.hbp.send_prompt_to_peer(_B, _frame(2, dst=4000, peer=bytes_4(9), stream=0x0A0A0A0A))
    assert stack.hbp._peers[_B][PEER_STREAM_END] == _NOW


# --- VoiceUseCases.play_to_peer ---------------------------------------------------------------------

def test_play_to_peer_starts_at_once_and_reaches_only_that_hotspot() -> None:
    from adn_server.application.voice_use_cases import VoiceUseCases

    sent: list[bytes] = []
    delays: list[float] = []
    queue: list[tuple[Any, tuple]] = []

    class _Hbp:
        def send_prompt_to_peer(self, peer_id: bytes, _pkt: bytes) -> bool:
            sent.append(peer_id)
            return True

    def call_later(delay: float, fn: Any, *a: Any) -> None:
        delays.append(delay)
        queue.append((fn, a))

    voice = VoiceUseCases(None, {}, get_protocols=lambda: {_SYSTEM: _Hbp()},  # type: ignore[arg-type]
                          call_from_reactor=lambda fn, *a: queue.append((fn, a)), call_later=call_later)
    assert voice.play_to_peer(_SYSTEM, _A, 2, [b"f1", b"f2"], "voice ident")
    while queue:
        fn, a = queue.pop(0)
        fn(*a)
    assert sent == [_A, _A]
    assert delays[0] == 0.0  # the hotspot is already known to be quiet
    assert not voice.play_to_peer("OBP-1", _A, 2, [b"f1"], "voice ident")  # no such system
