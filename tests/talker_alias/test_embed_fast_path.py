# ADN DMR Peer Server - tests talker alias embedded LC fast path
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

"""Embedded talker alias runs on 4 of every 6 voice frames, so it must stay cheap."""

from __future__ import annotations

import os
import time

from tests.harness.obp_ingress import build_config
from tests.talker_alias.test_mmdvm_wire import _voice_pkt_with_embed

import adn_server.infrastructure.twisted_adapters.udp_hbp as udp_hbp
from adn_server.domain.dmr import decode
from adn_server.domain.dmr.bptc import encode_emblc
from adn_server.domain.talker_alias import encode_utf8, talker_alias_lc_bytes
from adn_server.infrastructure.twisted_adapters.udp_hbp import HBPProtocol

STREAM = b"\x12\x34\x56\x78"
SRC = b"\x21\x70\x03"
PEER = b"\x00\x00\x08\x24"


def test_voice_embed_matches_the_full_decode() -> None:
    for _ in range(5000):
        pkt = os.urandom(33)
        assert decode.voice_embed(pkt) == decode.voice(pkt)["EMBED"]


def _protocol() -> HBPProtocol:
    config = build_config({"kind": "v5"})
    config["GLOBAL"]["TALKER_ALIAS"] = True
    return HBPProtocol("OBP-FR", config, router=None, dmrd_received=lambda *a, **k: None)


def _send_superframe(protocol: HBPProtocol) -> None:
    frags = encode_emblc(talker_alias_lc_bytes(0, encode_utf8("CE5RPY")[0:7]))
    for vseq in (1, 2, 3, 4):
        protocol.store_ta_from_voice_burst(PEER, SRC, STREAM, vseq, _voice_pkt_with_embed(frags[vseq]))


def test_a_complete_alias_is_not_decoded_again(monkeypatch) -> None:
    protocol = _protocol()
    _send_superframe(protocol)
    assert STREAM in protocol._ta_complete

    calls = []
    original = udp_hbp.try_buffer_ta_from_voice_fragments
    monkeypatch.setattr(
        udp_hbp, "try_buffer_ta_from_voice_fragments", lambda *a: (calls.append(a), original(*a))[1]
    )
    _send_superframe(protocol)
    assert calls == []


def test_a_complete_alias_does_not_expire_during_a_long_call() -> None:
    """Skipping the decode must still refresh ``last``, or the trimmer drops the alias of
    any call longer than its 180 s window while the call is still going."""
    protocol = _protocol()
    _send_superframe(protocol)
    protocol._dmra_by_stream[STREAM]["last"] = time.time() - 200  # already past the window

    _send_superframe(protocol)
    protocol.trim_dmra_streams(max_age=180.0)
    assert STREAM in protocol._dmra_by_stream
