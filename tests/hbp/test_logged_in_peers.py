# ADN DMR Peer Server - tests hbp logged in peers
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

"""A MASTER's peer queries: a peer has an address only once its login is complete; drop_peer forgets it."""

from __future__ import annotations

from typing import Any

from adn_server.domain.value_objects import bytes_4
from adn_server.infrastructure.config_normalizer import ensure_system_runtime_config
from adn_server.infrastructure.hbp_constants import RPTC, RPTK, RPTL
from adn_server.infrastructure.twisted_adapters.udp_hbp import HBPProtocol, _calc_hash, _get_passphrase_bytes

_PEER = bytes_4(1234567)
_ADDR = ("192.168.1.50", 62031)
_OTHER = bytes_4(1234568)
_OTHER_ADDR = ("192.168.1.51", 62031)


class _Transport:
    def write(self, data: bytes, addr: tuple[str, int]) -> None:
        pass


class _Router:
    def acl_check(self, _value: bytes, _acl: object) -> bool:
        return True


def _master() -> tuple[HBPProtocol, dict[str, Any]]:
    config: dict[str, Any] = {
        "GLOBAL": {"PING_TIME": 10, "MAX_MISSED": 3, "USE_ACL": False},
        "SYSTEMS": {
            "HOTSPOT": {
                "MODE": "MASTER", "ENABLED": True, "MAX_PEERS": 8, "PASSPHRASE": b"test-passphrase",
                "OPTIONS": "TS2=3340;", "_default_options": "TS2=9990;",
            },
        },
    }
    ensure_system_runtime_config(config)
    hbp = HBPProtocol("HOTSPOT", config, router=_Router())  # type: ignore[arg-type]
    hbp.transport = _Transport()  # type: ignore[assignment]
    return hbp, config["SYSTEMS"]["HOTSPOT"]


def _monitor_pushed(hbp: HBPProtocol) -> bool:
    pending, hbp._config_push_delayed = hbp._config_push_delayed, None
    if pending is not None:
        pending.cancel()
    return pending is not None


def _login_steps(hbp: HBPProtocol, peer: bytes, addr: tuple[str, int]):
    hbp.datagramReceived(RPTL + peer, addr)
    yield
    salt = bytes_4(hbp._peers[peer]["SALT"])
    hbp.datagramReceived(RPTK + peer + _calc_hash(salt, _get_passphrase_bytes(hbp._config)), addr)
    yield
    hbp.datagramReceived(RPTC + peer + b"CE1TEST " + b"\x00" * 85 + b"4", addr)
    yield


def test_a_peer_has_an_address_only_once_its_login_is_complete() -> None:
    hbp, _ = _master()
    assert hbp.logged_in_addr(_PEER) is None and hbp.logged_in_peer_at(_ADDR) is None

    seen = [(hbp.logged_in_addr(_PEER), hbp.logged_in_peer_at(_ADDR)) for _ in _login_steps(hbp, _PEER, _ADDR)]

    assert seen == [(None, None), (None, None), (_ADDR, _PEER)]
    assert hbp.logged_in_peer_at(_OTHER_ADDR) is None and hbp.logged_in_addr(_OTHER) is None


def test_drop_peer_resets_only_when_the_last_peer_is_gone() -> None:
    hbp, sys_cfg = _master()
    for peer, addr in ((_PEER, _ADDR), (_OTHER, _OTHER_ADDR)):
        for _ in _login_steps(hbp, peer, addr):
            pass
    _monitor_pushed(hbp)

    hbp.drop_peer(_OTHER)
    assert hbp.logged_in_addr(_OTHER) is None and hbp.logged_in_peer_at(_OTHER_ADDR) is None
    assert not sys_cfg.get("_reset") and sys_cfg["OPTIONS"] == "TS2=3340;"
    assert _monitor_pushed(hbp)

    hbp.drop_peer(_PEER)
    assert hbp._peers == {}
    assert sys_cfg["_reset"] is True and sys_cfg["OPTIONS"] == "TS2=9990;"
    assert _monitor_pushed(hbp)
