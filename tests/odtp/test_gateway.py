"""ODTP gateway end to end: terminal -> gateway -> real HBP MASTER, and back."""

import logging
import os
import struct

from bitarray import bitarray

from adn_server.application.odtp.ports import OdtpSettings
from adn_server.domain import odtp
from adn_server.domain.dmr.frames import DmrdStream, voice_bits
from adn_server.domain.dmrd import parse_dmrd_header
from adn_server.infrastructure.odtp.callsigns import CallsignFile
from tests.support.odtp_stack import PEER, TERMINAL_ID, Stack

RADIO = 2140001


def _call(tg: int, n: int, ambe: bytes, *, src: int = RADIO, private: bool = False) -> list[bytes]:
    """A call as the MASTER delivers it to the terminal's peer: header, ``n`` voice bursts, terminator."""
    bits = bitarray(endian="big")
    bits.frombytes(ambe)
    stream = DmrdStream(src.to_bytes(3, "big"), tg.to_bytes(3, "big"), PEER, 1, os.urandom(4), private=private)
    return [stream.header(), *(stream.burst(i, bits[:108], bits[108:]) for i in range(n)), stream.terminator()]


def _up(**kw):
    stack = Stack(**kw)
    term = stack.terminal()
    term.login()
    assert term.got(odtp.KEEP_ALIVE) == [b""], "authenticated and logged in to the MASTER"
    return stack, term


def test_the_challenge_is_the_masters_salt_and_the_peer_is_a_terminal():
    stack, term = _up()
    peer = stack.net.peer(PEER)
    assert peer["CONNECTION"] == "YES" and peer["OPTIONS"] == b"TERMINAL=1;"
    assert peer["CALLSIGN"].startswith(b"C31AG")
    assert len(term.got(odtp.CHALLENGE)[0]) == 4


def test_voice_both_ways_and_subscriptions():
    stack, term = _up()
    term.subscribe(666)
    assert term.got(odtp.SUBSCRIPTION) == [b""]
    stack.advance(0.2)
    assert stack.net.peer(PEER)["OPTIONS"] == b"TERMINAL=1;TS2=666;"

    ambe = os.urandom(27)
    stack.net.to_peer(PEER, _call(666, 2, ambe))
    assert odtp.parse_voice_header(term.got(odtp.VOICE_HEADER)[0]) == (666, RADIO, False)
    assert term.got(odtp.DMR_AUDIO_FRAME) == [ambe, ambe]
    assert len(term.got(odtp.VOICE_TERMINATOR)) == 1

    # The source is always the authenticated ID.
    for _ in range(2):
        term.send(odtp.VOICE_HEADER, odtp.voice_header(666, 9999999, False))
    term.send(odtp.DMR_AUDIO_FRAME, ambe)
    term.send(odtp.VOICE_TERMINATOR)
    frames = [parse_dmrd_header(f) for f in stack.net.dmrd]
    assert [(h.frame_type, h.dtype_vseq) for h in frames] == [(2, 1)] * 3 + [(1, 0), (2, 2)]
    assert {(int.from_bytes(h.rf_src, "big"), int.from_bytes(h.dst_id, "big"), h.slot, h.peer_id) for h in frames} == {
        (TERMINAL_ID, 666, 2, PEER)}
    assert voice_bits(stack.net.dmrd[3][20:53]).tobytes() == ambe

    term.send(odtp.CANCELLING)
    stack.advance(0.2)
    assert stack.net.peer(PEER)["OPTIONS"] == b"TERMINAL=1;"


def test_a_burst_of_subscriptions_is_one_rpto():
    stack, term = _up()
    for tg in (214, 666, 901, 902, 903):
        term.subscribe(tg)
    stack.advance(0.2)
    assert stack.net.options() == [b"TERMINAL=1;", b"TERMINAL=1;TS2=214,666,901,902,903;"]


def test_rx_ends_one_second_after_the_last_frame_without_a_terminator():
    stack, term = _up()
    frames = _call(666, 6, os.urandom(27))[:-1]  # the MASTER never sends the terminator
    for frame in frames:
        stack.net.to_peer(PEER, [frame])
        stack.advance(0.5)  # frames keep the call open
    assert len(term.got(odtp.DMR_AUDIO_FRAME)) == 6 and not term.got(odtp.VOICE_TERMINATOR)
    stack.advance(0.6)
    assert len(term.got(odtp.VOICE_TERMINATOR)) == 1
    stack.advance(5.0)
    assert len(term.got(odtp.VOICE_TERMINATOR)) == 1, "once"

    # The next call opens as usual, and its own terminator ends it once.
    stack.net.to_peer(PEER, _call(666, 2, os.urandom(27)))
    stack.advance(2.0)
    assert len(term.got(odtp.VOICE_HEADER)) == 2 and len(term.got(odtp.VOICE_TERMINATOR)) == 2


