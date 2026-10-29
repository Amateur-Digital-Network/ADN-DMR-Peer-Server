# ADN DMR Peer Server - tests support trunk site stack
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

"""Tier III sites, hotspots and OpenBridges on the real protocols and routing, wired as bootstrap wires them."""

from __future__ import annotations

from dataclasses import replace
from itertools import zip_longest
from typing import Any

from bitarray import bitarray
from tests.harness.deterministic import (
    DeterministicScenario,
    FakeClock,
    FakeHbpProtocol,
    FakeObpProtocol,
    FakeReportFactory,
    PEER_MAX,
    FakeReportSender,
    PacketSpec,
    acl_permit_all,
    active_routing_table,
    add_openbridge_system,
    minimal_config,
    patch_routing_wall_time,
)
from tests.support.dmrtc_wire import (
    GatewayLink,
    dmrt_registration,
    dmrt_subscription,
    read_deregistration,
    read_registration_confirmation,
    read_subscription_confirmation,
    site_dmrd,
)
from tests.support.hbp_repeat_stack import RecordingTransport
from twisted.internet.defer import maybeDeferred

from adn_server.application.reporting_use_cases import ReportingUseCases
from adn_server.application.routing_use_cases import RoutingUseCases
from adn_server.application.subscription.echo_seed import seed_echo_routing_table
from adn_server.application.subscription.store_sync import replace_store_from_routing_table
from adn_server.application.trunking.config import trunk_systems
from adn_server.domain import HBPF_VOICE, bytes_4
from adn_server.domain.dmr import decode
from adn_server.domain.dmr.bptc import decode_emblc, encode_emblc, encode_header_lc
from adn_server.infrastructure.acl_router import InMemoryAclRouter
from adn_server.infrastructure.bootstrap.trunking import build_trunk_sites
from adn_server.infrastructure.config_normalizer import ensure_system_runtime_config
from adn_server.infrastructure.hbp_constants import RPTACK, RPTC, RPTK, RPTL, RPTO
from adn_server.infrastructure.persistence.trunk_registration_store import JsonTrunkRegistrationStore
from adn_server.infrastructure.subscription_store import InMemorySubscriptionStore
from adn_server.infrastructure.talker_alias_emblc import default_ta_emblc_encoder
from adn_server.infrastructure.twisted_adapters.udp_hbp import HBPProtocol, _calc_hash
from adn_server.infrastructure.twisted_adapters.udp_hbp_trunk import TrunkSiteProtocol

PASS = b"trunk-pass"
FRAME_S = 0.06


def uuid_of(n: int) -> bytes:
    return bytes([n]) * 16


def over(
    peer_id: bytes,
    dst: int,
    *,
    rf_src: int,
    stream_id: int,
    slot: int,
    unit: bool = False,
    bursts: int = 6,
    lc: bytes | None = None,
    terminator: bool = True,
) -> list[bytes]:
    """One over as an MMDVM sends it (55 bytes): header, bursts A.. and terminator; ``lc`` goes in the header."""
    spec = PacketSpec(
        peer_id=int.from_bytes(peer_id, "big"), rf_src=rf_src, dst_id=dst, slot=slot, stream_id=stream_id,
        call_type="unit" if unit else "group",
    )
    head = DeterministicScenario.voice_head_spec(spec)
    if lc is not None:
        bits = bitarray(endian="big")
        bits.frombytes(head.payload)
        h_lc = encode_header_lc(lc)
        head = replace(head, payload=(h_lc[0:98] + bits[98:166] + h_lc[98:196]).tobytes())
    frames = [head.data()]
    frames += [
        DeterministicScenario.voice_burst_spec(spec, seq=n, dtype_vseq=(n - 1) % 6).data() for n in range(1, bursts + 1)
    ]
    if terminator:
        frames.append(DeterministicScenario.voice_term_spec(spec, seq=bursts + 1).data())
    return frames


def unit_data(peer_id: bytes, dst: int, *, rf_src: int, stream_id: int, slot: int, blocks: int = 2) -> list[bytes]:
    """A data header and its rate 1/2 blocks (SMS, GPS) to a radio."""
    spec = PacketSpec(
        peer_id=int.from_bytes(peer_id, "big"), rf_src=rf_src, dst_id=dst, slot=slot, stream_id=stream_id,
        call_type="unit",
    )
    head = DeterministicScenario.unit_data_header_spec(spec)
    return [head.data()] + [
        replace(head, seq=n, dtype_vseq=7, payload=bytes([n]) * 33).data() for n in range(1, blocks + 1)
    ]


def header_lc(frame: bytes) -> bytes:
    return decode.voice_head_term(frame[20:53])["LC"]


