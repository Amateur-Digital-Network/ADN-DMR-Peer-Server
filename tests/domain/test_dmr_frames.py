"""DMRD voice frames built incrementally (DmrdStream), shared by pkt_gen and the ODTP gateway."""

from bitarray import bitarray

from adn_server.domain.dmr import decode
from adn_server.domain.dmr.frames import (
    BURSTBITS,
    DmrdStream,
    superframe_embedding,
    voice_bits,
)

SRC = (2130035).to_bytes(3, "big")
DST = (9990).to_bytes(3, "big")
PEER = (213003598).to_bytes(4, "big")
SID = b"\x01\x02\x03\x04"


def _halves(seed):
    b = bitarray(endian="big")
    b.frombytes(bytes((seed + i) & 0xFF for i in range(27)))
    return b[:108], b[108:216]


def test_header_and_terminator_carry_the_voice_lc():
    stream = DmrdStream(SRC, DST, PEER, 1, SID)  # TS2
    head, term = stream.header(), stream.terminator()
    assert decode.voice_head_term(head[20:53])["LC"] == stream.lc
    assert decode.voice_head_term(term[20:53])["LC"] == stream.lc
    assert head[15] == 0x80 | 0b00100001 and term[15] == 0x80 | 0b00100010


def test_bursts_keep_the_voice_and_count_the_sequence():
    stream = DmrdStream(SRC, DST, PEER, 0, SID, private=True)  # TS1
    b0, b1 = _halves(5)
    pkts = [stream.burst(n, b0, b1) for n in range(7)]
    assert [p[4] for p in pkts] == list(range(7))
    assert all(voice_bits(p[20:53]) == b0 + b1 for p in pkts)
    assert pkts[6][15] == 0x40 | BURSTBITS[0]
    assert pkts[0][16:20] == SID and pkts[0][5:8] == SRC and pkts[0][8:11] == DST and pkts[0][11:15] == PEER


def test_an_other_lc_can_ride_in_a_superframe():
    stream = DmrdStream(SRC, DST, PEER, 2, SID)
    alias = bytes([4, 0, 0x48]) + b"EA3XYZ"
    b0, b1 = _halves(9)
    burst_b = stream.burst(1, b0, b1, superframe_embedding(alias))
    assert burst_b != stream.burst(1, b0, b1)


def test_embedded_lc_round_trip_through_bursts_b_to_e():
    from adn_server.domain.dmr.embedded_lc import collect_embedded_lc

    stream = DmrdStream(SRC, DST, PEER, 1, SID)
    alias = bytes([4, 0, 0x48]) + b"EA3XYZ"
    embedding = superframe_embedding(alias)
    b0, b1 = _halves(3)
    acc = {}
    got = [collect_embedded_lc(acc, n, stream.burst(n, b0, b1, embedding)[20:53]) for n in range(6)]
    assert got[:4] == [None, None, None, None] and got[4] == alias and got[5] is None and acc == {}