def test_tx_ends_one_second_after_the_apps_last_frame():
    stack, term = _up()
    term.send(odtp.VOICE_HEADER, odtp.voice_header(666, TERMINAL_ID, False))
    term.send(odtp.DMR_AUDIO_FRAME, os.urandom(27))
    stack.advance(0.9)
    term.send(odtp.DMR_AUDIO_FRAME, os.urandom(27))
    stack.advance(0.9)
    assert [parse_dmrd_header(f).dtype_vseq for f in stack.net.dmrd][-1] != 2, "still talking"
    stack.advance(0.2)  # no gateway tick: the call's own timer
    assert parse_dmrd_header(stack.net.dmrd[-1]).dtype_vseq == 2 and stack.gw.sessions_by_key[TERMINAL_ID].uplink is None


def test_busy_notice_drops_the_rest_of_the_call():
    stack, term = _up()
    frames = _call(666, 3, os.urandom(27))
    stack.net.to_peer(PEER, frames[:2])
    term.send(odtp.BUSY_NOTICE)
    stack.net.to_peer(PEER, frames[2:])
    stack.advance(2.0)
    assert len(term.got(odtp.DMR_AUDIO_FRAME)) == 1 and not term.got(odtp.VOICE_TERMINATOR)


def test_wrong_password_gets_silence_and_no_master_login():
    stack = Stack()
    term = stack.terminal()
    term.login(password=b"wrong")
    assert [t for t, _ in term.inbox] == [odtp.CHALLENGE]
    assert stack.net.peer(PEER) is None and not stack.gw.sessions


def test_a_failed_login_does_not_kick_the_connected_session():
    stack, term = _up()
    intruder = stack.terminal(50001)
    intruder.login(password=b"guess")
    assert [t for t, _ in intruder.inbox] == [odtp.CHALLENGE]
    assert stack.gw.sessions_by_key[TERMINAL_ID].addr == term.addr and len(stack.gw.sessions) == 1
    assert stack.net.peer(PEER)["CONNECTION"] == "YES"
    assert stack.net.sent[-2][4:8] == (TERMINAL_ID * 100 + 97).to_bytes(4, "big"), "the other suffix"

    # The app reconnecting from a new port with the right password replaces the session.
    again = stack.terminal(50002)
    again.login()
    assert again.got(odtp.KEEP_ALIVE) == [b""]
    assert list(stack.gw.sessions) == [again.addr] and stack.gw.sessions_by_key[TERMINAL_ID].suffix == 97
    assert term.got(odtp.CLOSE) == [b""]


def _uplink_sources(stack) -> set:
    return {(int.from_bytes(h.rf_src, "big"), int.from_bytes(h.dst_id, "big"), h.peer_id)
            for h in map(parse_dmrd_header, stack.net.dmrd)}


def test_a_9_digit_login_talks_only_as_its_own_dmr_id():
    login = TERMINAL_ID * 100 + 76
    stack = Stack()
    term = stack.terminal()
    term.login(login)
    assert stack.net.peer(login.to_bytes(4, "big"))["CONNECTION"] == "YES"
    assert stack.gw.sessions_by_key[login].dmr_id == TERMINAL_ID, "login // 100, from the start"
    term.subscribe(9999999, kind=5)  # a private subscription for somebody else changes nothing
    term.send(odtp.VOICE_HEADER, odtp.voice_header(666, 9999999, False))
    term.send(odtp.DMR_AUDIO_FRAME, os.urandom(27))
    term.send(odtp.VOICE_TERMINATOR)
    assert stack.net.dmrd and _uplink_sources(stack) == {(TERMINAL_ID, 666, login.to_bytes(4, "big"))}


def test_private_calls_reach_only_their_addressee():
    stack, term = _up()
    stack.net.to_peer(PEER, _call(2130001, 1, os.urandom(27), private=True))
    assert not term.got(odtp.VOICE_HEADER)
    stack.net.to_peer(PEER, _call(TERMINAL_ID, 1, os.urandom(27), private=True))
    assert odtp.parse_voice_header(term.got(odtp.VOICE_HEADER)[0]) == (TERMINAL_ID, RADIO, True)


def test_timeouts_drop_the_session_and_log_out_of_the_master():
    stack, term = _up()
    for _ in range(5):
        stack.advance(5.0)
        stack.gw.tick()
        term.send(odtp.KEEP_ALIVE)  # pings to the MASTER are answered
    assert stack.net.peer(PEER)["CONNECTION"] == "YES"
    stack.advance(31.0)
    stack.gw.tick()
    assert not stack.gw.sessions and stack.net.sent[-1] == b"RPTCL" + PEER

    half = stack.terminal(50001)
    half.send(odtp.KEEP_ALIVE, struct.pack("<IB", TERMINAL_ID, odtp.SERVICE_OPEN_TERMINAL))
    stack.advance(16.0)
    stack.gw.tick()
    assert not stack.gw.sessions, "a login the terminal never answers"


def test_a_master_that_stops_answering_drops_the_session():
    stack, term = _up()
    stack.net.links.clear()  # the MASTER's answers are lost
    for _ in range(3):  # MAX_MISSED pings go unanswered
        term.send(odtp.KEEP_ALIVE)
        stack.gw.tick()
    assert stack.gw.sessions
    stack.gw.tick()
    assert not stack.gw.sessions and term.got(odtp.CLOSE) == [b""]


