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
from tests.support.hbp_repeat_stack import HbpRepeatStack, build_hbp_repeat_stack

from adn_server.domain import bytes_3
from adn_server.domain.subscription import SystemId

_A = (213003507).to_bytes(4, "big")
_B = (213003591).to_bytes(4, "big")
_ADDR_A = ("10.0.0.7", 62031)
_ADDR_B = ("10.0.0.8", 62031)
_SYSTEM = "MASTER-A"


def _master(*, shared: bool) -> tuple[HbpRepeatStack, MagicMock, MagicMock]:
    """The real HBPProtocol and routing; hotspot B listens to TG 3340 on TS2."""
    stack = build_hbp_repeat_stack(talker_alias=False, system_name=_SYSTEM)
    if shared:
        stack.config["PROXY"] = {"TARGET_SYSTEM": _SYSTEM}
    hbp = stack.hbp
    hbp._on_in_band_signalling = stack.bridge.apply_in_band_signalling
    deactivate, dynamic_tgs = MagicMock(), MagicMock()
    hbp._on_deactivate_dynamic_relays = deactivate
    hbp._dynamic_tg_uc = dynamic_tgs
    stack.register_peer(_A, _ADDR_A)
    stack.register_peer(_B, _ADDR_B)
    stack.bridge.ensure_dynamic_relay(bytes_3(3340), _SYSTEM, 2, 900.0)
    assert _leg_3340_active(stack)
    return stack, deactivate, dynamic_tgs


def _leg_3340_active(stack: HbpRepeatStack) -> bool:
    store = stack.bridge._subscription_store
    return any(
        sub.is_active() for sub in store.list_by_system(SystemId(_SYSTEM))
        if int(sub.target_tgid) == 3340 and int(sub.channel.slot) == 2
    )


def _tg4000_from_a(stack: HbpRepeatStack, call_type: str = "group") -> None:
    head = replace(
        PacketSpec(dst_id=4000, slot=2, stream_id=0x40004000, call_type=call_type,
                   peer_id=int.from_bytes(_A, "big"), rf_src=2130035),
        frame_type=2, dtype_vseq=1,
    ).data()
    stack.hbp._master_datagram_received(head, _ADDR_A)


def test_on_a_shared_master_4000_leaves_the_other_hotspots_legs_alone() -> None:
    stack, deactivate, dynamic_tgs = _master(shared=True)
    _tg4000_from_a(stack)
    assert _leg_3340_active(stack), "hotspot B still hears TG 3340"
    deactivate.assert_not_called()
    dynamic_tgs.delete_peer.assert_called_once_with(_A, _SYSTEM)  # the sender's own dynamics go


def test_a_private_call_to_4000_on_a_shared_master_does_the_same() -> None:
    stack, deactivate, dynamic_tgs = _master(shared=True)
    _tg4000_from_a(stack, call_type="unit")
    assert _leg_3340_active(stack)
    deactivate.assert_not_called()
    dynamic_tgs.delete_peer.assert_called_once_with(_A, _SYSTEM)


def test_a_master_of_its_own_still_resets_its_legs() -> None:
    stack, deactivate, _dynamic_tgs = _master(shared=False)
    _tg4000_from_a(stack)
    assert not _leg_3340_active(stack)  # in-band 4000 signalling, as before
    deactivate.assert_called_once_with(_SYSTEM)