def embedded_lc(frames: list[bytes]) -> bytes:
    """The LC bursts B-E carry in their embedded signalling."""
    by_vseq = {f[15] & 0x0F: f for f in frames if (f[15] & 0x30) >> 4 == HBPF_VOICE}
    bits = bitarray(endian="big")
    for vseq in (1, 2, 3, 4):
        bits += decode.voice(by_vseq[vseq][20:53])["EMBED"]
    return decode_emblc(bits)


def by_stream(frames: list[bytes]) -> dict[bytes, list[bytes]]:
    out: dict[bytes, list[bytes]] = {}
    for frame in frames:
        out.setdefault(frame[16:20], []).append(frame)
    return out


class TrunkSiteStack:
    """Real ``TrunkSiteProtocol`` and hotspot ``HBPProtocol`` MASTERs over one ``RoutingUseCases``."""

    def __init__(
        self,
        config: dict[str, Any],
        trunk: tuple[str, ...],
        masters: tuple[str, ...],
        obp: tuple[str, ...],
        *,
        path: str,
        echo: bool,
        tables: dict[int, tuple[tuple[str, int], ...]],
        user_passwords: dict[int, bytes],
    ) -> None:
        self.config = config
        self.clock = FakeClock()
        self.report_factory = FakeReportFactory()
        self.reporting = ReportingUseCases(FakeReportSender(self.report_factory), config)
        self.acl = InMemoryAclRouter()
        self.store = InMemorySubscriptionStore()
        routing_table: dict[str, list[dict[str, Any]]] = seed_echo_routing_table(config) if echo else {}
        for tg, entries in tables.items():
            routing_table.update(active_routing_table(tg, entries))
        if routing_table:
            replace_store_from_routing_table(self.store, routing_table)
        self.protocols: dict[str, object] = {}
        self.transports: dict[str, RecordingTransport] = {}
        self.captured: dict[str, list[bytes]] = {}
        self.routing = RoutingUseCases(
            self.acl,
            config,
            self.store,
            send_to_system=self._send_to_system,
            get_protocols=lambda: self.protocols,
            reporting=self.reporting,
            send_dmra_to_system=self._send_dmra_to_system,
            get_dmra_blocks=self._get_dmra_blocks,
            encode_emblc=encode_emblc,
            ta_emblc_encoder=default_ta_emblc_encoder,
        )
        # Slot rows start at the clock's time, as they start at the server's.
        with patch_routing_wall_time(self.clock):
            self.routing.apply_startup_subscriptions()
            self.sites = None
            if trunk:
                self.sites = build_trunk_sites(
                    config, self.protocols, self.store, self.acl, self.routing, self.reporting, path,
                    JsonTrunkRegistrationStore(maybeDeferred),
                )
            for name in trunk:
                self.sites.system_added(name, self.clock.time())
                self._add(name, TrunkSiteProtocol(
                    name, config, None, sites=self.sites, router=self.acl,
                    dmrd_received=self.routing.dmrd_received,
                    get_user_password_callback=user_passwords.get,
                    on_talker_alias_stream_end=self.routing.clear_talker_alias_stream,
                    routing_table_for_report=self.routing.routing_table_for_report,
                    get_subscription_store=lambda: self.store,
                ))
            for name in masters:
                self._add(name, HBPProtocol(
                    name, config, None, router=self.acl,
                    dmrd_received=self.routing.dmrd_received,
                    on_in_band_signalling=self.routing.apply_in_band_signalling,
                    on_options_received=self.routing.options_config_for_system,
                    on_deactivate_dynamic_relays=self.routing.deactivate_all_dynamic_relays,
                    on_release_dynamic_leg=self.routing.release_dynamic_leg,
                    on_talker_alias_repeat_prepare=self.routing.prepare_talker_alias_local_repeat,
                    on_talker_alias_repeat_burst=self.routing.rewrite_repeat_voice_burst,
                    on_talker_alias_stream_end=self.routing.clear_talker_alias_stream,
                    on_dmra_fragment_stored=self.routing.on_dmra_fragment_stored,
                    routing_table_for_report=self.routing.routing_table_for_report,
                    get_subscription_store=lambda: self.store,
                ))
            for name in obp:
                self.protocols[name] = FakeObpProtocol(name)
            if echo:
                self.protocols["ECHO"] = FakeHbpProtocol("ECHO")
        self.routing.set_concurrent_systems(trunk_systems(config["SYSTEMS"]))

    def _add(self, name: str, proto: HBPProtocol) -> None:
        proto.transport = self.transports[name] = RecordingTransport()  # type: ignore[assignment]
        self.protocols[name] = proto

    def _send_to_system(self, system: str, packet: bytes, **kwargs: Any) -> None:
        proto = self.protocols.get(system)
        if isinstance(proto, HBPProtocol):
            proto.send_system(packet, **kwargs)
        else:
            self.captured.setdefault(system, []).append(packet)

    def _send_dmra_to_system(
        self, system: str, packets: list[bytes], exclude_peer: bytes | None = None, *, slot=None, tgid=None,
    ) -> int:
        proto = self.protocols.get(system)
        if not isinstance(proto, HBPProtocol):
            return 0
        return proto.send_dmra_system(packets, exclude_peer=exclude_peer, slot=slot, tgid=tgid)

    def _get_dmra_blocks(self, system: str, stream_id: bytes) -> dict[int, bytes] | None:
        proto = self.protocols.get(system)
        return proto.get_dmra_blocks(stream_id) if isinstance(proto, HBPProtocol) else None

    def proto(self, system: str) -> Any:
        return self.protocols[system]

    def inject(self, system: str, data: bytes, addr: tuple[str, int]) -> None:
        with patch_routing_wall_time(self.clock):
            self.protocols[system].datagramReceived(data, addr)

    def play(self, *overs: tuple[Any, list[bytes]]) -> None:
        """Frames of several overs interleaved, each over at the radio's own 60 ms pace."""
        step = FRAME_S / len(overs)
        for frames in zip_longest(*([(sender, f) for f in frames] for sender, frames in overs)):
            for item in frames:
                if item is not None:
                    self.clock.advance(step)
                    item[0].send(item[1])

    def echo_playback(self, rf_src: int, stream_id: int, *, bursts: int = 3) -> None:
        """The parrot playing an over back as ``rf_src``, through the ECHO system."""
        st = self.protocols["ECHO"].STATUS[2]
        for frame in over(bytes_4(9990), 9990, rf_src=rf_src, stream_id=stream_id, slot=2, bursts=bursts):
            self.clock.advance(FRAME_S)
            bits = frame[15]
            with patch_routing_wall_time(self.clock):
                ok = self.routing.dmrd_received(
                    "ECHO", frame[11:15], frame[5:8], frame[8:11], frame[4], 2, "group",
                    (bits & 0x30) >> 4, bits & 0x0F, frame[16:20], frame, ingress_pkt_time=self.clock.time(),
                )
            if ok:
                st["RX_STREAM_ID"] = frame[16:20]

    def advance(self, seconds: float) -> None:
        self.clock.advance(seconds)

    def maintenance(self, system: str) -> None:
        with patch_routing_wall_time(self.clock):
            self.protocols[system]._master_maintenance_loop()

    def tick(self) -> None:
        with patch_routing_wall_time(self.clock):
            self.sites.tick(self.clock.time())

    def trim_streams(self) -> None:
        with patch_routing_wall_time(self.clock):
            self.routing.stream_trimmer_loop()

    def bridge_reset(self) -> None:
        with patch_routing_wall_time(self.clock):
            self.routing.subscription_reset_loop()

    def events(self, prefix: str) -> list[list[str]]:
        return [e.split(",") for e in self.report_factory.events if e.startswith(prefix)]

    def site(self, system: str, peer_id: int, addr: tuple[str, int], **kwargs: Any) -> DmrtcSite:
        return DmrtcSite(self, system, peer_id, addr, **kwargs).login()

    def hotspot(self, system: str, peer_id: int, addr: tuple[str, int], *, options: bytes = b"") -> Hotspot:
        return Hotspot(self, system, peer_id, addr).login(options)


