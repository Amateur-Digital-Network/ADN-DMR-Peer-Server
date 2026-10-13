# ADN DMR Peer Server - tests hbp TG 4000 on a shared MASTER
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

"""TG/ID 4000 on a MASTER shared by many hotspots clears the sender's dynamics only.

Seen on 2131: one hotspot's TG 4000 on TS2 set every leg of SYSTEM on TS2 IDLE through the
in-band signalling (213, 2131, 3340, 7144, 9140, 214480), so the other hotspots stopped
hearing their talkgroups, and the OPTIONS statics among them came back only an hour later.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

from tests.harness.deterministic import PacketSpec

from adn_server.domain import bytes_3
from adn_server.infrastructure.config_normalizer import ensure_system_runtime_config
from adn_server.infrastructure.twisted_adapters.udp_hbp import HBPProtocol

_A = (213003507).to_bytes(4, "big")
_B = (213003591).to_bytes(4, "big")
_ADDR_A = ("10.0.0.7", 62031)
_ADDR_B = ("10.0.0.8", 62031)


class _Router:
    def acl_check(self, _id: bytes, _acl: object) -> bool:
        return True


class _Transport:
    def write(self, _data: bytes, _addr: tuple[str, int]) -> None:
        pass


def _master(*, shared: bool) -> tuple[HBPProtocol, MagicMock, MagicMock, MagicMock]:
    config = {
        "GLOBAL": {"PING_TIME": 10, "MAX_MISSED": 3, "USE_ACL": False},
        "SYSTEMS": {"SYSTEM": {"MODE": "MASTER", "ENABLED": True, "MAX_PEERS": 8, "PASSPHRASE": b"x"}},
    }
    if shared:
        config["PROXY"] = {"TARGET_SYSTEM": "SYSTEM"}
    ensure_system_runtime_config(config)
    in_band, deactivate, dynamic_tgs = MagicMock(), MagicMock(), MagicMock()
    hbp = HBPProtocol(
        "SYSTEM", config, router=_Router(),  # type: ignore[arg-type]
        on_in_band_signalling=in_band, on_deactivate_dynamic_relays=deactivate, dynamic_tg_uc=dynamic_tgs,
    )
    hbp.transport = _Transport()  # type: ignore[assignment]
    for peer, addr in ((_A, _ADDR_A), (_B, _ADDR_B)):
        hbp._peers[peer] = {
            "CONNECTION": "YES", "CONNECTED": 1.0, "LAST_PING": 1e12, "SOCKADDR": addr,
            "CALLSIGN": b"C31AG   ", "RADIO_ID": str(int.from_bytes(peer, "big")),
        }
    return hbp, in_band, deactivate, dynamic_tgs


def _tg4000_from_a(hbp: HBPProtocol, call_type: str = "group") -> None:
    head = replace(
        PacketSpec(dst_id=4000, slot=2, stream_id=0x40004000, call_type=call_type,
                   peer_id=int.from_bytes(_A, "big"), rf_src=2130035),
        frame_type=2, dtype_vseq=1,
    ).data()
    hbp._master_datagram_received(head, _ADDR_A)


def test_on_a_shared_master_4000_leaves_the_other_hotspots_legs_alone() -> None:
    hbp, in_band, deactivate, dynamic_tgs = _master(shared=True)
    _tg4000_from_a(hbp)
    in_band.assert_not_called()
    deactivate.assert_not_called()
    dynamic_tgs.delete_peer.assert_called_once_with(_A, "SYSTEM")  # the sender's own dynamics go


def test_a_private_call_to_4000_on_a_shared_master_does_the_same() -> None:
    hbp, in_band, deactivate, dynamic_tgs = _master(shared=True)
    _tg4000_from_a(hbp, call_type="unit")
    in_band.assert_not_called()
    deactivate.assert_not_called()
    dynamic_tgs.delete_peer.assert_called_once_with(_A, "SYSTEM")


def test_a_master_of_its_own_still_resets_its_legs() -> None:
    hbp, in_band, deactivate, dynamic_tgs = _master(shared=False)
    _tg4000_from_a(hbp)
    in_band.assert_called_once()
    system, slot, dst, _t = in_band.call_args.args
    assert (system, slot, dst) == ("SYSTEM", 2, bytes_3(4000))
    deactivate.assert_called_once_with("SYSTEM")
