# ADN DMR Peer Server - tests terminal peers (ODTP gateway) statics
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

"""A terminal peer (``TERMINAL=1`` in OPTIONS) takes only its own OPTIONS statics, never the MASTER's."""

from __future__ import annotations

from adn_server.application.report.payloads import peer_is_terminal
from adn_server.application.routing.peer_downlink_index import (
    build_peer_downlink_index,
    cached_peer_static_tgs,
    invalidate_peer_options_cache,
)
from adn_server.domain import bytes_4

MASTER_STATIC = (("9",), ("666", "214"))


def _peer(options: str) -> dict:
    return {"CONNECTION": "YES", "OPTIONS": options.encode(), "_SYSTEM_STATIC": MASTER_STATIC}


def test_hotspot_takes_master_statics_then_its_own() -> None:
    assert cached_peer_static_tgs(_peer("TS2=91;")) == (("9",), ("666", "214", "91"))


def test_terminal_takes_only_its_own_statics() -> None:
    assert cached_peer_static_tgs(_peer("TERMINAL=1;TS2=666;")) == ((), ("666",))


def test_terminal_without_subscriptions_gets_nothing() -> None:
    assert cached_peer_static_tgs(_peer("TERMINAL=1;")) == ((), ())


def test_terminal_flag_values() -> None:
    assert peer_is_terminal(b"TERMINAL=1;TS2=91;")
    assert peer_is_terminal("terminal=yes")
    assert not peer_is_terminal(b"TERMINAL=0;TS2=91;")
    assert not peer_is_terminal(b"TS2=91;")
    assert not peer_is_terminal(None)


def test_subscription_change_by_rpto() -> None:
    peer = _peer("TERMINAL=1;TS2=666;")
    cached_peer_static_tgs(peer)
    peer["OPTIONS"] = b"TERMINAL=1;TS2=91,3100;"
    invalidate_peer_options_cache(peer)
    assert cached_peer_static_tgs(peer) == ((), ("91", "3100"))


def test_downlink_index_skips_master_statics_for_terminal() -> None:
    hotspot, terminal = bytes_4(213003501), bytes_4(213003598)
    peers = {hotspot: _peer("TS2=91;"), terminal: _peer("TERMINAL=1;TS2=666;")}
    idx = build_peer_downlink_index(peers, {})
    assert idx.candidates(2, 214, connected_count=2) == frozenset({hotspot})
    assert idx.candidates(2, 666, connected_count=2) == frozenset({hotspot, terminal})
