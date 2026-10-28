"""ODTP gateway: embedded data, text messages and locations, end to end."""

import os
import struct

from bitarray import bitarray

from adn_server.application.odtp.messages import Messages
from adn_server.application.odtp.ports import OdtpSettings
from adn_server.application.odtp.session import _Uplink
from adn_server.domain import odtp
from adn_server.domain.dmr import data_header as hd
from adn_server.domain.dmr import hdap, ip_sms
from adn_server.domain.dmr.crc import crc32_data
from adn_server.domain.dmr.data_blocks import (
    DT_CSBK,
    DT_DATA_HEADER,
    build_blocks,
    data_frames,
    decode_block,
    decode_header_burst,
)
from adn_server.domain.dmr.embedded_lc import collect_embedded_lc
from adn_server.domain.dmr.frames import DmrdStream, superframe_embedding
from adn_server.domain.dmrd import parse_dmrd_header
from tests.support.odtp_stack import PEER, TERMINAL_ID, FakeClock, Stack

RADIO = 2140001
TA_HEADER = bytes([0x04, 0x00, 0x42]) + b"C31AG "  # Talker Alias header: 7-bit, 6 characters
NMEA = b"$GPRMC,120000,A,4230.378,N,00131.310,E,0.0,0.0,251026,,*00"


def _up(**kw):
    stack = Stack(**kw)
    term = stack.terminal()
    term.login()
    assert term.got(odtp.KEEP_ALIVE)
    return stack, term


def _packets(frames):
    """(header, user data) of each packet in a list of DMRD frames (CSBK preambles skipped)."""
    out, cur = [], None
    for f in frames:
        h = parse_dmrd_header(f)
        if h.dtype_vseq == DT_CSBK:
            continue
        if h.dtype_vseq == DT_DATA_HEADER:
            cur = [hd.DataHeader.from_bytes(decode_header_burst(f[20:53])), []]
            out.append(cur)
        else:
            cur[1].append((h.dtype_vseq, f[20:53]))
    result = []
    for header, data in out:
        confirmed = header.confirmed or (header.dpf == hd.DPF_SHORT_DATA_DEFINED and header.sarq)
        blocks = [decode_block(b, dt, confirmed, i == len(data) - 1) for i, (dt, b) in enumerate(data)]
        user = b"".join(b.data for b in blocks)
        assert not blocks or (all(b.crc_ok for b in blocks) and crc32_data(user) == blocks[-1].crc32)
        result.append((header, user))
    return result


def _radio(fmt, text, dst=TERMINAL_ID):
    """A private text from RADIO asking for every ACK it can, as the MASTER delivers it to the terminal's peer."""
    m = Messages(RADIO, RADIO * 100 + 1, OdtpSettings(sms_preambles=0), FakeClock(), lambda f: None, lambda *a: None)
    if fmt == "tms":
        tms = bytearray(ip_sms.tms_simple_text(text, 3))
        tms[2] |= 0x40  # application ACK wanted
        frames = m._ip_frames(dst, ip_sms.PORT_TMS, bytes(tms), 2)
    elif fmt == "hytera":
        tmp = hdap.text(9, dst, RADIO, text)
        frames = m._short_frames(dst, hdap.build(hdap.SERVICE_TMP, hdap.TMP_PRIVATE_MESSAGE, hdap.find(tmp)[0].payload), 2)
    else:
        frames = m._ip_frames(dst, ip_sms.PORT_DMR_STANDARD_SMS, ip_sms.standard_sms(text), 2)
    return [f[:11] + PEER + f[15:] for f in frames]


def _lcs(frames):
    acc, out = {}, []
    for f in frames:
        h = parse_dmrd_header(f)
        if h.frame_type in (0, 1):
            lc = collect_embedded_lc(acc, h.dtype_vseq if h.frame_type == 0 else 0, f[20:53])
            if lc is not None:
                out.append(lc)
    return out


