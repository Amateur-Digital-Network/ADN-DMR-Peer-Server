# ADN DMR Peer Server - tests hbp master replies
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

"""Every reply a MASTER sends a peer takes its opcode from REPLY_ACK/NAK/PONG/CLOSE: classic names by default."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from adn_server.domain.value_objects import bytes_4
from adn_server.infrastructure.config_normalizer import ensure_system_runtime_config
from adn_server.infrastructure.hbp_constants import (
    MSTCL,
    MSTNAK,
    MSTPONG,
    RPTACK,
    RPTC,
    RPTCL,
    RPTK,
    RPTL,
    RPTO,
    RPTPING,
)
from adn_server.infrastructure.twisted_adapters.udp_hbp import HBPProtocol, _calc_hash

_PEER = bytes_4(1234567)
_ADDR = ("192.168.1.50", 62031)
_OTHER = bytes_4(1234568)
_DENIED = bytes_4(1234569)
_PASS = b"test-passphrase"


class _Renamed(HBPProtocol):
    REPLY_ACK, REPLY_NAK, REPLY_PONG, REPLY_CLOSE = b"XXXACK", b"XXXNAK", b"XXXPONG", b"XXXCL"


_NAMES = {
    HBPProtocol: {"ACK": RPTACK, "NAK": MSTNAK, "PONG": MSTPONG, "CLOSE": MSTCL},
    _Renamed: {"ACK": b"XXXACK", "NAK": b"XXXNAK", "PONG": b"XXXPONG", "CLOSE": b"XXXCL"},
}


class _Transport:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def write(self, data: bytes, _addr: tuple[str, int]) -> None:
        self.sent.append(data)


class _Router:
    def acl_check(self, value: bytes, _acl: object) -> bool:
        return value != _DENIED


def _master(cls: type[HBPProtocol], *, own: bytes | None = None, **sys: Any) -> HBPProtocol:
    config: dict[str, Any] = {
        "GLOBAL": {"PING_TIME": 10, "MAX_MISSED": 3, "USE_ACL": False},
        "_SUB_IDS": {int.from_bytes(_PEER, "big"): "CE1TEST"},
        "SYSTEMS": {"HOTSPOT": {"MODE": "MASTER", "ENABLED": True, "MAX_PEERS": 8, "PASSPHRASE": _PASS, **sys}},
    }
    ensure_system_runtime_config(config)
    hbp = cls("HOTSPOT", config, router=_Router(), get_user_password_callback=lambda _id: own)  # type: ignore[arg-type]
    hbp.transport = _Transport()  # type: ignore[assignment]
    return hbp


def _rptl(hbp: HBPProtocol, peer: bytes = _PEER) -> bytes:
    hbp.datagramReceived(RPTL + peer, _ADDR)
    return peer


def _rptk(hbp: HBPProtocol, password: bytes = _PASS) -> bytes:
    salt = bytes_4(hbp._peers[_PEER]["SALT"]) if _PEER in hbp._peers else bytes(4)
    hbp.datagramReceived(RPTK + _PEER + _calc_hash(salt, password), _ADDR)
    return _PEER


def _rptc(hbp: HBPProtocol, callsign: bytes = b"CE1TEST ") -> bytes:
    hbp.datagramReceived(RPTC + _PEER + callsign + b"\x00" * 85 + b"4", _ADDR)
    return _PEER


def _logged_in(hbp: HBPProtocol) -> bytes:
    _rptl(hbp)
    _rptk(hbp)
    return _rptc(hbp)


def _salt_ack(hbp: HBPProtocol) -> bytes:
    _rptl(hbp)
    return bytes_4(hbp._peers[_PEER]["SALT"])


def _max_peers(hbp: HBPProtocol) -> bytes:
    _logged_in(hbp)
    return _rptl(hbp, _OTHER)


def _wrong_password(hbp: HBPProtocol) -> bytes:
    _rptl(hbp)
    return _rptk(hbp, b"wrong")


def _callsign_mismatch(hbp: HBPProtocol) -> bytes:
    _rptl(hbp)
    _rptk(hbp)
    return _rptc(hbp, b"OTHER   ")


def _after_login(packet: bytes) -> Callable[[HBPProtocol], bytes]:
    def scenario(hbp: HBPProtocol) -> bytes:
        _logged_in(hbp)
        hbp.datagramReceived(packet, _ADDR)
        return _PEER

    return scenario


def _without_login(packet: bytes) -> Callable[[HBPProtocol], bytes]:
    def scenario(hbp: HBPProtocol) -> bytes:
        hbp.datagramReceived(packet, _ADDR)
        return _PEER

    return scenario


def _time_out(hbp: HBPProtocol) -> bytes:
    _logged_in(hbp)
    hbp._peers[_PEER]["LAST_PING"] = 0
    hbp._master_maintenance_loop()
    return _PEER


def _dereg(hbp: HBPProtocol) -> bytes:
    _logged_in(hbp)
    hbp.dereg()
    return _PEER


# case: (_master arguments, scenario returning what follows the last reply's opcode, every reply in order)
_CASES: dict[str, tuple[dict[str, Any], Callable[[HBPProtocol], bytes], str]] = {
    "RPTL-salt": ({}, _salt_ack, "ACK"),
    "RPTL-denied": ({}, lambda hbp: _rptl(hbp, _DENIED), "NAK"),
    "RPTL-max-peers": ({"MAX_PEERS": 1}, _max_peers, "ACK ACK ACK NAK"),
    "RPTK": ({}, lambda hbp: (_rptl(hbp), _rptk(hbp))[1], "ACK ACK"),
    "RPTK-own-password": ({"own": b"own"}, lambda hbp: (_rptl(hbp), _rptk(hbp, b"own"))[1], "ACK ACK"),
    "RPTK-wrong-own-password": ({"own": b"own"}, lambda hbp: (_rptl(hbp), _rptk(hbp))[1], "ACK NAK"),
    "RPTK-wrong-passphrase": ({}, _wrong_password, "ACK NAK"),
    "RPTK-no-password": ({"PASSPHRASE": b""}, lambda hbp: (_rptl(hbp), _rptk(hbp))[1], "ACK NAK"),
    "RPTK-not-logging-in": ({}, _rptk, "NAK"),
    "RPTC": ({}, _logged_in, "ACK ACK ACK"),
    "RPTC-callsign-mismatch": ({"ALLOW_UNREG_ID": False}, _callsign_mismatch, "ACK ACK NAK"),
    "RPTC-not-logging-in": ({}, _rptc, "NAK"),
    "RPTCL": ({}, _after_login(RPTCL + _PEER), "ACK ACK ACK NAK"),
    "RPTO": ({}, _after_login(RPTO + _PEER + b"TS2=91;"), "ACK ACK ACK ACK"),
    "RPTO-not-logged-in": ({}, _without_login(RPTO + _PEER + b"TS2=91;"), "NAK"),
    "RPTPING": ({}, _after_login(RPTPING + _PEER), "ACK ACK ACK PONG"),
    "RPTPING-not-logged-in": ({}, _without_login(RPTPING + _PEER), "NAK"),
    "timeout": ({}, _time_out, "ACK ACK ACK CLOSE"),
    "dereg": ({}, _dereg, "ACK ACK ACK CLOSE"),
}


@pytest.mark.parametrize("cls", [HBPProtocol, _Renamed], ids=["classic", "renamed"])
@pytest.mark.parametrize("case", list(_CASES))
def test_each_reply_takes_its_opcode_from_the_class(case: str, cls: type[HBPProtocol]) -> None:
    master, scenario, replies = _CASES[case]
    hbp = _master(cls, **master)

    tail = scenario(hbp)

    if hbp._config_push_delayed is not None:
        hbp._config_push_delayed.cancel()
    opcodes = [_NAMES[cls][reply] for reply in replies.split()]
    sent = hbp.transport.sent
    assert [packet[: len(opcode)] for packet, opcode in zip(sent, opcodes)] == opcodes and len(sent) == len(opcodes)
    assert sent[-1] == opcodes[-1] + tail
