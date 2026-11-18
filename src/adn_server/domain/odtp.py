# ADN DMR Peer Server - ODTP / Rewind codec
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

"""ODTP (Open DMR Terminal Protocol) over BrandMeister's Rewind transport: UDP, little-endian."""

# Datagram: "REWIND01" | type u16 | flags u16 | sequence u32 | length u16 | data. Types and
# structures from BrandMeister's Rewind.h (DigestPlay, ODMRTP branch).

from __future__ import annotations

import struct

from .dmr import rs129

SIGN = b"REWIND01"
HEADER = struct.Struct("<8sHHIH")

KEEP_ALIVE = 0x0000
CLOSE = 0x0001
CHALLENGE = 0x0002
AUTHENTICATION = 0x0003
BUSY_NOTICE = 0x0200
SUBSCRIPTION = 0x0901
CANCELLING = 0x0902
DMR_DATA_BASE = 0x0910
VOICE_HEADER = DMR_DATA_BASE + 1
VOICE_TERMINATOR = DMR_DATA_BASE + 2
DMR_AUDIO_FRAME = 0x0920
DMR_EMBEDDED_DATA = 0x0927
MESSAGE_TEXT = 0x0A10
MESSAGE_STATUS = 0x0A11
LOCATION_REPORT = 0x0A20

FLAG_REAL_TIME_1 = 1
SERVICE_OPEN_TERMINAL = 0x21
SESSION_TYPE_GROUP_VOICE = 7
# Real-time data has its own sequence; the flag goes up to the audio frame (embedded data counts
# with the voice but carries no flag, as Z3DMR sends it).
REAL_TIME_SEQUENCE = range(DMR_DATA_BASE, DMR_EMBEDDED_DATA + 1)
REAL_TIME_FLAGGED = range(DMR_DATA_BASE, DMR_AUDIO_FRAME + 1)

AUDIO_PAYLOAD = 27  # three 72-bit AMBE frames, one 60 ms burst
LC_LEN = 9
FLCO_GROUP = 0
FLCO_PRIVATE = 3
EMBEDDED_FLCOS = frozenset({4, 5, 6, 7, 8})  # Talker Alias header and blocks 1-3, GPS info

TEXT_GROUP = 0x80
LOCATION_FORMAT_NMEA = 0

_KEEP_ALIVE = struct.Struct("<IB")  # number, service; the description follows
_SUBSCRIPTION = struct.Struct("<II")  # type, number
_TEXT = struct.Struct("<IIIHH")  # reserved, source, destination, option, length
_STATUS = struct.Struct("<IIIB")  # reserved, source, destination, status
_LOCATION = struct.Struct("<IIH")  # reserved, format, length


def pack(msg_type: int, seq: int, payload: bytes = b"", flags: int = 0) -> bytes:
    return HEADER.pack(SIGN, msg_type, flags, seq & 0xFFFFFFFF, len(payload)) + payload


def unpack(data: bytes) -> tuple[int, int, int, bytes] | None:
    """(type, flags, sequence, payload) of one Rewind datagram, or None."""
    if len(data) < HEADER.size or not data.startswith(SIGN):
        return None
    _, msg_type, flags, seq, length = HEADER.unpack_from(data)
    payload = data[HEADER.size:HEADER.size + length]
    if len(payload) != length:
        return None
    return msg_type, flags, seq, payload


def parse_keep_alive(payload: bytes) -> tuple[int, int, str] | None:
    """(number, service, description) of a terminal's keep-alive."""
    if len(payload) < _KEEP_ALIVE.size:
        return None
    number, service = _KEEP_ALIVE.unpack_from(payload)
    return number, service, payload[_KEEP_ALIVE.size:].split(b"\x00", 1)[0].decode("utf-8", "replace")


def parse_subscription(payload: bytes) -> tuple[int, int] | None:
    """(session type, number) of a SUBSCRIPTION / CANCELLING."""
    return _SUBSCRIPTION.unpack_from(payload) if len(payload) >= _SUBSCRIPTION.size else None


def full_lc(dst: int, src: int, private: bool) -> bytes:
    return bytes([FLCO_PRIVATE if private else FLCO_GROUP, 0, 0]) + dst.to_bytes(3, "big") + src.to_bytes(3, "big")


def voice_header(dst: int, src: int, private: bool) -> bytes:
    """The 12-byte ODTP voice header: the LC plus its RS(12,9) header parity."""
    lc = full_lc(dst, src, private)
    return lc + rs129.lc_header_encode(lc)


def parse_voice_header(payload: bytes) -> tuple[int, int, bool] | None:
    """(destination, source, private) of an ODTP voice header."""
    if len(payload) < LC_LEN:
        return None
    return int.from_bytes(payload[3:6], "big"), int.from_bytes(payload[6:9], "big"), (payload[0] & 0x3F) == FLCO_PRIVATE


def text_message(src: int, dst: int, text: str, group: bool = False) -> bytes:
    body = text.encode("utf-16-le")
    return _TEXT.pack(0, src, dst, TEXT_GROUP if group else 0, len(body)) + body


def parse_text_message(payload: bytes) -> tuple[int, int, bool, str] | None:
    """(source, destination, group, text) of a text message."""
    if len(payload) < _TEXT.size:
        return None
    _, src, dst, option, length = _TEXT.unpack_from(payload)
    body = payload[_TEXT.size:_TEXT.size + length]
    if len(body) % 2:
        body = body[:-1]
    return src, dst, bool(option & TEXT_GROUP), body.decode("utf-16-le", errors="replace").replace("\x00", "")


def text_status(src: int, dst: int, status: int) -> bytes:
    """Delivery status: ``src`` is the addressee of the text, ``dst`` its sender."""
    return _STATUS.pack(0, src, dst, status & 0xFF)


def parse_text_status(payload: bytes) -> tuple[int, int, int] | None:
    if len(payload) < _STATUS.size:
        return None
    _, src, dst, status = _STATUS.unpack_from(payload)
    return src, dst, status


def parse_location_report(payload: bytes) -> bytes | None:
    """The NMEA sentence of a location report, without its trailing zero."""
    if len(payload) < _LOCATION.size:
        return None
    _, fmt, length = _LOCATION.unpack_from(payload)
    if fmt != LOCATION_FORMAT_NMEA:
        return None
    return payload[_LOCATION.size:_LOCATION.size + length].split(b"\x00", 1)[0]