class _Peer:
    def __init__(self, stack: TrunkSiteStack, system: str, peer_id: int, addr: tuple[str, int]) -> None:
        self.stack = stack
        self.system = system
        self.peer_id = bytes_4(peer_id)
        self.addr = addr

    def send(self, data: bytes) -> list[bytes]:
        """``data`` to the server; what it wrote back to this address."""
        sent = self.stack.transports[self.system].sent
        n = len(sent)
        self.stack.inject(self.system, data, self.addr)
        return [pkt for pkt, addr in sent[n:] if addr == self.addr]

    def written(self) -> list[bytes]:
        return self.stack.transports[self.system].for_addr(self.addr)

    def dmrd(self) -> list[bytes]:
        return [pkt for pkt in self.written() if pkt[:4] == b"DMRD"]

    def call(self, frames: list[bytes]) -> list[bytes]:
        self.stack.play((self, frames))
        return frames


class DmrtcSite(_Peer):
    """A DMRGateway (TrunkingEnabled=1) with its dmrtc behind it."""

    def __init__(
        self,
        stack: TrunkSiteStack,
        system: str,
        peer_id: int,
        addr: tuple[str, int],
        *,
        password: bytes = PASS,
        callsign: bytes = b"CE1TEST",
        options: bytes = b"",
    ) -> None:
        super().__init__(stack, system, peer_id, addr)
        self.gateway = GatewayLink(self.peer_id, password, callsign=callsign, options=options)

    def login(self) -> DmrtcSite:
        out = self.gateway.login()
        while out is not None:
            replies = self.send(out)
            out = None
            for reply in replies:
                out = self.gateway.received(reply) or out
        assert self.gateway.status == "RUNNING" and self.gateway.master_login, self.gateway.status
        return self

    def dmrt(self) -> list[bytes]:
        return [pkt for pkt in self.written() if pkt[:4] == b"DMRT"]

    def _reply(self, payload: bytes, op: int) -> bytes:
        return next(r for r in self.send(payload) if r[:5] == b"DMRT" + bytes((op,)))

    def register(self, *ms: int) -> list[tuple[int, bool]]:
        payload = dmrt_registration(0x01, ms, int(self.stack.clock.time()))
        return read_registration_confirmation(self._reply(payload, 0x41))[1]

    def deregister(self, *ms: int) -> list[int]:
        payload = dmrt_registration(0x02, ms, int(self.stack.clock.time()))
        return read_deregistration(self._reply(payload, 0x42))

    def subscribe(self, *tgs: int) -> list[int]:
        return read_subscription_confirmation(self._reply(dmrt_subscription(0x03, tgs), 0x43))[1]

    def unsubscribe(self, *tgs: int) -> list[int]:
        return read_subscription_confirmation(self._reply(dmrt_subscription(0x04, tgs), 0x44))[1]

    def over(self, dst: int, *, rf_src: int, stream_id: int, uuid: bytes, slot: int = 1, **kwargs: Any) -> list[bytes]:
        """71 bytes per frame; dmrtc sets the slot bit as it likes."""
        frames = over(self.peer_id, dst, rf_src=rf_src, stream_id=stream_id, slot=slot, **kwargs)
        return [site_dmrd(frame, uuid) for frame in frames]

    def unit_data(self, dst: int, *, rf_src: int, stream_id: int, uuid: bytes, slot: int = 1) -> list[bytes]:
        return [site_dmrd(f, uuid) for f in unit_data(self.peer_id, dst, rf_src=rf_src, stream_id=stream_id, slot=slot)]


