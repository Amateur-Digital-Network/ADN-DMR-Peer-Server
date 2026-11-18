# ADN DMR Peer Server - Hytera HDAP
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

"""Hytera HDAP messages and its TMP text messages."""

# | service (bit 7 = reliable) | opcode (2) | length (2) | payload | checksum | 0x03 |, checksum =
# ((~sum(opcode .. payload) & 0xFF) + 0x33) & 0xFF. A Hytera radio sends it as DMR short data after
# two octets (service, retry number); other radios carry TMP over UDP 5016.

from __future__ import annotations

from dataclasses import dataclass

SERVICE_TMP = 0x09  # text messages


@dataclass(frozen=True)
class Hdap:
    service: int  # without the reliable bit
    opcode: int
    payload: bytes
    reliable: bool = False


def checksum(opcode_to_payload: bytes) -> int:
    return ((~sum(opcode_to_payload) & 0xFF) + 0x33) & 0xFF


def build(service: int, opcode: int, payload: bytes, reliable: bool = False) -> bytes:
    body = opcode.to_bytes(2, "big") + len(payload).to_bytes(2, "big") + payload
    return bytes([service | (0x80 if reliable else 0)]) + body + bytes([checksum(body), 0x03])


def find(buf: bytes) -> tuple[Hdap, int] | None:
    """First valid HDAP message in ``buf`` (envelope, length, checksum and end checked) and its offset."""
    services = (SERVICE_TMP,)
    for off in range(max(0, len(buf) - 6)):
        if buf[off] & 0x7F not in services:
            continue
        length = int.from_bytes(buf[off + 3:off + 5], "big")
        end = off + 7 + length
        if end > len(buf) or buf[end - 1] != 0x03:
            continue
        body = buf[off + 1:off + 5 + length]
        if checksum(body) != buf[end - 2]:
            continue
        return Hdap(buf[off] & 0x7F, int.from_bytes(body[:2], "big"), bytes(body[4:]), bool(buf[off] & 0x80)), off
    return None


def radio_ip(radio_id: int, subnet: int = 10) -> bytes:
    return bytes([subnet]) + radio_id.to_bytes(3, "big")


# ------------------------------------------------------------ TMP (text messages)
# Opcode = flags (bit 7: 0 = ACK wanted, bit 6: option field) + service type.
# Payload: request ID (4), destination IP (4), source IP (4), text UTF-16 LE.
# The ACK (0xA2) carries request ID, destination, source and a result (0 = OK).

TMP_PRIVATE_MESSAGE = 0xA1
TMP_PRIVATE_MESSAGE_ACK = 0xA2
_TMP_NO_ACK = 0x80
TMP_MAX_CHARS = 140


@dataclass(frozen=True)
class TextMessage:
    request_id: int
    dst_id: int
    src_id: int
    text: str
    ack_required: bool


def text_message(msg: Hdap) -> TextMessage | None:
    if msg.service != SERVICE_TMP or msg.opcode & 0xFF != TMP_PRIVATE_MESSAGE or len(msg.payload) < 12:
        return None
    p, option_len = msg.payload, 0
    if msg.opcode & 0x4000:  # option field: its length first, the option itself after the text
        option_len, p = int.from_bytes(p[0:2], "big"), p[2:]
    if len(p) < 12 + option_len:
        return None
    start, end = 12, len(p) - option_len
    body = p[start:end]
    if len(body) % 2:
        body = body[:-1]
    return TextMessage(int.from_bytes(p[0:4], "big"), int.from_bytes(p[5:8], "big"), int.from_bytes(p[9:12], "big"),
                       body.decode("utf-16-le", errors="replace").replace("\x00", "").strip(),
                       not msg.opcode & 0x8000)


def text_ack(request_id: int, dst_id: int, src_id: int, result: int = 0) -> bytes:
    """ACK of a private message: to its sender (``dst_id``), from its addressee (``src_id``)."""
    payload = (request_id & 0xFFFFFFFF).to_bytes(4, "big") + radio_ip(dst_id) + radio_ip(src_id) + bytes([result])
    return build(SERVICE_TMP, (_TMP_NO_ACK << 8) | TMP_PRIVATE_MESSAGE_ACK, payload)


def text(request_id: int, dst_id: int, src_id: int, message: str) -> bytes:
    """A private message to ``dst_id`` that asks for no ACK."""
    payload = ((request_id & 0xFFFFFFFF).to_bytes(4, "big") + radio_ip(dst_id) + radio_ip(src_id)
               + message[:TMP_MAX_CHARS].encode("utf-16-le"))
    return build(SERVICE_TMP, (_TMP_NO_ACK << 8) | TMP_PRIVATE_MESSAGE, payload)
