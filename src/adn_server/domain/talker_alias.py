# ADN DMR Peer Server - DMR Talker Alias (ETSI / MMDVMHost)
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

"""Talker Alias encode/decode and HBP DMRA packet builders (UTF-8 format 2)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .dmr import bptc, decode

if TYPE_CHECKING:
    from bitarray import bitarray

TALKER_ALIAS_MAX_LEN = 29
TA_FORMAT_7BIT = 0
TA_FORMAT_ISO8 = 1
TA_FORMAT_UTF8 = 2
DMRA_OPCODE = b"DMRA"
DMRA_PACKET_LEN = 15
DMRA_BLOCK_COUNT = 4
DMRA_PAYLOAD_LEN = 7
DMRA_BUF_LEN = DMRA_BLOCK_COUNT * DMRA_PAYLOAD_LEN
FLCO_TALKER_ALIAS_HEADER = 4
FLCO_TALKER_ALIAS_BLOCK3 = 7
TA_EMB_LC_LEN = 9


def truncate_talker_alias(text: str) -> str:
    """Hard-cap at protocol maximum (not user-configurable)."""
    if len(text) <= TALKER_ALIAS_MAX_LEN:
        return text
    return text[:TALKER_ALIAS_MAX_LEN]


def decode_ta(buf: bytes) -> str:
    """Decode 28-byte TA buffer (formats 0–3). Mirrors MMDVMHost CDMRTA::decodeTA."""
    if len(buf) < 1:
        return ""
    ta_format = (buf[0] >> 6) & 0x03
    ta_size = (buf[0] >> 1) & 0x1F
    if ta_format == 1 or ta_format == 2:
        raw = buf[1 : 1 + ta_size]
        return raw.decode("latin-1", errors="replace")
    if ta_format != 0:
        return ""
    out = bytearray()
    t1 = 0
    t2 = 0
    c = 0
    for i in range(min(32, len(buf))):
        if t2 >= ta_size:
            break
        for j in range(7, -1, -1):
            c = ((c << 1) | ((buf[i] >> j) & 1)) & 0xFF
            t1 += 1
            if t1 == 7:
                if i > 0:
                    out.append(c & 0x7F)
                    t2 += 1
                    if t2 >= ta_size:
                        break
                t1 = 0
                c = 0
    return out[:ta_size].decode("ascii", errors="replace")


decode_7bit = decode_ta  # backwards-compatible alias


VALID_TA_TEXT_FORMATS = frozenset({"utf8", "iso8", "7bit"})


def parse_ta_text_formats(raw: Any) -> list[str]:
    """Parse ``TALKER_ALIAS_TEXT_FORMAT`` (comma list or YAML list) → ordered encodings."""
    if raw is None:
        return ["utf8"]
    if isinstance(raw, list):
        parts = [str(x).strip().lower() for x in raw]
    else:
        parts = [f.strip().lower() for f in str(raw).split(",")]
    valid = [f for f in parts if f in VALID_TA_TEXT_FORMATS]
    return valid or ["utf8"]


def encode_utf8(text: str) -> bytes:
    """Encode text into 28-byte TA buffer (format 2 / UTF-8)."""
    text = truncate_talker_alias(text)
    raw = text.encode("utf-8")
    if len(raw) > 27:
        raw = truncate_talker_alias(text.encode("utf-8")[:27].decode("utf-8", errors="ignore")).encode("utf-8")
    size = len(raw)
    header = (TA_FORMAT_UTF8 << 6) | (size << 1) | 0
    buf = bytearray(DMRA_BUF_LEN)
    buf[0] = header
    buf[1 : 1 + size] = raw
    return bytes(buf)


def encode_iso8(text: str) -> bytes:
    """Encode text into 28-byte TA buffer (format 1 / ISO-8859-1, best for Hytera)."""
    text = truncate_talker_alias(text)
    raw = text.encode("latin-1", errors="replace")[:27]
    size = len(raw)
    header = (TA_FORMAT_ISO8 << 6) | (size << 1) | 0
    buf = bytearray(DMRA_BUF_LEN)
    buf[0] = header
    buf[1 : 1 + size] = raw
    return bytes(buf)


def encode_7bit(text: str) -> bytes:
    """Encode text into 28-byte TA buffer (format 0 / 7-bit packed, inverse of ``decode_ta``)."""
    text = truncate_talker_alias(text)
    chars = text.encode("ascii", errors="replace")[:31]
    size = len(chars)
    bits = [(TA_FORMAT_7BIT >> 1) & 1, TA_FORMAT_7BIT & 1]
    for b in range(4, -1, -1):
        bits.append((size >> b) & 1)
    for ch in chars:
        for b in range(6, -1, -1):
            bits.append((ch >> b) & 1)
    total = DMRA_BUF_LEN * 8
    bits = (bits + [0] * total)[:total]
    buf = bytearray(DMRA_BUF_LEN)
    for i, bit in enumerate(bits):
        if bit:
            buf[i // 8] |= 1 << (7 - (i % 8))
    return bytes(buf)


_TA_ENCODERS = {"utf8": encode_utf8, "iso8": encode_iso8, "7bit": encode_7bit}


def encode_ta_buffer(text: str, text_format: str = "utf8") -> bytes:
    """Encode text into a 28-byte TA buffer in the requested format."""
    return _TA_ENCODERS.get(text_format, encode_utf8)(text)


def blocks_from_buffer(buf: bytes) -> list[bytes]:
    """Split 28-byte encoded buffer into four 7-byte DMRA payloads."""
    buf = buf.ljust(DMRA_BUF_LEN, b"\x00")[:DMRA_BUF_LEN]
    return [buf[i * DMRA_PAYLOAD_LEN : (i + 1) * DMRA_PAYLOAD_LEN] for i in range(DMRA_BLOCK_COUNT)]


def required_ta_block_count(buf: bytes) -> int:
    """Blocks 1–4 actually needed (skip trailing zero payloads). ETSI allows 1–4."""
    blocks = blocks_from_buffer(buf)
    last = 0
    for i in range(DMRA_BLOCK_COUNT):
        if blocks[i] != b"\x00" * DMRA_PAYLOAD_LEN:
            last = i
    return max(1, last + 1)


def buffer_from_blocks(blocks: dict[int, bytes]) -> bytes:
    """Merge up to four block payloads into a 28-byte buffer (ADN inject layout)."""
    buf = bytearray(DMRA_BUF_LEN)
    for block_id, payload in blocks.items():
        if 0 <= block_id < DMRA_BLOCK_COUNT and payload:
            start = block_id * DMRA_PAYLOAD_LEN
            buf[start : start + DMRA_PAYLOAD_LEN] = payload[:DMRA_PAYLOAD_LEN]
    return bytes(buf)


def buffer_from_wire_blocks(blocks: dict[int, bytes]) -> bytes:
    """Merge MMDVMHost DMRA wire payloads into the 28-byte TA buffer.

    ``writeTalkerAlias`` copies ``data + 2`` (7 bytes) from the 9-byte embedded LC;
    ``CDMRTA::add(blockId, data + 2, 7)`` stores those at ``m_buf[blockId * 7]``.
    The UDP payload is therefore the same layout as ``buffer_from_blocks``.
    """
    return buffer_from_blocks(blocks)


def is_ta_header_byte(byte0: int) -> bool:
    """True if byte looks like ETSI TA header (UTF-8/ISO formats; not 7-bit)."""
    if (byte0 & 1) != 0:
        return False
    fmt = (byte0 >> 6) & 0x03
    size = (byte0 >> 1) & 0x1F
    return fmt in (1, 2) and 1 <= size <= TALKER_ALIAS_MAX_LEN


def talker_alias_decode_complete(buf: bytes) -> bool:
    """True when TA buffer decodes with expected size (MMDVM DMRTA parity)."""
    if len(buf) < 1 or not is_ta_header_byte(buf[0]):
        return False
    ta_size = (buf[0] >> 1) & 0x1F
    text = decode_ta(buf).rstrip("\x00").strip()
    return len(text) >= ta_size


def decode_ta_from_blocks(blocks: dict[int, bytes]) -> str:
    """Decode TA from buffered DMRA blocks (MMDVM wire layout, then ADN layout)."""
    buf = buffer_from_wire_blocks(blocks)
    if talker_alias_decode_complete(buf):
        return decode_ta(buf).rstrip("\x00").strip()
    if blocks.get(0) and is_ta_header_byte(blocks[0][0]):
        buf = buffer_from_blocks(blocks)
        if talker_alias_decode_complete(buf):
            return decode_ta(buf).rstrip("\x00").strip()
    return ""


def build_dmra_packets(rf_src: bytes, text: str, text_format: str = "utf8") -> list[bytes]:
    """Build HBP DMRA packets (1–4) for server injection."""
    rf = rf_src[:3] if len(rf_src) >= 3 else rf_src.ljust(3, b"\x00")[:3]
    encoded = encode_ta_buffer(text, text_format)
    blocks = blocks_from_buffer(encoded)
    count = required_ta_block_count(encoded)
    packets: list[bytes] = []
    for block_id in range(count):
        packets.append(DMRA_OPCODE + rf + bytes([block_id]) + blocks[block_id])
    return packets


def build_dmra_packet(rf_src: bytes, block_id: int, payload: bytes) -> bytes:
    """Build one 15-byte DMRA packet (pass-through or re-encode block)."""
    rf = rf_src[:3] if len(rf_src) >= 3 else rf_src.ljust(3, b"\x00")[:3]
    block = max(0, min(3, int(block_id)))
    pl = payload[:DMRA_PAYLOAD_LEN].ljust(DMRA_PAYLOAD_LEN, b"\x00")
    return DMRA_OPCODE + rf + bytes([block]) + pl


def parse_dmra_packet(data: bytes) -> tuple[bytes, int, bytes] | None:
    """Parse MMDVMHost HBP DMRA (15 bytes), per ``DMRNetwork::writeTalkerAlias``.

    Layout: ``DMRA`` | src_id[24-bit BE @4–6] | type @7 (0–3) | memcpy(@8, data+2, 7).

    ``data`` is the 9-byte embedded LC (``CDMREmbeddedData::getRawData``); wire bytes equal
    ``CDMRTA::add(blockId, data+2, 7)`` → ``m_buf[blockId*7 : blockId*7+7]``.
    """
    if len(data) < DMRA_PACKET_LEN or data[:4] != DMRA_OPCODE:
        return None
    block_id = data[7]
    if block_id > 3:
        return None
    return data[4:7], block_id, data[8:15]


def store_ta_block(blocks: dict[int, bytes], block_id: int, payload: bytes) -> bool:
    """Store one wire fragment (7 bytes at ``m_buf[block_id*7]``)."""
    if block_id < 0 or block_id > 3:
        return False
    blocks[block_id] = payload[:DMRA_PAYLOAD_LEN]
    return True


def store_ta_from_embed_lc(blocks: dict[int, bytes], block_id: int, lc9: bytes) -> bool:
    """Store from 9-byte embedded LC (MMDVM ``data`` in ``DMRSlot`` TA FLCO 4–7 path)."""
    if block_id < 0 or block_id > 3 or len(lc9) < 9:
        return False
    return store_ta_block(blocks, block_id, lc9[2:9])


def talker_alias_block_id_from_lc(lc: bytes) -> int | None:
    """FLCO 4–7 (MMDVM TALKER_ALIAS_HEADER..BLOCK3) → block index 0–3, else None."""
    if len(lc) < 9:
        return None
    flco = lc[0]
    if flco < FLCO_TALKER_ALIAS_HEADER or flco > FLCO_TALKER_ALIAS_BLOCK3:
        return None
    return flco - FLCO_TALKER_ALIAS_HEADER


def try_buffer_ta_from_voice_fragments(
    acc: dict[int, "bitarray"],
    vseq: int,
    dmrpkt: bytes,
    blocks: dict[int, bytes],
) -> bool:
    """Reassemble embedded LC across voice bursts B–E (vseq 1–4) and store if it is a TA.

    ``adn_server.domain.dmr.bptc.decode_emblc`` is correct on properly FEC-encoded input (the encode
    helper had an upstream bit-25 bug, fixed in our vendored copy), so a TA sent by a real radio
    decodes losslessly here.
    Returns True when a TA block was decoded and stored.
    """
    if vseq not in (1, 2, 3, 4) or len(dmrpkt) < 33:
        return False
    try:
        embed = decode.voice_embed(dmrpkt)
    except Exception:
        return False
    if vseq == 1:
        acc.clear()
    acc[vseq] = embed
    if len(acc) < 4:  # keys are vseq 1-4 only, and vseq 1 clears it
        return False
    try:
        lc = bptc.decode_emblc(acc[1] + acc[2] + acc[3] + acc[4])
    except Exception:
        acc.clear()
        return False
    acc.clear()
    block_id = talker_alias_block_id_from_lc(lc)
    if block_id is None:
        return False
    return store_ta_from_embed_lc(blocks, block_id, lc)


def talker_alias_lc_bytes(block_id: int, payload7: bytes) -> bytes:
    """Build 9-byte embedded LC for one TA fragment (FLCO 4–7 + FID + 7 payload)."""
    block = max(0, min(3, int(block_id)))
    payload = payload7[:DMRA_PAYLOAD_LEN].ljust(DMRA_PAYLOAD_LEN, b"\x00")
    return bytes([FLCO_TALKER_ALIAS_HEADER + block, 0x00]) + payload
