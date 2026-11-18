"""ODTP (Rewind) codec."""

import struct

from adn_server.domain import odtp
from adn_server.domain.dmr import rs129


def test_pack_unpack_round_trip():
    data = odtp.pack(odtp.DMR_AUDIO_FRAME, 7, b"\x01" * 27, odtp.FLAG_REAL_TIME_1)
    assert odtp.unpack(data) == (odtp.DMR_AUDIO_FRAME, odtp.FLAG_REAL_TIME_1, 7, b"\x01" * 27)


def test_unpack_rejects_foreign_or_short():
    data = odtp.pack(odtp.KEEP_ALIVE, 1, b"abc")
    assert odtp.unpack(b"REWIND02" + data[8:]) is None
    assert odtp.unpack(data[:-1]) is None
    assert odtp.unpack(data[:10]) is None


def test_voice_header_carries_the_lc_and_its_rs_parity():
    head = odtp.voice_header(666, 2130035, False)
    assert head[9:] == bytes(a ^ b for a, b in zip(rs129.encode(bytearray(head[:9])), rs129.START_MASK))
    assert len(head) == 12 and odtp.parse_voice_header(head) == (666, 2130035, False)
    assert odtp.parse_voice_header(odtp.voice_header(2140001, 2130035, True)) == (2140001, 2130035, True)


def test_keep_alive_and_subscription():
    payload = struct.pack("<IB", 2130035, odtp.SERVICE_OPEN_TERMINAL) + b"Z3DMR 1.9\x00junk"
    assert odtp.parse_keep_alive(payload) == (2130035, odtp.SERVICE_OPEN_TERMINAL, "Z3DMR 1.9")
    assert odtp.parse_keep_alive(b"\x00") is None
    assert odtp.parse_subscription(struct.pack("<II", 7, 666)) == (7, 666)
    assert odtp.parse_subscription(b"") is None
