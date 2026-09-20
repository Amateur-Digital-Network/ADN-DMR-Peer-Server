# ADN DMR Peer Server - infrastructure pcap reader
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

"""Read UDP datagrams out of a classic pcap file, without dependencies.

Enough of the format to walk a ``tcpdump -w`` capture and hand back what a
socket would have received: the payload, who sent it, which port it arrived on
and when. Anything that is not IPv4/IPv6 UDP is skipped.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

PCAP_MAGIC_US = 0xA1B2C3D4  # timestamps in microseconds
PCAP_MAGIC_NS = 0xA1B23C4D  # timestamps in nanoseconds
PCAPNG_MAGIC = 0x0A0D0D0A

LINKTYPE_NULL = 0
LINKTYPE_ETHERNET = 1
LINKTYPE_RAW = 101
LINKTYPE_LINUX_SLL = 113
LINKTYPE_LINUX_SLL2 = 276
LINKTYPE_IPV4 = 228
LINKTYPE_IPV6 = 229


class CaptureError(Exception):
    """The file is not a capture this reader can walk."""


@dataclass(frozen=True)
class CapturedDatagram:
    """One UDP datagram as it appeared on the wire."""

    timestamp: float
    source: tuple[str, int]
    destination: tuple[str, int]
    payload: bytes


def _ipv4(raw: bytes) -> str:
    return ".".join(str(b) for b in raw)


def _ipv6(raw: bytes) -> str:
    parts = [f"{raw[i] << 8 | raw[i + 1]:x}" for i in range(0, 16, 2)]
    return ":".join(parts)


def _strip_link_layer(frame: bytes, linktype: int) -> tuple[bytes, int] | None:
    """Return the network-layer payload and its ethertype-ish family."""
    if linktype == LINKTYPE_ETHERNET:
        if len(frame) < 14:
            return None
        ethertype = int.from_bytes(frame[12:14], "big")
        offset = 14
        while ethertype in (0x8100, 0x88A8):  # VLAN tags
            if len(frame) < offset + 4:
                return None
            ethertype = int.from_bytes(frame[offset + 2 : offset + 4], "big")
            offset += 4
        return frame[offset:], ethertype
    if linktype == LINKTYPE_LINUX_SLL:
        if len(frame) < 16:
            return None
        return frame[16:], int.from_bytes(frame[14:16], "big")
    if linktype == LINKTYPE_LINUX_SLL2:
        # `tcpdump -i any` on a recent libpcap: protocol first, 20-byte header
        if len(frame) < 20:
            return None
        return frame[20:], int.from_bytes(frame[:2], "big")
    if linktype == LINKTYPE_NULL:
        if len(frame) < 4:
            return None
        family = int.from_bytes(frame[:4], "little")
        return frame[4:], 0x0800 if family == 2 else 0x86DD
    if linktype in (LINKTYPE_RAW, LINKTYPE_IPV4, LINKTYPE_IPV6):
        if not frame:
            return None
        version = frame[0] >> 4
        return frame, 0x0800 if version == 4 else 0x86DD
    return None


def _udp_from_ip(packet: bytes, ethertype: int) -> tuple[str, str, bytes] | None:
    """Return ``(src_ip, dst_ip, udp_segment)`` for an IPv4/IPv6 UDP packet."""
    if ethertype == 0x0800:
        if len(packet) < 20 or packet[0] >> 4 != 4:
            return None
        header_len = (packet[0] & 0x0F) * 4
        if packet[9] != 17 or len(packet) < header_len + 8:  # 17 = UDP
            return None
        return _ipv4(packet[12:16]), _ipv4(packet[16:20]), packet[header_len:]
    if ethertype == 0x86DD:
        if len(packet) < 40 or packet[0] >> 4 != 6:
            return None
        if packet[6] != 17 or len(packet) < 48:  # no extension-header walking
            return None
        return _ipv6(packet[8:24]), _ipv6(packet[24:40]), packet[40:]
    return None


def read_udp(path: str | Path) -> Iterator[CapturedDatagram]:
    """Walk a capture and yield its UDP datagrams in order."""
    path = Path(path)
    with path.open("rb") as fh:
        header = fh.read(24)
        if len(header) < 24:
            raise CaptureError(f"{path}: too short to be a capture")
        magic = int.from_bytes(header[:4], "big")
        if magic == PCAPNG_MAGIC:
            raise CaptureError(
                f"{path}: pcapng is not supported; convert it first "
                "(editcap -F pcap in.pcapng out.pcap)"
            )
        if magic in (PCAP_MAGIC_US, PCAP_MAGIC_NS):
            endian = ">"
        else:
            magic = int.from_bytes(header[:4], "little")
            if magic not in (PCAP_MAGIC_US, PCAP_MAGIC_NS):
                raise CaptureError(f"{path}: not a pcap file")
            endian = "<"
        divisor = 1_000_000_000 if magic == PCAP_MAGIC_NS else 1_000_000
        linktype = struct.unpack(endian + "I", header[20:24])[0]

        record = struct.Struct(endian + "IIII")
        while True:
            raw = fh.read(record.size)
            if len(raw) < record.size:
                return
            seconds, fraction, captured_len, _original_len = record.unpack(raw)
            frame = fh.read(captured_len)
            if len(frame) < captured_len:
                return
            stripped = _strip_link_layer(frame, linktype)
            if stripped is None:
                continue
            packet, ethertype = stripped
            addresses = _udp_from_ip(packet, ethertype)
            if addresses is None:
                continue
            source_ip, destination_ip, segment = addresses
            if len(segment) < 8:
                continue
            source_port, destination_port, length = struct.unpack(">HHH", segment[:6])
            payload = segment[8 : max(8, length)] if length >= 8 else segment[8:]
            yield CapturedDatagram(
                timestamp=seconds + fraction / divisor,
                source=(source_ip, source_port),
                destination=(destination_ip, destination_port),
                payload=payload,
            )


__all__ = [
    "CaptureError",
    "CapturedDatagram",
    "read_udp",
]
