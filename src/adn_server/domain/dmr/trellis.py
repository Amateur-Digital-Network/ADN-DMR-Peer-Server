# ADN DMR Peer Server - DMR rate 3/4 trellis
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

"""Rate 3/4 trellis code of DMR packet data (ETSI TS 102 361-1 B.2)."""

# 144 data bits -> 48 tribits + a null tribit -> 49 constellation points of an 8-state machine
# -> 98 interleaved dibits. Decoding is a hard-decision Viterbi, so a few bit errors are fixed.

from __future__ import annotations

from bitarray import bitarray

# Constellation point for (state, tribit): row = state (the previous tribit).
_ENCODE = (
    (0, 8, 4, 12, 2, 10, 6, 14),
    (4, 12, 2, 10, 6, 14, 0, 8),
    (1, 9, 5, 13, 3, 11, 7, 15),
    (5, 13, 3, 11, 7, 15, 1, 9),
    (3, 11, 7, 15, 1, 9, 5, 13),
    (7, 15, 1, 9, 5, 13, 3, 11),
    (2, 10, 6, 14, 0, 8, 4, 12),
    (6, 14, 0, 8, 4, 12, 2, 10),
)

# Constellation point -> its two dibits (symbol values).
_POINTS = (
    (+1, -1), (-1, -1), (+3, -3), (-3, -3), (-3, -1), (+3, -1), (-1, -3), (+1, -3),
    (-3, +3), (+3, +3), (-1, +1), (+1, +1), (+1, +3), (-1, +3), (+3, +1), (-3, +1),
)
_POINT_OF = {pair: i for i, pair in enumerate(_POINTS)}

# Symbol <-> the 2 bits it is sent as.
_SYMBOL_BITS = {+3: (0, 1), +1: (0, 0), -1: (1, 0), -3: (1, 1)}
_BITS_SYMBOL = {bits: sym for sym, bits in _SYMBOL_BITS.items()}

# On air, dibit i is encoder output dibit _INTERLEAVE[i].
_INTERLEAVE = tuple(
    [0, 1, 8, 9, 16, 17, 24, 25, 32, 33, 40, 41, 48, 49, 56, 57, 64, 65, 72, 73, 80, 81, 88, 89, 96, 97]
    + [2, 3, 10, 11, 18, 19, 26, 27, 34, 35, 42, 43, 50, 51, 58, 59, 66, 67, 74, 75, 82, 83, 90, 91]
    + [4, 5, 12, 13, 20, 21, 28, 29, 36, 37, 44, 45, 52, 53, 60, 61, 68, 69, 76, 77, 84, 85, 92, 93]
    + [6, 7, 14, 15, 22, 23, 30, 31, 38, 39, 46, 47, 54, 55, 62, 63, 70, 71, 78, 79, 86, 87, 94, 95]
)


def encode_trellis34(data18: bytes) -> bitarray:
    """18 bytes (144 bits) -> 196 on-air info bits."""
    if len(data18) != 18:
        raise ValueError("a rate 3/4 block carries 18 bytes")
    bits = bitarray(endian="big")
    bits.frombytes(bytes(data18))
    tribits = [(bits[i] << 2) | (bits[i + 1] << 1) | bits[i + 2] for i in range(0, 144, 3)] + [0]
    dibits: list[int] = []
    state = 0
    for tribit in tribits:
        dibits.extend(_POINTS[_ENCODE[state][tribit]])
        state = tribit
    sent = [dibits[_INTERLEAVE[i]] for i in range(98)]
    out = bitarray(endian="big")
    for symbol in sent:
        out.extend(_SYMBOL_BITS[symbol])
    return out


def decode_trellis34(info196: bitarray) -> bytes | None:
    """196 on-air info bits -> 18 bytes, correcting what Viterbi can; None if hopeless."""
    # Branch metric: on-air bits that differ from the point the branch would have sent.
    if len(info196) != 196:
        return None
    sent = [(info196[2 * i] << 1) | info196[2 * i + 1] for i in range(98)]  # 2-bit symbols as sent
    dibits = [0] * 98
    for i, symbol in enumerate(sent):
        dibits[_INTERLEAVE[i]] = symbol
    received = [(dibits[2 * k] << 2) | dibits[2 * k + 1] for k in range(49)]  # 4 bits per point

    inf = 1 << 30
    metric = [0] + [inf] * 7  # start in state 0
    back: list[list[int]] = []  # per step: previous state of each new state
    for word in received:
        new_metric = [inf] * 8
        prev = [0] * 8
        for state in range(8):
            if metric[state] >= inf:
                continue
            row = _ENCODE[state]
            for tribit in range(8):
                cost = metric[state] + _BIT_DIFF[_POINT_BITS[row[tribit]] ^ word]
                if cost < new_metric[tribit]:  # the next state is the tribit just sent
                    new_metric[tribit] = cost
                    prev[tribit] = state
        metric = new_metric
        back.append(prev)
    if metric[0] >= inf or metric[0] > 16:  # the null tribit leaves the machine in state 0
        return None
    tribits = [0] * 49
    state = 0
    for k in range(48, -1, -1):
        tribits[k] = state
        state = back[k][state]
    out = bitarray(endian="big")
    for tribit in tribits[:48]:
        out.extend(((tribit >> 2) & 1, (tribit >> 1) & 1, tribit & 1))
    return out.tobytes()


# Constellation point -> its 4 on-air bits; bit count of 0..15.
_POINT_BITS = tuple(
    (_SYMBOL_BITS[a][0] << 3) | (_SYMBOL_BITS[a][1] << 2) | (_SYMBOL_BITS[b][0] << 1) | _SYMBOL_BITS[b][1]
    for a, b in _POINTS
)
_BIT_DIFF = tuple(i.bit_count() for i in range(16))
