# ADN DMR Peer Server - tests hbp bridge reset only without peers
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

"""BRIDGERESET clears every leg of a MASTER: only when its last peer is gone.

A failed login, a callsign mismatch or one hotspot closing down used to set ``_reset`` on
the whole system, so the next frame was dropped ("disallow transmission") and every TG
the other hotspots were listening to went IDLE, cutting their calls and announcements.
"""

from __future__ import annotations

import time

from adn_server.domain.value_objects import bytes_4
from adn_server.infrastructure.config_normalizer import ensure_system_runtime_config
from adn_server.infrastructure.hbp_constants import MSTNAK, RPTC, RPTCL, RPTK, RPTL
from adn_server.infrastructure.twisted_adapters.udp_hbp import (
    HBPProtocol,
    _calc_hash,
    _get_passphrase_bytes,
)

_LISTENER = bytes_4(213003591)
_LISTENER_ADDR = ("192.168.1.10", 62031)
_OTHER = bytes_4(1234567)
_OTHER_ADDR = ("192.168.1.50", 62031)
_DENIED = bytes_4(2131)


class _Transport:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def write(self, data: bytes, addr: tuple[str, int]) -> None:
        self.sent.append((data, addr))


class _Router:
    def acl_check(self, peer_id: bytes, acl: object) -> bool:
        return peer_id != _DENIED


def _master() -> tuple[HBPProtocol, _Transport, dict]:
    config = {
        "GLOBAL": {"PING_TIME": 10, "MAX_MISSED": 3, "USE_ACL": False},
        "SYSTEMS": {
            "SYSTEM": {
                "MODE": "MASTER",
                "ENABLED": True,
                "MAX_PEERS": 8,
                "PASSPHRASE": b"test-passphrase",
                "OPTIONS": "TS2=3340;",
                "_default_options": "TS2=9990;",
            }
        },
    }
    ensure_system_runtime_config(config)
    hbp = HBPProtocol("SYSTEM", config, router=_Router())  # type: ignore[arg-type]
    transport = _Transport()
    hbp.transport = transport  # type: ignore[assignment]
    return hbp, transport, config["SYSTEMS"]["SYSTEM"]


def _connect(hbp: HBPProtocol, peer: bytes, addr: tuple[str, int], callsign: bytes = b"C31AG   ") -> None:
    hbp.datagramReceived(RPTL + peer, addr)
    salt = bytes_4(hbp._peers[peer]["SALT"])
    hbp.datagramReceived(RPTK + peer + _calc_hash(salt, _get_passphrase_bytes(hbp._config)), addr)
    hbp.datagramReceived(RPTC + peer + callsign + b"\x00" * 85 + b"4", addr)


def test_failed_login_does_not_reset_a_master_with_peers() -> None:
    hbp, transport, sys_cfg = _master()
    _connect(hbp, _LISTENER, _LISTENER_ADDR)
    assert hbp._peers[_LISTENER]["CONNECTION"] == "YES"
    transport.sent.clear()

    hbp.datagramReceived(RPTL + _DENIED, _OTHER_ADDR)

    assert transport.sent and transport.sent[0][0].startswith(MSTNAK)
    assert not sys_cfg.get("_reset"), "the listener's TGs must stay up"
    assert sys_cfg["OPTIONS"] == "TS2=3340;"


def test_failed_login_still_resets_an_empty_master() -> None:
    hbp, _transport, sys_cfg = _master()
    hbp.datagramReceived(RPTL + _DENIED, _OTHER_ADDR)
    assert sys_cfg.get("_reset") is True


def test_callsign_mismatch_does_not_reset_a_master_with_peers() -> None:
    hbp, transport, sys_cfg = _master()
    hbp._config["ALLOW_UNREG_ID"] = False
    hbp._CONFIG["_SUB_IDS"] = {2130035: "C31AG", 213003591: "C31AG", 1234567: "CE1TEST"}
    _connect(hbp, _LISTENER, _LISTENER_ADDR)
    assert hbp._peers[_LISTENER]["CONNECTION"] == "YES"

    _connect(hbp, _OTHER, _OTHER_ADDR, callsign=b"WRONG   ")

    assert _OTHER not in hbp._peers
    assert not sys_cfg.get("_reset")


def test_one_peer_closing_keeps_the_others_options_and_legs() -> None:
    hbp, transport, sys_cfg = _master()
    _connect(hbp, _LISTENER, _LISTENER_ADDR)
    _connect(hbp, _OTHER, _OTHER_ADDR)
    assert hbp._peers[_OTHER]["CONNECTION"] == "YES"

    hbp.datagramReceived(RPTCL + _OTHER, _OTHER_ADDR)

    assert _OTHER not in hbp._peers and _LISTENER in hbp._peers
    assert not sys_cfg.get("_reset")
    assert sys_cfg["OPTIONS"] == "TS2=3340;"

    # the last one leaving resets as before, back to the default OPTIONS
    hbp.datagramReceived(RPTCL + _LISTENER, _LISTENER_ADDR)
    assert sys_cfg.get("_reset") is True
    assert sys_cfg["OPTIONS"] == "TS2=9990;"


def test_one_peer_timing_out_keeps_the_others_legs() -> None:
    hbp, _transport, sys_cfg = _master()
    _connect(hbp, _LISTENER, _LISTENER_ADDR)
    _connect(hbp, _OTHER, _OTHER_ADDR)
    hbp._peers[_OTHER]["LAST_PING"] = 0
    hbp._peers[_LISTENER]["LAST_PING"] = time.time()

    hbp._master_maintenance_loop()

    assert _OTHER not in hbp._peers
    assert not sys_cfg.get("_reset")
