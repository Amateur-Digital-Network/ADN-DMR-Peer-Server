# ADN DMR Peer Server - tests peer_matches_rf_source
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

"""peer_matches_rf_source checks one hotspot directly: same answers as scanning every peer."""

from __future__ import annotations

import random

from adn_server.application.routing.helpers import _peer_key_from_int, peer_matches_rf_source
from adn_server.domain import bytes_3, bytes_4, int_id


def _reference(peer_id: bytes, rf_src: bytes, peers: dict) -> bool:
    """The previous implementation: match every peer, then look for this one."""
    val = int_id(rf_src)
    val_str = str(val)
    matches = []
    for pk in peers:
        try:
            pk_b = _peer_key_from_int(pk)
        except (TypeError, ValueError):
            continue
        pk_int = int_id(pk_b)
        pk_str = str(pk_int)
        if pk_int == val or pk_int // 100 == val or (
            len(val_str) >= 5 and len(pk_str) >= 7 and pk_str.startswith(val_str)
        ):
            matches.append(pk_b)
    return _peer_key_from_int(peer_id) in matches


def test_hotspot_of_the_radio_matches() -> None:
    peers = {bytes_4(213003501): {}, bytes_4(213003598): {}, bytes_4(214000101): {}, bytes_4(2130035): {}}
    rf = bytes_3(2130035)
    assert peer_matches_rf_source(bytes_4(213003501), rf, peers)  # ID + 2-digit suffix
    assert peer_matches_rf_source(bytes_4(2130035), rf, peers)  # the radio ID itself
    assert not peer_matches_rf_source(bytes_4(214000101), rf, peers)


def test_prefix_rule_needs_five_digits() -> None:
    peers = {bytes_4(213003501): {}, bytes_4(1234567): {}}
    assert peer_matches_rf_source(bytes_4(213003501), bytes_3(21300), peers)  # 5-digit prefix
    assert not peer_matches_rf_source(bytes_4(1234567), bytes_3(1234), peers)  # 4 digits: no


def test_same_answers_as_scanning_every_peer() -> None:
    rnd = random.Random(159)
    for _ in range(300):
        ids = [rnd.choice([rnd.randrange(1_000_000, 9_999_999), rnd.randrange(100_000_000, 999_999_999),
                           rnd.randrange(10_000, 99_999)]) for _ in range(rnd.randrange(1, 12))]
        kind = rnd.choice(["bytes", "int", "str"])
        peers = {(bytes_4(i) if kind == "bytes" else i if kind == "int" else str(i)): {} for i in ids}
        base = rnd.choice(ids)
        candidates = [base, base // 100, int(str(base)[:5]), int(str(base)[:6]), rnd.randrange(1, 16_777_215)]
        rf = bytes_3(min(rnd.choice(candidates), 16_777_215))
        for pid in [*ids, rnd.randrange(1, 999_999_999)]:
            peer = bytes_4(pid)
            assert peer_matches_rf_source(peer, rf, peers) == _reference(peer, rf, peers), (pid, int_id(rf), kind)
