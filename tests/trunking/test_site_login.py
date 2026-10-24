# ADN DMR Peer Server - tests trunking site login
#
# Copyright (C) 2026  Rodrigo Pérez, CE5RPY <ce5rpy@qmd.cl>
# Copyright (C) 2026  Joaquin Madrid Belando, EA5GVK <ea5gvk@gmail.com>
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

"""DTC* login, keepalive and leaving of a Tier III site, as DMRGateway (TrunkingEnabled=1) does them."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

import pytest
from tests.support.dmrtc_wire import GatewayLink, dtcc, dtccl, dtck, dtcl, dtco, dtcping
from tests.support.trunk_site_stack import PASS, DmrtcSite, build_trunk_site_stack

from adn_server.application.subscription.site_tg_ops import site_tg_held
from adn_server.domain import bytes_3, bytes_4
from adn_server.domain.trunking import DTCCL, DTCNAK
from adn_server.infrastructure.hbp_constants import MSTNAK, RPTC, RPTK, RPTL, RPTO, RPTPING

pytestmark = pytest.mark.integration

A, A_ADDR = 214000101, ("10.0.1.1", 40001)
B, B_ADDR = 214000102, ("10.0.1.2", 40002)
HS, HS_ADDR = 2140001, ("10.0.0.1", 62031)
TG = 2141
MS = 2149050


def _peer(s, site: DmrtcSite) -> dict | None:
    return s.proto("TRUNK")._peers.get(site.peer_id)


def test_config_is_acked_with_dtcack() -> None:
    """DMRGateway starts MasterLogin, so dmrtc subscribes, only when the config ACK is DTCACK."""
    s = build_trunk_site_stack()
    site = DmrtcSite(s, "TRUNK", A, A_ADDR)
    gateway = GatewayLink(site.peer_id, PASS)

    login = gateway.login()
    salt_ack = site.send(login)
    assert len(login) == 8 and len(salt_ack) == 1
    assert salt_ack[0][:6] == b"DTCACK" and len(salt_ack[0]) == 10
    auth = gateway.received(salt_ack[0])
    assert len(auth) == 40
    assert site.send(auth) == [b"DTCACK" + site.peer_id]
    config = gateway.received(b"DTCACK" + site.peer_id)
    assert len(config) == 302
    assert site.send(config) == [b"DTCACK" + site.peer_id]
    assert gateway.received(b"DTCACK" + site.peer_id) is None

    assert gateway.status == "RUNNING" and gateway.master_login
    assert _peer(s, site)["CONNECTION"] == "YES"


def test_a_23_cm_site_logs_in() -> None:
    """DMRGateway prints each frequency with %09u, so on 23 cm both take ten digits and DTCC is 304 bytes."""
    s = build_trunk_site_stack()
    site = DmrtcSite(s, "TRUNK", A, A_ADDR)
    gateway = site.gateway
    auth = gateway.received(site.send(gateway.login())[0])
    config = gateway.received(site.send(auth)[0])
    config = config[:16] + b"1297500000" + b"1291500000" + config[34:]
    assert len(config) == 304

    assert site.send(config) == [b"DTCACK" + site.peer_id]
    assert _peer(s, site)["CONNECTION"] == "YES"


def test_a_wrong_password_gets_dtcnak_and_removes_the_site() -> None:
    s = build_trunk_site_stack()
    site = DmrtcSite(s, "TRUNK", A, A_ADDR, password=b"wrong")
    gateway = site.gateway

    salt_ack = site.send(gateway.login())
    nak = site.send(gateway.received(salt_ack[0]))

    assert nak == [DTCNAK + site.peer_id]
    gateway.received(nak[0])
    assert gateway.status == "FAILED"
    assert _peer(s, site) is None


def test_users_pass_takes_precedence_over_the_passphrase() -> None:
    s = build_trunk_site_stack(user_passwords={A: b"own-pass"})

    assert s.site("TRUNK", A, A_ADDR, password=b"own-pass").gateway.master_login
    refused = DmrtcSite(s, "TRUNK", A, ("10.0.1.9", 40009))
    salt_ack = refused.send(refused.gateway.login())
    assert refused.send(refused.gateway.received(salt_ack[0])) == [DTCNAK + refused.peer_id]


def test_dtco_is_acked_and_not_applied(monkeypatch) -> None:
    """A site holds talkgroups by DMRT only: its options never become static legs or system OPTIONS."""
    s = build_trunk_site_stack()
    applied: list[str] = []
    monkeypatch.setattr(s.routing, "options_config_for_system", lambda system, *a, **k: applied.append(system))

    site = s.site("TRUNK", A, A_ADDR, options=b"TS1=91;TS2=92;PASS=secret")

    assert site.gateway.master_login
    assert site.written()[-1] == b"DTCACK" + site.peer_id
    assert "OPTIONS" not in s.config["SYSTEMS"]["TRUNK"]
    assert "OPTIONS" not in _peer(s, site)
    assert applied == []
    assert not s.store.has_table("91") and not s.store.has_table("92")


def test_dtcping_gets_dtcpong_and_refreshes_last_ping() -> None:
    s = build_trunk_site_stack()
    site = s.site("TRUNK", A, A_ADDR)
    s.advance(5)

    assert site.send(dtcping(site.peer_id)) == [b"DTCPONG" + site.peer_id]
    assert _peer(s, site)["LAST_PING"] == s.clock.time()


def test_dtcpong_from_a_client_is_not_answered() -> None:
    """DTCPONG shares its first four bytes with DTCPING: only the full command is a ping."""
    s = build_trunk_site_stack()
    site = s.site("TRUNK", A, A_ADDR)
    last_ping = _peer(s, site)["LAST_PING"]
    s.advance(5)

    assert site.send(b"DTCPONG" + site.peer_id) == []
    assert _peer(s, site)["LAST_PING"] == last_ping


def test_dtccl_gets_no_reply_releases_talkgroups_and_keeps_registrations(tmp_path) -> None:
    path = tmp_path / "trunk_registrations.json"
    s = build_trunk_site_stack(path=str(path))
    site = s.site("TRUNK", A, A_ADDR)
    site.register(MS)
    site.subscribe(TG)

    assert site.send(dtccl(site.peer_id)) == []

    assert _peer(s, site) is None
    assert not site_tg_held(s.store, TG, "TRUNK")
    s.sites.save()
    assert [entry[:3] for entry in json.loads(path.read_text())] == [[MS, "TRUNK", A]]


def test_dtccl_from_another_address_changes_nothing() -> None:
    s = build_trunk_site_stack()
    site = s.site("TRUNK", A, A_ADDR)
    site.subscribe(TG)
    spoof = DmrtcSite(s, "TRUNK", A, ("10.9.9.9", 40009))

    assert spoof.send(dtccl(site.peer_id)) == []

    assert _peer(s, site)["CONNECTION"] == "YES"
    assert site_tg_held(s.store, TG, "TRUNK")


def test_a_site_logging_in_again_is_not_connected_and_gets_no_dmrt() -> None:
    s = build_trunk_site_stack()
    site = s.site("TRUNK", A, A_ADDR)
    proto = s.proto("TRUNK")
    assert proto.site_connected(site.peer_id)

    site.send(dtcl(site.peer_id))
    written = len(site.written())

    assert not proto.site_connected(site.peer_id)
    assert proto.send_dmrt(site.peer_id, b"DMRT" + bytes((0x41,))) is False
    assert len(site.written()) == written


def test_a_keepalive_does_not_pin_the_site_radios_again() -> None:
    """Only a completed login pins them; a DTCPING must not bring back what SUB_MAP has dropped since."""
    s = build_trunk_site_stack()
    site = s.site("TRUNK", A, A_ADDR)
    site.register(MS)
    del s.config["_SUB_MAP"][bytes_3(MS)]

    site.send(dtcping(site.peer_id))

    assert bytes_3(MS) not in s.config["_SUB_MAP"]


def test_a_timeout_writes_dtccl_and_releases_its_talkgroups() -> None:
    """DTCCL, unlike MSTCL, makes DMRGateway tell dmrtc (0xC2) that the master closed."""
    s = build_trunk_site_stack()
    site = s.site("TRUNK", A, A_ADDR)
    site.subscribe(TG)
    s.advance(31)

    s.maintenance("TRUNK")

    assert site.written()[-1] == DTCCL + site.peer_id
    assert _peer(s, site) is None
    assert not site_tg_held(s.store, TG, "TRUNK")


def test_dereg_writes_dtccl() -> None:
    s = build_trunk_site_stack()
    site = s.site("TRUNK", A, A_ADDR)

    s.proto("TRUNK").dereg()

    assert site.written()[-1] == DTCCL + site.peer_id


@pytest.mark.parametrize(
    "login",
    [
        RPTL + bytes_4(A),
        RPTK + bytes_4(A) + bytes(32),
        RPTC + bytes_4(A) + bytes(294),
        RPTO + bytes_4(A) + b"TS1=91;",
        RPTPING + bytes_4(A),
    ],
    ids=["RPTL", "RPTK", "RPTC", "RPTO", "RPTPING"],
)
def test_classic_login_is_refused_with_mstnak(login: bytes, caplog) -> None:
    """A gateway without TrunkingEnabled=1 is told why at once, instead of half logging in."""
    s = build_trunk_site_stack()
    site = DmrtcSite(s, "TRUNK", A, A_ADDR)

    with caplog.at_level(logging.WARNING):
        assert site.send(login) == [MSTNAK + site.peer_id]

    assert _peer(s, site) is None
    assert "classic HBP login refused on a TRUNKING system" in caplog.text


def test_dtc_logins_on_a_classic_master_behave_as_before(caplog) -> None:
    s = build_trunk_site_stack()
    site = DmrtcSite(s, "MASTER-A", A, A_ADDR)

    with caplog.at_level(logging.ERROR):
        assert site.send(dtcl(site.peer_id)) == []

    assert s.proto("MASTER-A")._peers == {}
    assert "Unrecognized command" in caplog.text


def _callsign_mismatch(s) -> DmrtcSite:
    s.config["_PEER_IDS"] = {int(str(A)[:7]): "CE1TEST"}
    s.config["SYSTEMS"]["TRUNK"]["ALLOW_UNREG_ID"] = False
    site = DmrtcSite(s, "TRUNK", A, A_ADDR)
    site.send(site.gateway.received(site.send(site.gateway.login())[0]))
    site.send(dtcc(site.peer_id, b"CE9OTHER"))
    return site


def _not_logged_in(packet: Callable[[bytes], bytes]) -> Callable[..., DmrtcSite]:
    def scenario(s) -> DmrtcSite:
        site = DmrtcSite(s, "TRUNK", A, A_ADDR)
        site.send(packet(site.peer_id))
        return site

    return scenario


def _wrong_password(s) -> DmrtcSite:
    site = DmrtcSite(s, "TRUNK", A, A_ADDR, password=b"wrong")
    site.send(site.gateway.received(site.send(site.gateway.login())[0]))
    return site


_REFUSALS = {
    "DTCK-wrong-password": _wrong_password,
    "DTCK-not-logging-in": _not_logged_in(lambda p: dtck(p, bytes(4), PASS)),
    "DTCC-not-logging-in": _not_logged_in(dtcc),
    "DTCC-callsign-mismatch": _callsign_mismatch,
    "DTCO-not-logged-in": _not_logged_in(lambda p: dtco(p, b"TS1=91;")),
    "DTCPING-not-logged-in": _not_logged_in(dtcping),
}


@pytest.mark.parametrize("refusal", list(_REFUSALS))
def test_every_refusal_to_a_site_is_dtcnak(refusal: str) -> None:
    s = build_trunk_site_stack()

    site = _REFUSALS[refusal](s)

    assert site.written()[-1] == DTCNAK + site.peer_id
    assert MSTNAK not in {packet[:6] for packet in site.written()}


def test_max_peers_refuses_the_next_site() -> None:
    s = build_trunk_site_stack(config_overrides={"TRUNK": {"MAX_PEERS": 1}})
    s.site("TRUNK", A, A_ADDR)
    site_b = DmrtcSite(s, "TRUNK", B, B_ADDR)

    assert site_b.send(dtcl(site_b.peer_id)) == [DTCNAK + site_b.peer_id]
    assert _peer(s, site_b) is None


def test_a_lower_case_callsign_logs_in_with_allow_unreg_id_false() -> None:
    """The callsign check of #151 ignores case for sites too."""
    s = build_trunk_site_stack(
        config_overrides={"TRUNK": {"ALLOW_UNREG_ID": False}, "_PEER_IDS": {int(str(A)[:7]): "CE1TEST"}},
    )

    site = s.site("TRUNK", A, A_ADDR, callsign=b"ce1test")

    assert site.gateway.master_login


