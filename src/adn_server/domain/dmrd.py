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

"""Fields of an HBP DMRD frame."""

from __future__ import annotations

from typing import NamedTuple

from .mesh_admission import call_attributes


class DmrdHeader(NamedTuple):
    seq: int
    rf_src: bytes
    dst_id: bytes
    peer_id: bytes
    slot: int
    call_type: str
    frame_type: int
    dtype_vseq: int
    stream_id: bytes


def parse_dmrd_header(pkt: bytes) -> DmrdHeader | None:
    """The HBP DMRD header fields of any call type (group, vcsbk or unit)."""
    if len(pkt) < 53 or pkt[:4] != b"DMRD":
        return None
    attrs = call_attributes(pkt[15])
    return DmrdHeader(
        seq=pkt[4],
        rf_src=pkt[5:8],
        dst_id=pkt[8:11],
        peer_id=pkt[11:15],
        slot=attrs.slot,
        call_type=attrs.call_type,
        frame_type=attrs.frame_type,
        dtype_vseq=attrs.dtype_vseq,
        stream_id=pkt[16:20],
    )