def test_text_status_location_codecs():
    msg = odtp.text_message(TERMINAL_ID, RADIO, "hola ñandú")
    assert odtp.parse_text_message(msg) == (TERMINAL_ID, RADIO, False, "hola ñandú")
    assert odtp.parse_text_message(odtp.text_message(1, 213, "x", group=True))[2] is True
    assert odtp.parse_text_status(odtp.text_status(RADIO, TERMINAL_ID, 8)) == (RADIO, TERMINAL_ID, 8)
    report = struct.pack("<IIH", 0, odtp.LOCATION_FORMAT_NMEA, len(NMEA) + 1) + NMEA + b"\x00"
    assert odtp.parse_location_report(report) == NMEA


def test_uplink_embedded_lc_takes_every_other_superframe():
    call = _Uplink(TERMINAL_ID, 666, PEER, False, 0.0)
    block1 = bytes([0x05]) + b"C31AG Ad"
    call.embed(TA_HEADER)
    call.embed(block1)
    frames = [call.voice(os.urandom(27), 0.0) for _ in range(30)]
    voice_lc = odtp.full_lc(666, TERMINAL_ID, False)
    assert _lcs(frames) == [TA_HEADER, voice_lc, block1, voice_lc, voice_lc]


def test_embedded_data_both_ways():
    stack, term = _up()
    # Downlink: a radio's Talker Alias reaches the app once.
    stream = DmrdStream(RADIO.to_bytes(3, "big"), (666).to_bytes(3, "big"), PEER, 1, os.urandom(4))
    bits = bitarray(endian="big")
    bits.frombytes(os.urandom(27))
    ta = superframe_embedding(TA_HEADER)
    frames = [stream.header()] + [stream.burst(i, bits[:108], bits[108:], ta) for i in range(18)] + [stream.terminator()]
    stack.net.to_peer(PEER, frames)
    assert term.got(odtp.DMR_EMBEDDED_DATA) == [TA_HEADER]
    # Uplink: the app's Talker Alias rides in its call.
    term.send(odtp.VOICE_HEADER, odtp.voice_header(666, TERMINAL_ID, False))
    term.send(odtp.DMR_EMBEDDED_DATA, TA_HEADER + b"\x00")
    for _ in range(12):
        term.send(odtp.DMR_AUDIO_FRAME, os.urandom(27))
    assert TA_HEADER in _lcs(stack.net.dmrd)


def test_text_from_the_app_to_a_radio_its_ack_and_one_retry():
    stack, term = _up()
    term.send(odtp.MESSAGE_TEXT, odtp.text_message(TERMINAL_ID, RADIO, "prueba ODTP"))
    stack.advance(1.0)
    [(header, user)] = _packets(stack.net.dmrd)
    assert (header.dpf, header.src, header.dst, header.response_requested) == (hd.DPF_CONFIRMED, TERMINAL_ID, RADIO, True)
    msg = ip_sms.parse_ip_udp(user)
    assert msg.radio_id == TERMINAL_ID and ip_sms.standard_sms_parse(msg.payload) == "prueba ODTP"
    stack.advance(6.0)
    assert len(_packets(stack.net.dmrd)) == 2, "no ACK: sent once more"
    # The radio's link ACK becomes MESSAGE_STATUS (ACK = 0x08).
    ack = hd.response_ack(TERMINAL_ID, RADIO, hd.SAP_IP, header.send_seq).to_bytes()
    stack.net.to_peer(PEER, [f[:11] + PEER + f[15:] for f in data_frames(ack, [], RADIO, TERMINAL_ID, RADIO, 2)])
    assert [odtp.parse_text_status(p) for p in term.got(odtp.MESSAGE_STATUS)] == [(RADIO, TERMINAL_ID, 0x08)]
    stack.advance(12.0)
    assert len(_packets(stack.net.dmrd)) == 2