_TRUNCATED = {
    "DTCL-4": lambda p: dtcl(p)[:4],
    "DTCL-7": lambda p: dtcl(p)[:7],
    "DTCL-9": lambda p: dtcl(p) + b"\x00",
    "DTCK-39": lambda p: dtck(p, bytes(4), PASS)[:39],
    "DTCK-41": lambda p: dtck(p, bytes(4), PASS) + b"\x00",
    "DTCC-301": lambda p: dtcc(p)[:301],
    "DTCC-303": lambda p: dtcc(p) + b"\x00",
    "DTCPING-10": lambda p: dtcping(p)[:10],
    "DTCPING-12": lambda p: dtcping(p) + b"\x00",
    "DTCCL-8": lambda p: dtccl(p)[:8],
    "DTCCL-10": lambda p: dtccl(p) + b"\x00",
}


@pytest.mark.parametrize("frame", list(_TRUNCATED), ids=list(_TRUNCATED))
def test_truncated_login_frames_change_nothing(frame: str) -> None:
    """A login frame one byte off is not the command it starts like: no reply, no new or changed site."""
    s = build_trunk_site_stack()
    site = s.site("TRUNK", A, A_ADDR)
    before = dict(_peer(s, site))
    s.advance(5)

    assert site.send(_TRUNCATED[frame](site.peer_id)) == []
    assert s.proto("TRUNK")._peers == {site.peer_id: before}