def test_not_an_open_terminal_is_ignored():
    stack = Stack()
    term = stack.terminal()
    term.send(odtp.KEEP_ALIVE, struct.pack("<IB", TERMINAL_ID, 0x10))
    assert not term.inbox and stack.net.sent == []


# ── caps: each one refuses what is over it, and only that ──

def _login_many(stack, n, ip="198.51.100.7"):
    terms = []
    for i in range(n):
        term = stack.terminal(51000 + i)
        term.addr = (ip, 51000 + i)
        stack.socket.terminals[term.addr] = term
        term.login(2130100 + i + len(stack.gw.sessions) * 1000)
        terms.append(term)
    return terms


def test_cap_on_sessions_per_ip():
    stack = Stack(settings=OdtpSettings(max_sessions_per_ip=2))
    terms = _login_many(stack, 3)
    assert [bool(t.got(odtp.KEEP_ALIVE)) for t in terms] == [True, True, False]
    other = _login_many(stack, 1, ip="203.0.113.9")
    assert other[0].got(odtp.KEEP_ALIVE)


def test_cap_on_total_sessions():
    stack = Stack(settings=OdtpSettings(max_sessions=2))
    a = _login_many(stack, 1, ip="203.0.113.1") + _login_many(stack, 1, ip="203.0.113.2")
    c = _login_many(stack, 1, ip="203.0.113.3")
    assert all(t.got(odtp.KEEP_ALIVE) for t in a) and not c[0].inbox and len(stack.gw.sessions) == 2


def test_cap_on_logins_per_minute(caplog):
    stack = Stack(settings=OdtpSettings(max_logins_per_minute=2, max_sessions_per_ip=100))
    with caplog.at_level(logging.WARNING):
        terms = _login_many(stack, 5)
    assert [bool(t.got(odtp.KEEP_ALIVE)) for t in terms] == [True, True, False, False, False]
    refusals = [r for r in caplog.records if "refused" in r.getMessage()]
    assert len(refusals) == 1 and "logins per minute" in refusals[0].getMessage(), "one log line a minute"
    stack.advance(61.0)
    assert _login_many(stack, 1)[0].got(odtp.KEEP_ALIVE)


def test_junk_does_not_spend_the_login_cap():
    stack = Stack(settings=OdtpSettings(max_logins_per_minute=2, max_sessions_per_ip=100))
    for i in range(60):  # spoofed datagrams: no valid keep-alive, or not an Open DMR Terminal
        junk = stack.terminal(52000 + i)
        junk.send(odtp.KEEP_ALIVE, b"\x01" if i % 2 else struct.pack("<IB", TERMINAL_ID, 0x10))
    assert not stack.gw.sessions and stack.net.sent == []
    assert [bool(t.got(odtp.KEEP_ALIVE)) for t in _login_many(stack, 3)] == [True, True, False]



def test_an_id_too_big_for_a_dmr_id_opens_no_link():
    stack = Stack(settings=OdtpSettings(max_logins_per_minute=2, max_sessions_per_ip=100))
    for i, number in enumerate((0x1000000, 99_999_999)):  # 8 digits, over 24 bits
        stack.terminal(53000 + i).send(odtp.KEEP_ALIVE, struct.pack("<IB", number, odtp.SERVICE_OPEN_TERMINAL))
    stack.advance(120)
    stack.gw.tick()
    assert not stack.gw.sessions and not stack.net.links and stack.net.sent == []
    assert [bool(t.got(odtp.KEEP_ALIVE)) for t in _login_many(stack, 3)] == [True, True, False]

def test_callsign_file_looks_up_without_loading(tmp_path):
    compact = tmp_path / "compact.json"
    compact.write_text('{"count":3,"results":[{"id":2130035,"callsign":"C31AG","fname":"A"},'
                       '{"id":21300351,"callsign":"WRONG"},{"id":2140001,"callsign":"EA3XYZ"}]}')
    spaced = tmp_path / "spaced.json"
    spaced.write_text('{"results": [{"id": 2130035, "callsign": "C31AG"}]}')
    for path in (compact, spaced):
        assert CallsignFile(str(path))(2130035) == "C31AG"
    lookup = CallsignFile(str(compact))
    assert lookup(213003) == "" and lookup(2130035) == "C31AG"  # a prefix is not an ID
    compact.write_text('{"results":[{"id":213003,"callsign":"NEW"}]}')
    assert lookup(213003) == "", "an ID not in the file is not looked up again"
    compact.unlink()
    assert lookup(2130035) == "C31AG", "found callsigns stay cached"
    later = CallsignFile(str(tmp_path / "later.json"))
    assert later(2130035) == ""
    (tmp_path / "later.json").write_text('{"results":[{"id":2130035,"callsign":"C31AG"}]}')
    assert later(2130035) == "C31AG", "a missing file is looked at again"
