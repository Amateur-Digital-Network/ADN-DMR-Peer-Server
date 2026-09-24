# ADN DMR Peer Server - tests peer OPTIONS / RF mode caching
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

"""Per-peer OPTIONS fields and RF mode are parsed on change, not per frame."""

from __future__ import annotations

from typing import Any

from tests.support.hbp_repeat_stack import build_hbp_repeat_stack

from adn_server.application.routing.helpers import (
    RF_MODE_DUPLEX,
    RF_MODE_SIMPLEX,
    peer_options_fields,
    peer_rf_mode,
    peer_single_mode,
)
from adn_server.application.routing.peer_downlink_index import cached_peer_static_tgs

_PEER = (1234567).to_bytes(4, "big")
_ADDR = ("10.0.0.9", 54321)


def _rptc(peer_id: bytes, *, slots: bytes, rx: bytes, tx: bytes) -> bytes:
    """MMDVM RPTC frame (field widths per hblink.py / MMDVMHost RPTC layout)."""
    return b"".join(
        [
            b"RPTC",
            peer_id,
            b"CE5RPY  ",
            rx.ljust(9, b"0"),
            tx.ljust(9, b"0"),
            b"01",
            b"01",
            b"00.00000",
            b"000.00000",
            b"000",
            b"Andorra".ljust(20),
            b"hotspot".ljust(19),
            slots,
            b"".ljust(124),
            b"MMDVM".ljust(40),
            b"".ljust(40),
        ]
    )


def _waiting_peer(stack: Any, peer_id: bytes, addr: tuple[str, int]) -> dict[str, Any]:
    peer: dict[str, Any] = {
        "CONNECTION": "WAITING_CONFIG",
        "SOCKADDR": addr,
        "RADIO_ID": str(int.from_bytes(peer_id, "big")),
        "CALLSIGN": b"CE5RPY  ",
    }
    stack.hbp._peers[peer_id] = peer
    return peer


def test_options_fields_reused_while_the_blob_is_unchanged() -> None:
    """Same OPTIONS must not be re-parsed: ingress asks several times per frame."""
    stack = build_hbp_repeat_stack()
    stack.register_peer(_PEER, _ADDR)
    peer = stack.hbp._peers[_PEER]
    peer["OPTIONS"] = b"TS2_1=214;SINGLE=1;TIMER=15;"

    first = peer_options_fields(peer)
    assert peer_options_fields(peer) is first


def test_rpto_makes_the_new_options_take_effect() -> None:
    """RPTO drops the cached parse, so SINGLE/TIMER and static TGs follow the new blob."""
    stack = build_hbp_repeat_stack()
    sys_cfg = stack.config["SYSTEMS"][stack.system_name]
    stack.register_peer(_PEER, _ADDR)
    peer = stack.hbp._peers[_PEER]

    stack.hbp._master_datagram_received(b"RPTO" + _PEER + b"TS2_1=214;SINGLE=1;", _ADDR)
    assert peer_single_mode(peer, sys_cfg) is True
    assert cached_peer_static_tgs(peer) == ((), ("214",))

    stack.hbp._master_datagram_received(b"RPTO" + _PEER + b"TS1_1=91;SINGLE=0;", _ADDR)
    assert peer_single_mode(peer, sys_cfg) is False
    assert cached_peer_static_tgs(peer) == (("91",), ())
    assert peer_options_fields(peer).get("SINGLE") == "0"


def test_rptc_classifies_rf_mode_at_login() -> None:
    """Simplex/duplex is decided from the RPTC fields, not on every frame."""
    stack = build_hbp_repeat_stack()
    stack.config["SYSTEMS"][stack.system_name]["ALLOW_UNREG_ID"] = True
    peer = _waiting_peer(stack, _PEER, _ADDR)

    stack.hbp._master_datagram_received(
        _rptc(_PEER, slots=b"3", rx=b"438500000", tx=b"431100000"), _ADDR,
    )
    assert peer["CONNECTION"] == "YES"
    assert peer["RF_MODE"] == RF_MODE_DUPLEX
    assert peer_rf_mode(peer) == RF_MODE_DUPLEX


def test_relogin_with_new_slots_reclassifies_rf_mode() -> None:
    """A hotspot that comes back as simplex must not keep the duplex verdict."""
    stack = build_hbp_repeat_stack()
    stack.config["SYSTEMS"][stack.system_name]["ALLOW_UNREG_ID"] = True
    peer = _waiting_peer(stack, _PEER, _ADDR)
    stack.hbp._master_datagram_received(
        _rptc(_PEER, slots=b"3", rx=b"438500000", tx=b"431100000"), _ADDR,
    )
    assert peer["RF_MODE"] == RF_MODE_DUPLEX

    peer["CONNECTION"] = "WAITING_CONFIG"
    stack.hbp._master_datagram_received(
        _rptc(_PEER, slots=b"4", rx=b"145500000", tx=b"145500000"), _ADDR,
    )
    assert peer["RF_MODE"] == RF_MODE_SIMPLEX
    assert peer_rf_mode(peer) == RF_MODE_SIMPLEX
