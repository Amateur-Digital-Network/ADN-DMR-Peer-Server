# ADN DMR Peer Server - voice packet generator (legacy mk_voice.pkt_gen)
# Copyright (C) 2026  Rodrigo Pérez, CE5RPY <ce5rpy@qmd.cl>
#
# Derived from ADN DMR Server / FreeDMR  / HBlink. Original license:
###############################################################################
# Copyright (C) 2020 Simon Adlem, G7RZU <g7rzu@gb7fr.org.uk>
# Copyright (C) 2016-2019 Cortney T. Buffington, N0MJS <n0mjs@me.com>
#
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

"""Generate HBP DMRD voice packets for a phrase. Legacy mk_voice.pkt_gen."""

from __future__ import annotations

from random import randint
from typing import Any, Iterator

from ...domain import bytes_4
from ...domain.dmr.frames import DmrdStream


def pkt_gen(
    rf_src: bytes,
    dst_id: bytes,
    peer: bytes,
    slot: int,
    phrase: list[Any],
    *,
    private: bool = False,
) -> Iterator[bytes]:
    """Generate DMRD voice packets for phrase. Each word in phrase is list of [b0, b1] burst pairs (bitarray).

    ``private``: a unit-to-unit call (LC FLCO 3, DMRD call type bit) instead of a group call.
    """
    stream = DmrdStream(rf_src, dst_id, peer, slot, bytes_4(randint(0x00, 0xFFFFFFFF)), private=private)
    for _ in range(3):
        yield stream.header()
    for word in phrase:
        for burst in range(len(word)):
            yield stream.burst(burst, word[burst][0], word[burst][1])
    yield stream.terminator()