def _text_to_the_app(fmt):
    stack, term = _up()
    frames = _radio(fmt, "hola desde la radio")
    stack.net.to_peer(PEER, frames)
    assert [odtp.parse_text_message(p) for p in term.got(odtp.MESSAGE_TEXT)] == [
        (RADIO, TERMINAL_ID, False, "hola desde la radio")]
    stack.advance(2.0)
    headers = [h for h, _ in _packets(stack.net.dmrd)]
    acks = [h for h in headers if h.dpf == hd.DPF_RESPONSE]
    assert acks and all((h.src, h.dst, h.response_class, h.response_type) == (TERMINAL_ID, RADIO, 0, 1) for h in acks)
    if fmt != "standard":
        assert any(h.dpf != hd.DPF_RESPONSE and h.dst == RADIO for h in headers), "application ACK"
    stack.net.to_peer(PEER, frames)  # the radio repeats it: the app does not get it twice
    assert len(term.got(odtp.MESSAGE_TEXT)) == 1


def test_dmr_standard_text_to_the_app():
    _text_to_the_app("standard")


def test_mototrbo_tms_text_to_the_app():
    _text_to_the_app("tms")


def test_hytera_tmp_text_to_the_app():
    _text_to_the_app("hytera")


def test_reply_goes_in_the_format_the_radio_wrote_in():
    stack, term = _up()
    stack.net.to_peer(PEER, _radio("tms", "hola"))
    stack.advance(2.0)
    stack.net.dmrd.clear()
    term.send(odtp.MESSAGE_TEXT, odtp.text_message(TERMINAL_ID, RADIO, "respuesta"))
    stack.advance(1.0)
    [(_header, user)] = _packets(stack.net.dmrd)
    msg = ip_sms.parse_ip_udp(user)
    assert msg.port == ip_sms.PORT_TMS and ip_sms.tms_parse(msg.payload).text == "respuesta"


def test_group_text_both_ways():
    stack, term = _up()
    term.send(odtp.MESSAGE_TEXT, odtp.text_message(TERMINAL_ID, 213, "a todos", group=True))
    stack.advance(1.0)
    [(header, user)] = _packets(stack.net.dmrd)
    assert header.group and header.dst == 213 and header.dpf == hd.DPF_UNCONFIRMED
    assert "unit" not in {parse_dmrd_header(f).call_type for f in stack.net.dmrd}
    assert ip_sms.standard_sms_parse(ip_sms.parse_ip_udp(user).payload) == "a todos"
    stack.net.dmrd.clear()
    payload = ip_sms.ip_udp(RADIO, 213, ip_sms.PORT_DMR_STANDARD_SMS, ip_sms.standard_sms("hola grupo"), 7, dst_net=225)
    blocks, pad = build_blocks(payload, rate34=False, confirmed=False)
    h = hd.DataHeader(dpf=hd.DPF_UNCONFIRMED, dst=213, src=RADIO, sap=hd.SAP_IP, group=True,
                      pad_octets=pad, blocks_to_follow=len(blocks)).to_bytes()
    stack.net.to_peer(PEER, data_frames(h, blocks, RADIO, 213, int.from_bytes(PEER, "big"), 2, group=True))
    assert [odtp.parse_text_message(p) for p in term.got(odtp.MESSAGE_TEXT)] == [(RADIO, 213, True, "hola grupo")]
    stack.advance(1.0)
    assert stack.net.dmrd == [], "no ACK to a group text"


def test_location_goes_to_the_d_aprs_id_as_nmea_data_at_most_every_10_s():
    stack, term = _up(settings=OdtpSettings(location_dst=900999))
    report = struct.pack("<IIH", 0, 0, len(NMEA)) + NMEA
    term.send(odtp.LOCATION_REPORT, report)
    stack.advance(1.0)
    [(header, user)] = _packets(stack.net.dmrd)
    assert (header.dpf, header.src, header.dst, header.group) == (hd.DPF_UNCONFIRMED, TERMINAL_ID, 900999, False)
    assert user.rstrip(b"\x00") == NMEA + b"\r\n"
    term.send(odtp.LOCATION_REPORT, report)
    stack.advance(1.0)
    assert len(_packets(stack.net.dmrd)) == 1
    stack.advance(9.0)
    term.send(odtp.LOCATION_REPORT, report)
    stack.advance(1.0)
    assert len(_packets(stack.net.dmrd)) == 2
