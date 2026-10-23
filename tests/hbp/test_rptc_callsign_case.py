# ADN DMR Peer Server - tests hbp rptc callsign case
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

"""RPTC callsign check against the subscriber DB is case-insensitive.

Older IPSC2HBP builds send the callsign lower case ("hp3icc"). With ALLOW_UNREG_ID off the
master compared it byte for byte with the DB ("HP3ICC") and answered MSTNAK after a good
login, so those repeaters could not connect to any v2 master that checks callsigns.
"""

from __future__ import annotations

import pytest

from adn_server.domain.value_objects import bytes_4
from adn_server.infrastructure.config_normalizer import ensure_system_runtime_config
from adn_server.infrastructure.hbp_constants import MSTNAK, RPTACK, RPTC, RPTK, RPTL
from adn_server.infrastructure.twisted_adapters.udp_hbp import (
    HBPProtocol,
    _calc_hash,
    _callsign_matches,
    _get_passphrase_bytes,
)

_PEER = bytes_4(714000137)
_ADDR = ("66.154.118.84", 49738)


class _Transport:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def write(self, data: bytes, addr: tuple[str, int]) -> None:
        self.sent.append((data, addr))


class _Router:
    def acl_check(self, peer_id: bytes, acl: object) -> bool:
        return True


def _master(sub_ids: dict[int, str]) -> tuple[HBPProtocol, _Transport]:
    config = {
        "GLOBAL": {"PING_TIME": 10, "MAX_MISSED": 3, "USE_ACL": False},
        "SYSTEMS": {
            "SYSTEM": {
                "MODE": "MASTER",
                "ENABLED": True,
                "MAX_PEERS": 8,
                "PASSPHRASE": b"test-passphrase",
                "ALLOW_UNREG_ID": False,
            }
        },
    }
    ensure_system_runtime_config(config)
    hbp = HBPProtocol("SYSTEM", config, router=_Router())  # type: ignore[arg-type]
    hbp._CONFIG["_SUB_IDS"] = sub_ids
    transport = _Transport()
    hbp.transport = transport  # type: ignore[assignment]
    return hbp, transport


def _rptc_reply(hbp: HBPProtocol, transport: _Transport, callsign: bytes) -> bytes:
    hbp.datagramReceived(RPTL + _PEER, _ADDR)
    salt = bytes_4(hbp._peers[_PEER]["SALT"])
    hbp.datagramReceived(RPTK + _PEER + _calc_hash(salt, _get_passphrase_bytes(hbp._config)), _ADDR)
    transport.sent.clear()
    hbp.datagramReceived(RPTC + _PEER + callsign.ljust(8) + b"\x00" * 285 + b"4", _ADDR)
    return transport.sent[0][0]


@pytest.mark.parametrize("callsign", [b"hp3icc", b"HP3ICC", b"Hp3Icc"])
def test_callsign_in_any_case_is_accepted(callsign: bytes) -> None:
    hbp, transport = _master({7140001: "HP3ICC"})
    assert _rptc_reply(hbp, transport, callsign).startswith(RPTACK)
    assert hbp._peers[_PEER]["CONNECTION"] == "YES"


def test_db_callsign_in_lower_case_is_accepted() -> None:
    hbp, transport = _master({7140001: "hp3icc"})
    assert _rptc_reply(hbp, transport, b"HP3ICC").startswith(RPTACK)


def test_other_callsign_is_still_rejected() -> None:
    hbp, transport = _master({7140001: "HP3ICC"})
    assert _rptc_reply(hbp, transport, b"hp3abc").startswith(MSTNAK)
    assert _PEER not in hbp._peers


def test_unknown_id_is_still_rejected_at_login() -> None:
    hbp, transport = _master({})
    hbp.datagramReceived(RPTL + _PEER, _ADDR)
    assert transport.sent[0][0].startswith(MSTNAK)


def test_callsign_matches_needs_a_db_callsign() -> None:
    assert _callsign_matches("hp3icc", "HP3ICC ")
    assert not _callsign_matches("hp3icc", False)
    assert not _callsign_matches("", False)
