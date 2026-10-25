#!/usr/bin/env python
# ADN DMR Peer Server - domain dmr crc
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

#
# Ported from dmr_utils3 / HBlink (N0MJS). Original license:
#
###############################################################################
#   Copyright (C) 2016-2018  Cortney T. Buffington, N0MJS <n0mjs@me.com>
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


from bitarray import bitarray

# Does anybody read this stuff? There's a PEP somewhere that says I should do this.
__author__     = 'Cortney T. Buffington, N0MJS'
__copyright__  = 'Copyright (c) 2016-2018 Cortney T. Buffington, N0MJS and the K0USY Group'
__credits__    = 'Jonathan Naylor, G4KLX'
__license__    = 'GNU GPLv3'
__maintainer__ = 'Cort Buffington, N0MJS'
__email__      = 'n0mjs@me.com'


def csum5(_data):
    accum = 0
    assert len(_data) == 9, 'csum5 expected 9 bytes of data and got something else'

    for i in range(9):
        accum += _data[i]
    accum = bytes([accum % 31])
    csum = bitarray()
    csum.frombytes(accum)
    del csum[0:3]

    return csum



# Packet data CRCs (ETSI TS 102 361-1 B.3), checked against MOTOTRBO, Hytera and BrandMeister captures.
_MASK_DATA_HEADER = 0xCCCC
_MASK_CSBK = 0xA5A5


def _crc_ccitt(data):
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def crc16_data_header(ten_bytes):
    return _crc_ccitt(ten_bytes) ^ 0xFFFF ^ _MASK_DATA_HEADER


def crc16_csbk(ten_bytes):
    return _crc_ccitt(ten_bytes) ^ 0xFFFF ^ _MASK_CSBK


def crc32_data(user_and_pad):
    """Packet CRC-32 as the 4 octets that end the last block (byte-swapped pairs in, little-endian out)."""
    data = bytearray(user_and_pad)
    tail = b""
    if len(data) % 2:
        tail, data = bytes(data[-1:]), data[:-1]
    data[0::2], data[1::2] = data[1::2], data[0::2]
    reg = 0
    for byte in bytes(data) + tail:
        for i in range(7, -1, -1):
            msb = (reg >> 31) & 1
            reg = (reg << 1) & 0xFFFFFFFF
            if msb ^ ((byte >> i) & 1):
                reg ^= 0x04C11DB7
    return reg.to_bytes(4, "little")


def crc9_block(data, dbsn, crc32=None):
    """CRC-9 of a confirmed data block: over its data, the CRC-32 octets (last block) and the 7-bit DBSN."""
    bits = []
    for byte in bytes(data) + (crc32 or b""):
        bits.extend((byte >> i) & 1 for i in range(7, -1, -1))
    bits.extend((dbsn >> i) & 1 for i in range(6, -1, -1))
    reg = 0
    for bit in bits:
        feedback = ((reg >> 8) & 1) ^ bit
        reg = (reg << 1) & 0x1FF
        if feedback:
            reg ^= 0x059
    return reg


if __name__ == '__main__':

    message = b'\x00\x10\x20\x00\x0c\x30\x2f\x9b\xe5'

    result = csum5(message)
    print(result, type(result))
