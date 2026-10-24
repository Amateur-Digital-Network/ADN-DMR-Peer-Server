# ADN DMR Peer Server - tests trunking transactions
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

"""EXPERIMENTAL DMRT 0x05-0x0A between sites: source validation, bridge rules, and answers only from a target."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from adn_server.application.subscription.router import SubscriptionRouter
from adn_server.application.subscription.routing_table_import import subscriptions_from_routing_table
from adn_server.application.trunking.legs import SiteLegs
from adn_server.application.trunking.ports import SiteLink
from adn_server.application.trunking.registry import MAX_PENDING_PER_SITE, PENDING_TTL_S, Site, SiteRegistry
from adn_server.application.trunking.signalling import DmrtSignalling
from adn_server.application.trunking.transactions import DmrtTransactions
from adn_server.domain import ID_MAX, bytes_3, bytes_4
from adn_server.domain.trunking import DmrtOp, call_reject, to_network
from adn_server.domain.voice_routing import ForwardLeg, VoiceIngress
from adn_server.infrastructure.acl_router import InMemoryAclRouter
from adn_server.infrastructure.config_loader import acl_build
from adn_server.infrastructure.subscription_store import InMemorySubscriptionStore
from tests.support.dmrtc_wire import (
    dmrt_call_setup,
    dmrt_registration,
    dmrt_reply,
    dmrt_status,
    dmrt_subscription,
    dmrt_udt,
)

A = Site("TRUNK", bytes_4(214000101))
B = Site("TRUNK", bytes_4(214000102))
C = Site("TRUNK-2", bytes_4(214000201))
D = Site("TRUNK-3", bytes_4(214000301))
GONE = Site("TRUNK", bytes_4(214000109))
MS_A, MS_B, MS_C, MS_GONE, UNKNOWN = 2140501, 2140502, 2140503, 2140509, 2149999
NOW = 1_761_300_000.0
UUID = bytes(range(0xA0, 0xB0))
TRANSACTIONS_LOGGER = "adn_server.application.trunking.transactions"


def _acls(sub: str = "PERMIT:ALL") -> dict:
    return {
        "USE_ACL": True,
        "SUB_ACL": acl_build(sub, ID_MAX),
        "TG1_ACL": acl_build("PERMIT:ALL", ID_MAX),
        "TG2_ACL": acl_build("PERMIT:ALL", ID_MAX),
    }


class _Link(SiteLink):
    """Every DMRT the server sent; GONE is registered but not connected."""

    def __init__(self) -> None:
        self.sent: list[tuple[Site, bytes]] = []

    def send(self, site: Site, payload: bytes) -> bool:
        self.sent.append((site, payload))
        return site != GONE

    def connected(self, site: Site) -> bool:
        return site != GONE


class _Stack:
    def __init__(self, *, repeat: bool = True, router: SubscriptionRouter | None = None) -> None:
        self.config: dict[str, Any] = {
            "GLOBAL": _acls(),
            "SYSTEMS": {
                name: {"MODE": "MASTER", "TRUNKING": True, "REPEAT": repeat, **_acls()}
                for name in ("TRUNK", "TRUNK-2", "TRUNK-3")
            },
            "_SUB_MAP": {},
        }
        self.store = InMemorySubscriptionStore()
        self.registry = SiteRegistry()
        self.link = _Link()
        legs = SiteLegs(self.store, self.config, lambda tg: None, lambda: None)
        acl = InMemoryAclRouter()
        transactions = DmrtTransactions(
            self.config, self.registry, self.link, acl, router or SubscriptionRouter(self.store),
        )
        self.signalling = DmrtSignalling(self.config, self.registry, legs, self.link, acl, transactions=transactions)
        for site, ms in ((A, MS_A), (B, MS_B), (C, MS_C), (GONE, MS_GONE)):
            self.send(site, dmrt_registration(0x01, [ms], int(NOW)))

    def send(self, site: Site, payload: bytes, now: float = NOW) -> None:
        self.signalling.received(site, payload, now)

    def subscribe(self, site: Site, *tgs: int) -> None:
        self.send(site, dmrt_subscription(0x03, tgs))

    def got(self, site: Site) -> list[bytes]:
        """What reached ``site`` from another site (not the server's own confirmations)."""
        return [p for to, p in self.link.sent if to == site and 0x45 <= p[4] <= 0x4A]

    def nothing_forwarded(self) -> bool:
        return not any(0x45 <= p[4] <= 0x4A for _, p in self.link.sent)


def test_private_udt_goes_to_the_registered_site_only() -> None:
    """A private UDT reaches the destination radio's site, as 0x45 with the site's own bytes, and no other site."""
    s = _Stack()
    udt = dmrt_udt(UUID, MS_A, MS_B, b"hello")
    s.send(A, udt)
    assert s.got(B) == [to_network(udt)] and to_network(udt)[4] == 0x45
    assert s.got(A) == [] and s.got(C) == []


@pytest.mark.parametrize("src", [UNKNOWN, MS_B], ids=["unregistered", "registered-at-another-site"])
def test_udt_from_an_unregistered_src_is_dropped(src: int, caplog: pytest.LogCaptureFixture) -> None:
    """A site cannot speak for a radio that is not registered there: the UDT is dropped and logged."""
    s = _Stack()
    with caplog.at_level(logging.INFO, logger=TRANSACTIONS_LOGGER):
        s.send(A, dmrt_udt(UUID, src, MS_C, b"spoof"))
    assert s.nothing_forwarded()
    assert [r.getMessage() for r in caplog.records if r.name == TRANSACTIONS_LOGGER] == [
        f"(TRUNK) EXPERIMENTAL DMRT 0x05 from site 214000101: SUB {src} not registered there or denied by SUB_ACL, "
        "dropped"
    ]


@pytest.mark.parametrize("scope", ["GLOBAL", "TRUNK"])
def test_udt_from_an_acl_denied_src_is_dropped(scope: str, caplog: pytest.LogCaptureFixture) -> None:
    """A radio registered before its SUB_ACL denied it (GLOBAL or the site's system) sends nothing more."""
    s = _Stack()
    cfg = s.config["GLOBAL"] if scope == "GLOBAL" else s.config["SYSTEMS"]["TRUNK"]
    cfg["SUB_ACL"] = acl_build(f"DENY:{MS_A}", ID_MAX)
    with caplog.at_level(logging.INFO, logger=TRANSACTIONS_LOGGER):
        s.send(A, dmrt_udt(UUID, MS_A, MS_B, b"denied"))
        s.send(A, dmrt_call_setup(UUID, MS_A, MS_B))
        s.send(A, dmrt_status(UUID, MS_A, MS_B, 3))
    assert s.nothing_forwarded()
    assert len([r for r in caplog.records if r.name == TRANSACTIONS_LOGGER]) == 3


def test_a_repeated_drop_is_logged_once_a_minute(caplog: pytest.LogCaptureFixture) -> None:
    """A site repeating a refused message does not flood the log."""
    s = _Stack()
    with caplog.at_level(logging.INFO, logger=TRANSACTIONS_LOGGER):
        for now in (NOW, NOW + 30, NOW + 59, NOW + 60):
            s.send(A, dmrt_udt(UUID, UNKNOWN, MS_B, b"again"), now)
    assert len([r for r in caplog.records if r.name == TRANSACTIONS_LOGGER]) == 2


def test_group_udt_follows_bridges() -> None:
    """A group UDT reaches the trunk systems voice on its talkgroup would reach, never one bridged under another TG."""
    s = _Stack()
    s.subscribe(A, 91)
    s.subscribe(C, 91, 92)
    s.subscribe(D, 93)
    # After D's subscription, which created table 93: TG 93 of TRUNK is bridged to TRUNK-3 only as TG 2141.
    for sub in subscriptions_from_routing_table({"#93": [
        {"SYSTEM": "TRUNK", "TS": 1, "TGID": bytes_3(93), "ACTIVE": True, "TO_TYPE": "ON"},
        {"SYSTEM": "TRUNK-3", "TS": 1, "TGID": bytes_3(2141), "ACTIVE": True, "TO_TYPE": "ON"},
    ]}):
        s.store.upsert(sub)

    s.send(A, dmrt_udt(UUID, MS_A, 91, b"bridged", group=True))
    assert s.got(C) == [to_network(dmrt_udt(UUID, MS_A, 91, b"bridged", group=True))]

    s.send(A, dmrt_udt(bytes(16), MS_A, 92, b"no bridge", group=True))
    s.send(A, dmrt_udt(bytes(16), MS_A, 93, b"translated", group=True))
    assert len(s.got(C)) == 1 and s.got(D) == [] and s.got(A) == []


def test_group_messages_follow_the_bridges_the_given_router_resolves() -> None:
    class _BridgedToTrunk2(SubscriptionRouter):
        def resolve(self, ingress: VoiceIngress) -> tuple[ForwardLeg, ...]:
            return (ForwardLeg("TRUNK-2", ingress.slot, ingress.dst_tgid),)

    s = _Stack(router=_BridgedToTrunk2(InMemorySubscriptionStore()))
    s.subscribe(C, 92)

    s.send(A, dmrt_udt(UUID, MS_A, 92, b"bridged", group=True))

    assert s.got(C) == [to_network(dmrt_udt(UUID, MS_A, 92, b"bridged", group=True))]


@pytest.mark.parametrize("repeat", [True, False])
def test_same_system_delivery_honours_repeat(repeat: bool) -> None:
    """Another site of the sender's system gets a group UDT only when that system repeats; the sender never does."""
    s = _Stack(repeat=repeat)
    s.subscribe(A, 91)
    s.subscribe(B, 91)
    s.send(A, dmrt_udt(UUID, MS_A, 91, b"local", group=True))
    assert len(s.got(B)) == (1 if repeat else 0)
    assert s.got(A) == []


def test_udt_accept_reaches_the_origin_as_0x46() -> None:
    """The destination site's 0x06 goes back to the site that sent the UDT, as 0x46."""
    s = _Stack()
    s.send(A, dmrt_udt(UUID, MS_A, MS_B, b"hello"))
    accept = dmrt_reply(0x06, UUID, MS_B, MS_A)
    s.send(B, accept)
    assert s.got(A) == [to_network(accept)] and to_network(accept)[4] == 0x46


def test_udt_accept_from_a_non_target_is_dropped() -> None:
    """Only a site the UDT went to may answer it, and only for a UUID the server routed."""
    s = _Stack()
    s.send(A, dmrt_udt(UUID, MS_A, MS_B, b"hello"))
    s.send(C, dmrt_reply(0x06, UUID, MS_C, MS_A))
    s.send(B, dmrt_reply(0x06, bytes(16), MS_B, MS_A))
    s.send(B, dmrt_reply(0x08, UUID, MS_B, MS_A))
    assert s.got(A) == []


def test_a_target_cannot_reopen_a_pending_uuid() -> None:
    """Every target has seen the UUID: one reusing it for its own UDT cannot take the origin's answers."""
    s = _Stack()
    s.send(A, dmrt_udt(UUID, MS_A, MS_B, b"hello"))
    s.send(B, dmrt_udt(UUID, MS_B, MS_C, b"mine"))
    accept = dmrt_reply(0x06, UUID, MS_B, MS_A)
    s.send(B, accept)
    assert s.got(C) == []
    assert s.got(A) == [to_network(accept)]


@pytest.mark.parametrize("op", [0x08, 0x09], ids=["accept", "reject"])
def test_call_setup_is_forwarded_as_0x47_and_answered_back(op: int) -> None:
    """A private call setup reaches the called radio's site as 0x47; its accept or reject goes back to the caller."""
    s = _Stack()
    setup = dmrt_call_setup(UUID, MS_A, MS_B, 0x20)
    s.send(A, setup)
    assert s.got(B) == [to_network(setup)] and to_network(setup)[4] == 0x47
    answer = dmrt_reply(op, UUID, MS_B, MS_A)
    s.send(B, answer)
    assert s.got(A) == [to_network(answer)] and s.got(A)[0][4] == op | 0x40


@pytest.mark.parametrize("dst", [UNKNOWN, MS_A, MS_GONE], ids=["unknown", "same-site", "site-not-connected"])
def test_call_setup_to_an_unknown_radio_is_rejected(dst: int) -> None:
    """When no other connected site holds the called radio, the caller's site gets 0x49 at once."""
    s = _Stack()
    s.send(A, dmrt_call_setup(UUID, MS_A, dst))
    assert [p for to, p in s.link.sent if p[4] >= 0x45] == [call_reject(UUID, MS_A, dst)]
    assert s.link.sent[-1][0] == A


def test_pending_expires() -> None:
    """An answer later than the transaction's window is not routed, and the tick forgets the transaction."""
    s = _Stack()
    s.send(A, dmrt_udt(UUID, MS_A, MS_B, b"hello"))
    s.send(B, dmrt_reply(0x06, UUID, MS_B, MS_A), NOW + PENDING_TTL_S + 1)
    assert s.got(A) == []
    s.registry.trim(NOW + PENDING_TTL_S + 1)
    assert s.registry.pending(UUID, DmrtOp.UDT, NOW) is None


def test_a_site_past_its_open_transactions_is_refused(caplog: pytest.LogCaptureFixture) -> None:
    """A UDT is not forwarded and a call setup is rejected at once, logged once a minute."""
    s = _Stack()
    for n in range(MAX_PENDING_PER_SITE):
        s.send(A, dmrt_udt(n.to_bytes(16, "big"), MS_A, MS_B, b"hello"))
    forwarded = len(s.got(B))
    setup = dmrt_call_setup(UUID, MS_A, MS_B)
    caplog.clear()

    with caplog.at_level(logging.INFO, logger=TRANSACTIONS_LOGGER):
        s.send(A, dmrt_udt(UUID, MS_A, MS_B, b"one more"))
        s.send(A, dmrt_udt(UUID, MS_A, MS_B, b"and another"))
        s.send(A, setup)

    assert forwarded == MAX_PENDING_PER_SITE and len(s.got(B)) == forwarded
    assert s.link.sent[-1] == (A, call_reject(UUID, MS_A, MS_B))
    assert [r.getMessage() for r in caplog.records if r.name == TRANSACTIONS_LOGGER] == [
        "(TRUNK) EXPERIMENTAL DMRT 0x05 from site 214000101 refused: too many open transactions",
        "(TRUNK) EXPERIMENTAL DMRT 0x07 from site 214000101 refused: too many open transactions",
    ]


def test_status_goes_to_the_group_or_the_registered_radio() -> None:
    """A status transfer is routed like a UDT, as 0x4A, and opens no transaction."""
    s = _Stack()
    s.subscribe(A, 91)
    s.subscribe(B, 91)
    group = dmrt_status(UUID, MS_A, 91, 5, group=True)
    s.send(A, group)
    private = dmrt_status(bytes(16), MS_A, MS_C, 6)
    s.send(A, private)
    assert s.got(B) == [to_network(group)] and s.got(C) == [to_network(private)]
    assert to_network(group)[4] == 0x4A
    s.send(B, dmrt_reply(0x06, UUID, MS_B, MS_A))
    assert s.got(A) == []


def test_a_status_opens_no_transaction() -> None:
    """A status is never answered, so its UUID stays free: another site may send a status with it at once."""
    s = _Stack()
    s.subscribe(A, 91)
    s.subscribe(B, 91)
    s.send(A, dmrt_status(UUID, MS_A, 91, 5, group=True))
    again = dmrt_status(UUID, MS_B, 91, 6, group=True)
    s.send(B, again)
    assert s.got(A) == [to_network(again)]
