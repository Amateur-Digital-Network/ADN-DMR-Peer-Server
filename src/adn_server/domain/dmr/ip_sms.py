# ADN DMR Peer Server - DMR IP data and text messages
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

"""IPv4/UDP between DMR IDs and the text formats on top: MOTOTRBO TMS (UDP 4007) and "DMR standard" SMS (UDP 5016)."""

# Addresses come from DMR IDs: radio 12.x.y.z, gateway 13.x.y.z. Layouts as captured from radios
# and BrandMeister.

from __future__ import annotations

from dataclasses import dataclass

PORT_TMS = 4007

RADIO_NET = 0x0C
GATEWAY_NET = 0x0D


def _ones_complement(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    total = 0
    for i in range(0, len(data), 2):
        total += (data[i] << 8) | data[i + 1]
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def ip_udp(src_id: int, dst_id: int, port: int, payload: bytes, ip_id: int, src_net: int = RADIO_NET,
           dst_net: int = RADIO_NET) -> bytes:
    """IPv4/UDP datagram between two DMR IDs (same port both ends); radio to radio by default."""
    src_ip = bytes([src_net]) + src_id.to_bytes(3, "big")
    dst_ip = bytes([dst_net]) + dst_id.to_bytes(3, "big")
    udp_len = 8 + len(payload)
    ports = port.to_bytes(2, "big") * 2
    pseudo = src_ip + dst_ip + b"\x00\x11" + udp_len.to_bytes(2, "big")
    udp_check = _ones_complement(pseudo + ports + udp_len.to_bytes(2, "big") + b"\x00\x00" + payload)
    udp = ports + udp_len.to_bytes(2, "big") + udp_check.to_bytes(2, "big") + payload
    header = (
        bytes([0x45, 0x01]) + (20 + len(udp)).to_bytes(2, "big") + (ip_id & 0xFFFF).to_bytes(2, "big")
        + b"\x00\x00\x40\x11\x00\x00" + src_ip + dst_ip
    )
    check = _ones_complement(header)
    return header[:10] + check.to_bytes(2, "big") + header[12:] + udp


@dataclass(frozen=True)
class UdpMessage:
    radio_id: int  # from the source address
    port: int  # destination port
    payload: bytes
    dst_net: int = RADIO_NET  # first octet of the address the radio wrote to


def parse_ip_udp(packet: bytes) -> UdpMessage | None:
    """The UDP message of a radio's IPv4 datagram (packet may carry pad octets after it)."""
    if len(packet) < 28 or packet[0] != 0x45 or packet[9] != 0x11:
        return None
    total = int.from_bytes(packet[2:4], "big")
    if total < 28 or total > len(packet):
        return None
    return UdpMessage(int.from_bytes(packet[13:16], "big"), int.from_bytes(packet[22:24], "big"),
                      bytes(packet[28:total]), packet[16])


def tms_text(payload: bytes) -> str:
    """The longest run of UTF-16 printable ASCII in a TMS payload (either byte order)."""

    def longest(big_endian: bool) -> str:
        best, i, n = "", 0, len(payload)
        while i < n - 1:
            j, chars = i, []
            while j < n - 1:
                hi, lo = (payload[j], payload[j + 1]) if big_endian else (payload[j + 1], payload[j])
                if hi != 0 or not 0x20 <= lo <= 0x7E:
                    break
                chars.append(chr(lo))
                j += 2
            if chars:
                best = max(best, "".join(chars), key=len)
                i = j
            else:
                i += 1
        return best

    le, be = longest(False), longest(True)
    return (le if len(le) >= len(be) else be).strip()


# ------------------------------------------------------------ TMS (text messages)
#
#   0-1  length of what follows      2  0xA0 (extension + reserved) | 0x40 ack wanted | type (0 = text)
#   3    address length (0)          4  0x80 | sequence number      5  0x04 = UCS-2 LE
#   6..  "\r\n" + text, UTF-16 LE
# An acknowledgement is 00 03 BF 00 80|seq (control PDU type 0x1F).

TMS_MAX_CHARS = 140
_TMS_ACK_TYPE = 0x1F


@dataclass(frozen=True)
class TmsMessage:
    text: str
    sequence: int
    ack_required: bool
    is_ack: bool = False


def tms_parse(payload: bytes) -> TmsMessage | None:
    if len(payload) < 4 or int.from_bytes(payload[:2], "big") != len(payload) - 2:
        return None
    flags = payload[2]
    i = 4 + payload[3]  # after the address field
    sequence = 0
    if flags & 0x80 and i < len(payload):  # extension headers: sequence (+ encoding), each 0x80 = one more
        sequence = payload[i] & 0x1F
        while i < len(payload) and payload[i] & 0x80:
            i += 1
        i += 1
    if flags & 0x1F == _TMS_ACK_TYPE:
        return TmsMessage("", sequence, False, True)
    body = payload[i:]
    if len(body) % 2:
        body = body[:-1]
    text = body.decode("utf-16-le", errors="replace").replace("\x00", "")
    text = text.removeprefix("\r\n")
    text = text.strip()
    if not text or any(ord(c) > 0x2FF for c in text):  # misaligned (unexpected header): best ASCII run
        text = tms_text(payload) or text
    return TmsMessage(text, sequence, bool(flags & 0x40))


def tms_ack(sequence: int) -> bytes:
    return bytes([0x00, 0x03, 0xBF, 0x00, 0x80 | (sequence & 0x1F)])


def tms_simple_text(text: str, sequence: int = 0) -> bytes:
    body = ("\r\n" + text[:TMS_MAX_CHARS]).encode("utf-16-le")
    pdu = bytes([0xA0, 0x00, 0x80 | (sequence & 0x1F), 0x04]) + body
    return len(pdu).to_bytes(2, "big") + pdu


# ------------------------------------------------------------ "DMR standard" SMS (UDP 5016)
# Hytera, Anytone, TYT...: same IPv4/UDP carriage, body 00 0D 00 0A + text UTF-16 LE.

PORT_DMR_STANDARD_SMS = 5016
_STD_SUB_HEADER = b"\x00\x0d\x00\x0a"


def standard_sms_parse(payload: bytes) -> str | None:
    if not payload.startswith(_STD_SUB_HEADER):
        return None
    body = payload[len(_STD_SUB_HEADER):]
    if len(body) % 2:
        body = body[:-1]
    return body.decode("utf-16-le", errors="replace").replace("\x00", "").strip()


def standard_sms(text: str) -> bytes:
    return _STD_SUB_HEADER + text[:TMS_MAX_CHARS].encode("utf-16-le")
