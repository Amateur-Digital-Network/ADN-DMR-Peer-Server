# ADN DMR Peer Server - DMR data header
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

"""DMR data header (ETSI TS 102 361-1 9.2.6): response, unconfirmed, confirmed, Hytera short data."""

from __future__ import annotations

from dataclasses import dataclass

from .crc import crc16_data_header

DPF_RESPONSE = 0b0001
DPF_UNCONFIRMED = 0b0010
DPF_CONFIRMED = 0b0011
DPF_SHORT_DATA_DEFINED = 0b1101

SAP_IP = 0b0100  # MOTOTRBO ARS / LRRP / TMS
SAP_SHORT_DATA = 0b1010  # Hytera HDAP

FSN_SINGLE_CONFIRMED = 0b1000  # last (and only) fragment of a confirmed packet

RESPONSE_ACK = (0, 1)  # class 0, type 1


@dataclass(frozen=True)
class DataHeader:
    dpf: int
    dst: int
    src: int
    sap: int = SAP_IP
    group: bool = False
    response_requested: bool = False
    # unconfirmed / confirmed
    pad_octets: int = 0
    full_message: bool = True
    blocks_to_follow: int = 0
    resync: bool = False
    send_seq: int = 0
    fsn: int = 0
    # response
    response_class: int = 0
    response_type: int = 0
    response_status: int = 0
    # short data defined
    appended_blocks: int = 0
    dd_format: int = 0
    sarq: bool = False
    bit_padding: int = 0

    @property
    def confirmed(self) -> bool:
        return self.dpf == DPF_CONFIRMED

    @property
    def blocks(self) -> int:
        """Data blocks after the header, whatever the format."""
        return self.appended_blocks if self.dpf == DPF_SHORT_DATA_DEFINED else self.blocks_to_follow

    def to_bytes(self) -> bytes:
        o = bytearray(12)
        o[0] = (self.group << 7) | (self.response_requested << 6) | (self.dpf & 0x0F)
        o[2:5] = self.dst.to_bytes(3, "big")
        o[5:8] = self.src.to_bytes(3, "big")
        if self.dpf in (DPF_UNCONFIRMED, DPF_CONFIRMED):
            o[0] |= ((self.pad_octets >> 4) & 1) << 4
            o[1] = (self.sap << 4) | (self.pad_octets & 0x0F)
            o[8] = (self.full_message << 7) | (self.blocks_to_follow & 0x7F)
            if self.dpf == DPF_CONFIRMED:
                o[9] = (self.resync << 7) | ((self.send_seq & 7) << 4) | (self.fsn & 0x0F)
            else:
                o[9] = self.fsn & 0x0F
        elif self.dpf == DPF_RESPONSE:
            o[1] = self.sap << 4
            o[8] = self.blocks_to_follow & 0x7F
            o[9] = ((self.response_class & 3) << 6) | ((self.response_type & 7) << 3) | (self.response_status & 7)
        elif self.dpf == DPF_SHORT_DATA_DEFINED:
            o[0] |= ((self.appended_blocks >> 4) & 3) << 4
            o[1] = (self.sap << 4) | (self.appended_blocks & 0x0F)
            o[8] = ((self.dd_format & 0x3F) << 2) | (self.sarq << 1) | int(self.full_message)
            o[9] = self.bit_padding & 0xFF
        else:
            raise ValueError(f"data packet format {self.dpf} not supported")
        o[10:12] = crc16_data_header(bytes(o[:10])).to_bytes(2, "big")
        return bytes(o)

    @staticmethod
    def from_bytes(data: bytes) -> DataHeader | None:
        """None when the CRC fails or the format is not one of the four."""
        if len(data) < 12 or crc16_data_header(data[:10]) != int.from_bytes(data[10:12], "big"):
            return None
        o = data
        dpf = o[0] & 0x0F
        common = {
            "dpf": dpf,
            "group": bool(o[0] & 0x80),
            "response_requested": bool(o[0] & 0x40),
            "dst": int.from_bytes(o[2:5], "big"),
            "src": int.from_bytes(o[5:8], "big"),
            "sap": o[1] >> 4,
        }
        if dpf in (DPF_UNCONFIRMED, DPF_CONFIRMED):
            confirmed = dpf == DPF_CONFIRMED
            return DataHeader(
                **common,
                pad_octets=(((o[0] >> 4) & 1) << 4) | (o[1] & 0x0F),
                full_message=bool(o[8] & 0x80),
                blocks_to_follow=o[8] & 0x7F,
                resync=bool(o[9] & 0x80) if confirmed else False,
                send_seq=(o[9] >> 4) & 7 if confirmed else 0,
                fsn=o[9] & 0x0F,
            )
        if dpf == DPF_RESPONSE:
            return DataHeader(
                **common,
                blocks_to_follow=o[8] & 0x7F,
                response_class=o[9] >> 6,
                response_type=(o[9] >> 3) & 7,
                response_status=o[9] & 7,
            )
        if dpf == DPF_SHORT_DATA_DEFINED:
            return DataHeader(
                **common,
                appended_blocks=(((o[0] >> 4) & 3) << 4) | (o[1] & 0x0F),
                dd_format=o[8] >> 2,
                sarq=bool(o[8] & 2),
                full_message=bool(o[8] & 1),
                bit_padding=o[9],
            )
        return None


def response_ack(dst: int, src: int, sap: int, send_seq: int = 0) -> DataHeader:
    """Link ACK of a confirmed packet or of short data with SARQ (status = N(S))."""
    cls, typ = RESPONSE_ACK
    return DataHeader(
        dpf=DPF_RESPONSE, dst=dst, src=src, sap=sap,
        response_class=cls, response_type=typ, response_status=send_seq & 7,
    )
