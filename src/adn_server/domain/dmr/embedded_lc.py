# ADN DMR Peer Server - DMRD voice frames
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

"""Embedded LC of a voice stream, reassembled from bursts B-E."""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import bptc, decode

if TYPE_CHECKING:
    from bitarray import bitarray


def collect_embedded_lc(acc: dict[int, bitarray], vseq: int, burst: bytes) -> bytes | None:
    """Add the fragment of burst ``vseq`` (1-4 for B-E) to ``acc``; the 9-byte LC once E completes it."""
    if vseq not in (1, 2, 3, 4) or len(burst) < 33:
        return None
    try:
        embed = decode.voice_embed(burst)
    except Exception:
        return None
    if vseq == 1:
        acc.clear()
    acc[vseq] = embed
    if len(acc) < 4:  # keys are vseq 1-4 only, and vseq 1 clears it
        return None
    try:
        return bptc.decode_emblc(acc[1] + acc[2] + acc[3] + acc[4])
    except Exception:
        return None
    finally:
        acc.clear()
