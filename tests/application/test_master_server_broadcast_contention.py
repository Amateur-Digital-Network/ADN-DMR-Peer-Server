# ADN DMR Peer Server - server broadcast slot contention
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

"""MASTER slot contention while server scheduled voice holds TX row."""

from __future__ import annotations

from adn_server.application.routing.helpers import (
    hbp_slot_blocks_group_voice,
    master_slot_holds_server_broadcast,
)
from adn_server.application.server_voice import DEFAULT_SERVER_VOICE_ID
from adn_server.domain import HBPF_SLT_VHEAD, HBPF_SLT_VTERM, STREAM_TO, bytes_3, bytes_4


def test_master_slot_holds_server_broadcast_detects_announcement_row() -> None:
    slot = {
        "TX_TYPE": HBPF_SLT_VHEAD,
        "TX_TIME": 100.0,
        "TX_RFS": bytes_3(DEFAULT_SERVER_VOICE_ID),
        "TX_TGID": bytes_3(91),
    }
    assert master_slot_holds_server_broadcast(slot, 100.1) is True
    assert master_slot_holds_server_broadcast(slot, 100.0 + STREAM_TO + 1) is False


def test_master_slot_holds_server_broadcast_ignores_obp_tx() -> None:
    slot = {
        "TX_TYPE": HBPF_SLT_VHEAD,
        "TX_TIME": 100.0,
        "TX_RFS": bytes_3(3340062),
        "TX_TGID": bytes_3(7144),
    }
    assert master_slot_holds_server_broadcast(slot, 100.1) is False


def test_master_slot_holds_server_broadcast_ignores_another_servers_announcement() -> None:
    """Every server announces as 1000001: one relayed over OpenBridge is ordinary mesh voice."""
    slot = {
        "TX_TYPE": HBPF_SLT_VHEAD,
        "TX_TIME": 100.0,
        "TX_RFS": bytes_3(DEFAULT_SERVER_VOICE_ID),
        "TX_TGID": bytes_3(9140),
        "TX_FROM_MESH": True,
    }
    assert master_slot_holds_server_broadcast(slot, 100.1) is False
    slot["TX_FROM_MESH"] = False  # this server's own announcement still holds the slot
    assert master_slot_holds_server_broadcast(slot, 100.1) is True


def test_obp_blocked_when_server_broadcast_holds_slot() -> None:
    slot = {
        "RX_TYPE": HBPF_SLT_VTERM,
        "TX_TYPE": HBPF_SLT_VHEAD,
        "TX_TIME": 200.0,
        "TX_RFS": bytes_3(DEFAULT_SERVER_VOICE_ID),
        "TX_TGID": bytes_3(91),
    }
    blocked = hbp_slot_blocks_group_voice(
        slot,
        bytes_3(7144),
        bytes_4(0x11111111),
        200.05,
        group_hangtime=3.0,
    )
    assert blocked is True
