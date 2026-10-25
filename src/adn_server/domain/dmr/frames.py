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

"""DMRD voice frames: header, voice bursts A-F and terminator of one stream."""

from __future__ import annotations

from bitarray import bitarray

from . import bptc
from .bptc import encode_emblc
from .const import BS_DATA_SYNC, BS_VOICE_SYNC, EMB, LC_OPT, LC_OPT_U, SLOT_TYPE

# DMRD byte 15 below the slot and call-type bits.
HEADBITS = 0b00100001
BURSTBITS = [0b00010000, 0b00000001, 0b00000010, 0b00000011, 0b00000100, 0b00000101]
TERMBITS = 0b00100010

NULL_EMB_LC = bitarray(endian="big")
NULL_EMB_LC.frombytes(b"\x00\x00\x00\x00")

TAIL = b"\x00\x00"


def superframe_embedding(lc: bytes) -> list[bitarray]:
    """Sync or EMB field of bursts A-F, with ``lc`` embedded in B-E."""
    emb_lc = encode_emblc(lc)
    return [
        BS_VOICE_SYNC,
        EMB["BURST_B"][:8] + emb_lc[1] + EMB["BURST_B"][-8:],
        EMB["BURST_C"][:8] + emb_lc[2] + EMB["BURST_C"][-8:],
        EMB["BURST_D"][:8] + emb_lc[3] + EMB["BURST_D"][-8:],
        EMB["BURST_E"][:8] + emb_lc[4] + EMB["BURST_E"][-8:],
        EMB["BURST_F"][:8] + NULL_EMB_LC + EMB["BURST_F"][-8:],
    ]


def voice_bits(burst: bytes) -> bitarray:
    """The 216 voice bits (three AMBE frames) of a 33-byte voice burst."""
    bits = bitarray(endian="big")
    bits.frombytes(burst[:33])
    return bits[:108] + bits[156:264]


class DmrdStream:
    """One DMRD voice stream, built frame by frame; ``slot`` is 0 for TS1 and 1 for TS2, as in pkt_gen."""

    def __init__(self, rf_src: bytes, dst_id: bytes, peer: bytes, slot: int, stream_id: bytes, *, private: bool = False) -> None:
        self.stream_id = stream_id
        self.lc = (LC_OPT_U if private else LC_OPT) + dst_id + rf_src
        self.embedding = superframe_embedding(self.lc)
        self._sdp = rf_src + dst_id + peer
        self._slot_byte = ((slot << 7) & 0xFF) | (0x40 if private else 0)
        self._seq = 0

    def _pkt(self, bits: int, payload: bitarray) -> bytes:
        pkt = b"DMRD" + bytes([self._seq]) + self._sdp + bytes([self._slot_byte | bits]) + self.stream_id + payload.tobytes() + TAIL
        self._seq = (self._seq + 1) % 0x100
        return pkt

    def header(self) -> bytes:
        lc = bptc.encode_header_lc(self.lc)
        return self._pkt(HEADBITS, lc[:98] + SLOT_TYPE["VOICE_LC_HEAD"][:10] + BS_DATA_SYNC + SLOT_TYPE["VOICE_LC_HEAD"][-10:] + lc[-98:])

    def burst(self, n: int, b0: bitarray, b1: bitarray, embedding: list[bitarray] | None = None) -> bytes:
        """Voice burst ``n`` (A-F by ``n % 6``) from its two 108-bit voice halves."""
        return self._pkt(BURSTBITS[n % 6], b0 + (embedding or self.embedding)[n % 6] + b1)

    def terminator(self) -> bytes:
        lc = bptc.encode_terminator_lc(self.lc)
        return self._pkt(TERMBITS, lc[:98] + SLOT_TYPE["VOICE_LC_TERM"][:10] + BS_DATA_SYNC + SLOT_TYPE["VOICE_LC_TERM"][-10:] + lc[-98:])