def test_a_site_relogging_within_the_reset_window_keeps_its_subscriptions() -> None:
    """The last site timing out must not leave a bridge reset behind for the one logging straight back in."""
    s = build_trunk_site_stack()
    hotspot = s.hotspot("MASTER-A", HS, HS_ADDR, options=f"TS2={TG};".encode())
    site = s.site("TRUNK", A, A_ADDR)
    site.subscribe(TG)
    s.advance(31)
    s.maintenance("TRUNK")

    site.login().subscribe(TG)
    s.bridge_reset()

    frames = hotspot.call(hotspot.over(TG, rf_src=2140101, stream_id=0x0A0A0A0A))
    assert len(site.dmrd()) == len(frames)


def test_a_refused_login_reset_is_healed_by_the_tick(caplog) -> None:
    """A refused login with no site connected sets a bridge reset that idles what a site subscribes meanwhile."""
    denied = 214000199
    s = build_trunk_site_stack(config_overrides={"TRUNK": {"REG_ACL": (False, [(denied, denied)])}})
    hotspot = s.hotspot("MASTER-A", HS, HS_ADDR, options=f"TS2={TG};".encode())
    refused = DmrtcSite(s, "TRUNK", denied, ("10.0.1.99", 40099))
    assert refused.send(dtcl(refused.peer_id)) == [DTCNAK + refused.peer_id]
    site = s.site("TRUNK", A, A_ADDR)
    site.subscribe(TG)
    s.bridge_reset()
    assert not site_tg_held(s.store, TG, "TRUNK")

    with caplog.at_level(logging.INFO):
        s.tick()

    assert f"Tier III talkgroup {TG} re-armed after a bridge reset" in caplog.text
    frames = hotspot.call(hotspot.over(TG, rf_src=2140101, stream_id=0x0A0A0A0A))
    assert len(site.dmrd()) == len(frames)

