"""HBP PEER login and keepalive (PeerLogin), shared by the PEER mode and the ODTP gateway."""

import logging
from binascii import a2b_hex
from hashlib import sha256

from adn_server.application.hbp_peer_login import PeerLogin

PEER = (213003598).to_bytes(4, "big")
SALT = (0x12345678).to_bytes(4, "big")


def _login(**kw):
    sent = []
    config = {"RADIO_ID": PEER, "PASSPHRASE": b"passw0rd", "CALLSIGN": b"N0CALL  ", "OPTIONS": b"TS2=9990"}
    config.update(kw.pop("config", {}))
    stats = {}
    login = PeerLogin("TEST", config, stats, sent.append, logging.getLogger("test"), **kw)
    return login, stats, sent


def _rpta(peer=PEER):
    return b"RPTACK" + peer


def test_full_login_reaches_yes_with_the_passphrase_hash():
    login, stats, sent = _login()
    login.tick(3)
    assert sent[-1] == b"RPTL" + PEER and stats["CONNECTION"] == "RPTL_SENT"
    login.datagram(b"RPTA", b"RPTACK" + SALT)
    assert sent[-1] == b"RPTK" + PEER + a2b_hex(sha256(SALT + b"passw0rd").hexdigest())
    login.datagram(b"RPTA", _rpta())
    assert sent[-1].startswith(b"RPTC" + PEER + b"N0CALL  ")
    login.datagram(b"RPTA", _rpta())
    assert sent[-1] == b"RPTO" + PEER + b"TS2=9990"
    login.datagram(b"RPTA", _rpta())
    assert stats["CONNECTION"] == "YES"


def test_without_options_the_config_ack_completes_the_login():
    login, stats, sent = _login(config={"OPTIONS": b""})
    login.tick(3)
    for data in (b"RPTACK" + SALT, _rpta(), _rpta()):
        login.datagram(b"RPTA", data)
    assert stats["CONNECTION"] == "YES" and not any(p.startswith(b"RPTO") for p in sent)


def test_connected_peer_pings_and_logs_in_again_after_missed_pongs():
    login, stats, sent = _login()
    stats["CONNECTION"] = "YES"
    login.tick(3)
    assert sent[-1] == b"RPTPING" + PEER and stats["PING_OUTSTANDING"]
    login.datagram(b"MSTP", b"MSTPONG" + PEER)
    assert stats["PINGS_ACKD"] == 1 and not stats["PING_OUTSTANDING"]
    for _ in range(4):
        login.tick(3)
    assert sent[-1] == b"RPTL" + PEER and stats["CONNECTION"] == "RPTL_SENT"


def test_master_nak_and_close_reset_the_connection():
    login, stats, _ = _login()
    stats["CONNECTION"] = "YES"
    login.datagram(b"MSTN", b"MSTNAK" + PEER)
    assert stats["CONNECTION"] == "NO"
    stats["CONNECTION"] = "YES"
    login.datagram(b"MSTC", b"MSTCL" + PEER)
    assert stats["CONNECTION"] == "NO"


def test_an_ack_for_another_peer_resets_the_login():
    login, stats, _ = _login()
    stats["CONNECTION"] = "AUTHENTICATED"
    login.datagram(b"RPTA", _rpta((1).to_bytes(4, "big")))
    assert stats["CONNECTION"] == "NO"


def test_deferred_key_waits_for_the_caller():
    salts = []
    login, stats, sent = _login(on_salt=lambda salt: salts.append(salt))
    login.tick(3)
    login.datagram(b"RPTA", b"RPTACK" + SALT)
    assert salts == [SALT] and sent[-1] == b"RPTL" + PEER and stats["CONNECTION"] == "RPTL_SENT"
    login.send_key(b"\x01" * 32)
    assert sent[-1] == b"RPTK" + PEER + b"\x01" * 32 and stats["CONNECTION"] == "AUTHENTICATED"


def test_unrelated_commands_are_left_to_the_caller():
    login, _, _ = _login()
    assert login.datagram(b"DMRD", b"DMRD") is False


def test_hbp_peer_mode_logs_in_through_peer_login():
    from adn_server.infrastructure.acl_router import InMemoryAclRouter
    from adn_server.infrastructure.twisted_adapters.udp_hbp import HBPProtocol
    from tests.support.hbp_repeat_stack import RecordingTransport

    master = ("203.0.113.7", 62031)
    config = {
        "GLOBAL": {"USE_ACL": False, "MAX_MISSED": 3},
        "SYSTEMS": {"PEER-1": {
            "MODE": "PEER", "ENABLED": True, "RADIO_ID": PEER, "PASSPHRASE": b"passw0rd",
            "MASTER_SOCKADDR": master, "USE_ACL": False, "OPTIONS": b"",
        }},
    }
    hbp = HBPProtocol("PEER-1", config, router=InMemoryAclRouter())
    hbp.transport = RecordingTransport()
    hbp._peer_maintenance_loop()
    for data in (b"RPTACK" + SALT, _rpta(), _rpta()):
        hbp._peer_datagram_received(data, master)
    sent = hbp.transport.for_addr(master)
    assert sent[0] == b"RPTL" + PEER
    assert sent[1] == b"RPTK" + PEER + a2b_hex(sha256(SALT + b"passw0rd").hexdigest())
    assert sent[2].startswith(b"RPTC" + PEER)
    assert hbp._stats["CONNECTION"] == "YES"
