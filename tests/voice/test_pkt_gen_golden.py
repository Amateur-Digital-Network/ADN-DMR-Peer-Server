"""pkt_gen output is byte for byte what it was before it moved onto DmrdStream."""

import hashlib
import random

from bitarray import bitarray

from adn_server.infrastructure.voice.pkt_gen import pkt_gen

# sha256 of the frames below, taken from pkt_gen before the refactor.
GOLDEN = "56c9162ce6cb957b0b6d5493d1012ba440e95ec424edf50b4b649e369b0d0e16"


def _bits(n, seed):
    r = random.Random(seed)
    b = bitarray(endian="big")
    b.extend(r.getrandbits(1) for _ in range(n))
    return b


def test_pkt_gen_frames_are_unchanged():
    phrase = [[[_bits(108, w * 100 + i), _bits(108, w * 100 + i + 50)] for i in range(k)] for w, k in enumerate((6, 4, 9))]
    frames = []
    for private in (False, True):
        random.seed(11)
        frames += list(pkt_gen(b"\x21\x00\x35", b"\x00\x03\xd5", b"\x0c\xb7\x0c\x5e", 2, phrase, private=private))
        random.seed(12)
        frames += list(pkt_gen(b"\x21\x00\x35", b"\x00\x00\xd6", b"\x00\x00\x00\x09", 1, phrase))
    assert len(frames) == 92
    assert hashlib.sha256(b"".join(frames)).hexdigest() == GOLDEN
