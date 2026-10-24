# ADN DMR Peer Server - tests trunking recent keys
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

"""RecentKeys: a key once per window, expired keys dropped as new ones come, and a cap on how many it keeps."""

from __future__ import annotations

from adn_server.application.trunking.recent import MAX_RECENT_KEYS, RecentKeys

T0 = 1_761_300_000.0


def test_a_key_is_noted_once_per_window() -> None:
    recent = RecentKeys(60.0)

    assert [recent.note("a", T0 + dt) for dt in (0, 30, 59.9, 60, 61)] == [True, False, False, True, False]


def test_a_note_drops_the_keys_whose_window_is_over() -> None:
    recent = RecentKeys(60.0)
    for n in range(10):
        recent.note(n, T0 + n)

    recent.note("new", T0 + 65)

    assert list(recent._noted) == [6, 7, 8, 9, "new"]


def test_past_the_cap_the_oldest_key_is_forgotten_first() -> None:
    recent = RecentKeys(60.0)
    for n in range(MAX_RECENT_KEYS + 1):
        recent.note(n, T0)

    assert len(recent._noted) == MAX_RECENT_KEYS
    assert recent.note(1, T0) is False
    assert recent.note(0, T0) is True
