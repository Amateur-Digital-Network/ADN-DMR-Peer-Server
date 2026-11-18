# ADN DMR Peer Server - DMR packet data bursts
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

"""DMR packet data bursts: CSBK preambles, data header and rate 1/2 or 3/4 blocks, as DMRD frames."""

from __future__ import annotations

import os
from dataclasses import dataclass

from bitarray import bitarray

from .bptc import decode_19696, encode_19696, interleave_19696
from .const import BS_DATA_SYNC, SLOT_TYPE
from .crc import crc9_block, crc16_csbk, crc32_data
from .frames import dmrd_frame
from .trellis import decode_trellis34, encode_trellis34

DT_CSBK = 3
DT_DATA_HEADER = 6
DT_RATE12 = 7
DT_RATE34 = 8

_MIDDLE = {
    dtype: SLOT_TYPE[name][:10] + BS_DATA_SYNC + SLOT_TYPE[name][-10:]
    for dtype, name in ((DT_CSBK, "CSBK"), (DT_DATA_HEADER, "DATA_HEAD"), (DT_RATE12, "1/2_DATA"), (DT_RATE34, "3/4_DATA"))
}


def info_bits(payload33: bytes) -> bitarray:
    """The 196 info bits of a data burst (slot type and sync skipped)."""
    bits = bitarray(endian="big")
    bits.frombytes(bytes(payload33[:33]))
    return bits[0:98] + bits[166:264]


def make_burst(info196: bitarray, dtype: int) -> bytes:
    return (info196[0:98] + _MIDDLE[dtype] + info196[98:196]).tobytes()


def _bptc_burst(twelve: bytes, dtype: int) -> bytes:
    return make_burst(interleave_19696(encode_19696(bytes(twelve))), dtype)


@dataclass(frozen=True)
class Block:
    data: bytes  # user data octets (the CRC-32 of the last block not included)
    dbsn: int = 0
    crc_ok: bool = True  # CRC-9 of a confirmed block; always True when unconfirmed
    crc32: bytes = b""  # last block only


def decode_block(payload33: bytes, dtype: int, confirmed: bool, last: bool) -> Block | None:
    """One rate 1/2 or rate 3/4 data burst -> its block; None if undecodable."""
    info = info_bits(payload33)
    if dtype == DT_RATE12:
        raw = decode_19696(info)
    elif dtype == DT_RATE34:
        raw = decode_trellis34(info)
        if raw is None:
            return None
    else:
        return None
    if confirmed:
        dbsn = raw[0] >> 1
        crc9 = ((raw[0] & 1) << 8) | raw[1]
        body = raw[2:]
    else:
        dbsn, crc9, body = 0, None, raw
    data, crc32 = (body[:-4], body[-4:]) if last else (body, b"")
    ok = crc9 is None or crc9_block(data, dbsn, crc32 or None) == crc9
    return Block(data, dbsn, ok, crc32)


def _block_bytes(chunk: bytes, dbsn: int, confirmed: bool, crc32: bytes) -> bytes:
    if not confirmed:
        return chunk + crc32
    crc9 = crc9_block(chunk, dbsn, crc32 or None)
    return bytes([((dbsn & 0x7F) << 1) | (crc9 >> 8), crc9 & 0xFF]) + chunk + crc32


def build_blocks(user: bytes, rate34: bool, confirmed: bool) -> tuple[list[tuple[int, bytes]], int]:
    """User data -> (data type, burst) of its blocks (padded, CRC-32 in the last) and the pad octets."""
    size = (18 if rate34 else 12) - (2 if confirmed else 0)
    n = 1
    while size * n - 4 < len(user):
        n += 1
    pad = size * n - 4 - len(user)
    body = bytes(user) + b"\x00" * pad
    crc32 = crc32_data(body)
    bursts = []
    for i in range(n):
        last = i == n - 1
        chunk = body[i * size:(i + 1) * size] if not last else body[i * size:]
        raw = _block_bytes(chunk, i, confirmed, crc32 if last else b"")
        if rate34:
            bursts.append((DT_RATE34, make_burst(encode_trellis34(raw), DT_RATE34)))
        else:
            bursts.append((DT_RATE12, _bptc_burst(raw, DT_RATE12)))
    return bursts, pad


def decode_header_burst(payload33: bytes) -> bytes:
    return decode_19696(info_bits(payload33))


def preamble_burst(src: int, dst: int, blocks_to_follow: int, group: bool = False) -> bytes:
    """CSBK preamble (opcode 0x3D) announcing the blocks that follow."""
    b = bytearray(12)
    b[0], b[1], b[2], b[3] = 0xBD, 0x00, 0xC0 if group else 0x80, blocks_to_follow & 0xFF
    b[4:7] = dst.to_bytes(3, "big")
    b[7:10] = src.to_bytes(3, "big")
    b[10:12] = crc16_csbk(bytes(b[:10])).to_bytes(2, "big")
    return _bptc_burst(bytes(b), DT_CSBK)


def data_frames(
    header12: bytes,
    blocks: list[tuple[int, bytes]],
    src: int,
    dst: int,
    peer: int,
    slot: int,
    preambles: int = 0,
    group: bool = False,
) -> list[bytes]:
    """DMRD frames of one packet (``slot`` 1 or 2): CSBK preambles, header, blocks; unit data unless ``group``."""
    sid = os.urandom(4)
    bursts = [(DT_CSBK, preamble_burst(src, dst, preambles - i + len(blocks), group)) for i in range(preambles)]
    bursts.append((DT_DATA_HEADER, _bptc_burst(header12, DT_DATA_HEADER)))
    bursts.extend(blocks)
    ids = (src.to_bytes(3, "big"), dst.to_bytes(3, "big"), peer.to_bytes(4, "big"))
    call = (0x80 if slot == 2 else 0x00) | (0 if group else 0x40) | (2 << 4)  # data sync
    return [dmrd_frame(i, *ids, call | dtype, sid, payload) for i, (dtype, payload) in enumerate(bursts)]