class Hotspot(_Peer):
    """An MMDVM hotspot logged in with classic HBP."""

    def login(self, options: bytes) -> Hotspot:
        salt = self.send(RPTL + self.peer_id)[-1]
        assert salt[:6] == RPTACK
        self.send(RPTK + self.peer_id + _calc_hash(salt[6:10], PASS))
        self.send(RPTC + self.peer_id + b"CE2HOT  ".ljust(294, b"\x00"))
        if options:
            self.send(RPTO + self.peer_id + options)
        assert self.stack.proto(self.system)._peers[self.peer_id]["CONNECTION"] == "YES"
        return self

    def over(self, dst: int, *, rf_src: int, stream_id: int, slot: int = 2, **kwargs: Any) -> list[bytes]:
        return over(self.peer_id, dst, rf_src=rf_src, stream_id=stream_id, slot=slot, **kwargs)


def build_trunk_site_stack(
    trunk: tuple[str, ...] = ("TRUNK",),
    masters: tuple[str, ...] = ("MASTER-A",),
    obp: tuple[str, ...] = (),
    config_overrides: dict[str, dict[str, Any]] | None = None,
    *,
    path: str = "",
    echo: bool = False,
    tables: dict[int, tuple[tuple[str, int], ...]] | None = None,
    user_passwords: dict[int, bytes] | None = None,
) -> TrunkSiteStack:
    """``config_overrides`` keys are system names or top-level config keys; their dicts are merged in."""
    config = minimal_config(trunk + masters)
    config["GLOBAL"]["REG_ACL"] = acl_permit_all(PEER_MAX)
    config["REPORTS"]["REPORT"] = True
    config["_SUB_MAP"] = {}
    for name in trunk:
        config["SYSTEMS"][name].update(TRUNKING=True, SINGLE_MODE=False, MAX_PEERS=4, DEFAULT_UA_TIMER=10)
    for name in trunk + masters:
        config["SYSTEMS"][name]["PASSPHRASE"] = PASS
    for name in obp:
        add_openbridge_system(config, name)
    if echo:
        config["SYSTEMS"]["ECHO"] = {"MODE": "PEER", "ENABLED": True, "USE_ACL": False, "SUB_MAP_LEARN": False}
    for key, values in (config_overrides or {}).items():
        target = config["SYSTEMS"].get(key, config.get(key))
        if isinstance(target, dict):
            target.update(values)
        else:
            config[key] = values
    ensure_system_runtime_config(config)
    return TrunkSiteStack(
        config, trunk, masters, obp, path=path, echo=echo, tables=tables or {}, user_passwords=user_passwords or {},
    )
