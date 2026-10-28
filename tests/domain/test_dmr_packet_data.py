"""DMR packet data in the domain: header, blocks, trellis, CRCs, IP/TMS/SMS and Hytera HDAP."""

import os
from pathlib import Path

from bitarray import bitarray

from adn_server.domain.dmr import data_header as hd
from adn_server.domain.dmr import hdap, ip_sms
from adn_server.domain.dmr.bptc import decode_19696
from adn_server.domain.dmr.crc import crc16_csbk, crc32_data
from adn_server.domain.dmr.data_blocks import (
    DT_CSBK,
    DT_DATA_HEADER,
    DT_RATE12,
    DT_RATE34,
    build_blocks,
    data_frames,
    decode_block,
    decode_header_burst,
    info_bits,
)
from adn_server.domain.dmr.trellis import decode_trellis34, encode_trellis34

CAPTURES = Path(__file__).parent.parent / "fixtures" / "dmr_data"


def _bursts(name):
    rows = []
    for line in (CAPTURES / name).read_text().split("\n"):
        if line.strip():
            dtype, payload = line.split()
            rows.append((int(dtype), bytes.fromhex(payload)))
    return rows


def _decode_packet(bursts):
    """(header, user data, blocks) of the first packet in a list of on-air bursts."""
    start = next(i for i, (dtype, _) in enumerate(bursts) if dtype == DT_DATA_HEADER)
    header = hd.DataHeader.from_bytes(decode_header_burst(bursts[start][1]))
    confirmed = header.confirmed or (header.dpf == hd.DPF_SHORT_DATA_DEFINED and header.sarq)
    data = bursts[start + 1:start + 1 + header.blocks]
    blocks = [decode_block(p, dtype, confirmed, i == len(data) - 1) for i, (dtype, p) in enumerate(data)]
    return header, b"".join(b.data for b in blocks), blocks


def test_on_air_hytera_packets_decode_with_their_crcs():
    for name in ("hytera_lp_report.txt", "hytera_rrs_registration.txt"):
        header, user, blocks = _decode_packet(_bursts(name))
        assert header is not None and blocks and all(b is not None and b.crc_ok for b in blocks)
        assert crc32_data(user) == blocks[-1].crc32


def test_on_air_csbk_preamble_and_response_header():
    dtype, csbk = _bursts("hytera_lp_report.txt")[0]
    raw = decode_19696(info_bits(csbk))
    assert dtype == DT_CSBK and crc16_csbk(raw[:10]) == int.from_bytes(raw[10:12], "big")
    response = hd.DataHeader.from_bytes(decode_header_burst(_bursts("hytera_sack.txt")[0][1]))
    assert response is not None and response.dpf == hd.DPF_RESPONSE


def test_blocks_round_trip_in_every_rate():
    user = os.urandom(57)
    for rate34 in (False, True):
        for confirmed in (False, True):
            bursts, pad = build_blocks(user, rate34, confirmed)
            blocks = [decode_block(p, dtype, confirmed, i == len(bursts) - 1) for i, (dtype, p) in enumerate(bursts)]
            data = b"".join(b.data for b in blocks)
            assert data[:len(user)] == user and len(data) == len(user) + pad
            assert all(b.crc_ok for b in blocks) and crc32_data(data) == blocks[-1].crc32
            assert {dtype for dtype, _ in bursts} == {DT_RATE34 if rate34 else DT_RATE12}


def test_trellis_round_trip_corrects_a_bit_error():
    for i in range(196):
        data = os.urandom(18)
        bits = encode_trellis34(data)
        assert decode_trellis34(bits) == data
        bits[i] ^= 1
        assert decode_trellis34(bits) == data
    assert decode_trellis34(bitarray(195)) is None


def test_data_header_round_trips():
    for header in (
        hd.DataHeader(dpf=hd.DPF_UNCONFIRMED, dst=213, src=2130035, group=True, pad_octets=17, blocks_to_follow=3),
        hd.DataHeader(dpf=hd.DPF_CONFIRMED, dst=2140001, src=2130035, response_requested=True, blocks_to_follow=2,
                      resync=True, send_seq=5, fsn=hd.FSN_SINGLE_CONFIRMED),
        hd.response_ack(2140001, 2130035, hd.SAP_IP, 5),
        hd.DataHeader(dpf=hd.DPF_SHORT_DATA_DEFINED, dst=1, src=2, sap=hd.SAP_SHORT_DATA, appended_blocks=21,
                      sarq=True, bit_padding=40),
    ):
        assert hd.DataHeader.from_bytes(header.to_bytes()) == header
    broken = bytearray(hd.response_ack(1, 2, hd.SAP_IP).to_bytes())
    broken[3] ^= 1
    assert hd.DataHeader.from_bytes(bytes(broken)) is None


def test_data_frames_announce_the_blocks_and_mark_unit_data():
    blocks, pad = build_blocks(b"hola", rate34=False, confirmed=False)
    header = hd.DataHeader(dpf=hd.DPF_UNCONFIRMED, dst=2140001, src=2130035, pad_octets=pad, blocks_to_follow=len(blocks))
    frames = data_frames(header.to_bytes(), blocks, 2130035, 2140001, 213003598, 2, preambles=2)
    assert [f[15] & 0x0F for f in frames] == [DT_CSBK, DT_CSBK, DT_DATA_HEADER, DT_RATE12]
    assert all(f[15] & 0xC0 == 0xC0 and len(f) == 55 for f in frames)  # TS2, unit data
    assert decode_19696(info_bits(frames[0][20:53]))[3] == 3  # after the first preamble: preamble, header, block


def test_ip_text_formats_round_trip():
    msg = ip_sms.parse_ip_udp(ip_sms.ip_udp(2130035, 2140001, ip_sms.PORT_TMS, ip_sms.tms_simple_text("hola ñandú", 3), 7))
    assert (msg.radio_id, msg.port) == (2130035, ip_sms.PORT_TMS)
    tms = ip_sms.tms_parse(msg.payload)
    assert (tms.text, tms.sequence) == ("hola ñandú", 3)
    assert ip_sms.tms_parse(ip_sms.tms_ack(3)).is_ack
    assert ip_sms.standard_sms_parse(ip_sms.standard_sms("73")) == "73"
    found, _ = hdap.find(b"\x01\x00" + hdap.text(9, 2140001, 2130035, "QRV"))
    tm = hdap.text_message(found)
    assert (tm.request_id, tm.dst_id, tm.src_id, tm.text, tm.ack_required) == (9, 2140001, 2130035, "QRV", False)
