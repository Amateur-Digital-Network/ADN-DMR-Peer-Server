# ADN DMR Peer Server - tests infrastructure obp proxy
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

"""OBP_PROXY configuration and fan-in demux tests."""

from __future__ import annotations

import pytest
from tests.conftest import minimal_valid_config

from adn_server.application.proxy.deployment import (
    config_has_enabled_openbridge,
    is_obp_proxy_managed,
    normalize_obp_proxy_targets,
    obp_bridge_legacy_listen_port,
    obp_proxy_bind_legacy_ports,
    obp_proxy_enabled,
)
from adn_server.domain import bytes_4
from adn_server.domain.errors import ConfigError
from adn_server.infrastructure.config_normalizer import normalize_obp_config
from adn_server.infrastructure.config_validator import validate_config
from adn_server.infrastructure.hbp_constants import DMRD
from adn_server.infrastructure.mesh.obp_v1 import build_bcka, build_dmrd_v1
from adn_server.infrastructure.proxy.obp_config import obp_proxy_settings
from adn_server.infrastructure.proxy.obp_fanin import (
    InProcessObpSink,
    ObpBridgeEntry,
    ObpBridgeRegistry,
    ObpFanInDemux,
    ObpIngressReplyTransport,
)
from adn_server.infrastructure.proxy.obp_runtime import (
    apply_obp_proxy_config_reload,
    build_obp_bridge_registry,
    start_obp_proxy_service,
)

_PASS = b"test-passphrase\x00\x00\x00\x00\x00\x00"
_NETWORK = bytes_4(73044)
_ADDR = ("10.0.0.9", 62044)


class _RecordingTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.port = 62032

    def write(self, data: bytes, addr: tuple[str, int]) -> None:
        self.sent.append((data, addr))

    def getHost(self) -> _RecordingTransport:
        return self


class _RecordingObp:
    def __init__(self) -> None:
        self.packets: list[tuple[bytes, tuple[str, int]]] = []
        self.transport: object | None = None

    def _obp_datagram_received(self, data: bytes, sockaddr: tuple[str, int]) -> None:
        self.packets.append((data, sockaddr))


class _FakeSocket:
    def setsockopt(self, *_args: object) -> None:
        return None

    def getsockopt(self, *_args: object) -> int:
        return 0


class _FakeUdpPort:
    def __init__(self, port: int, protocol: object) -> None:
        self.protocol = protocol
        self.socket = _FakeSocket()
        self.port = port

    def getHost(self) -> _FakeUdpPort:
        return self

    def stopListening(self) -> None:
        return None


class _FakeReactor:
    """listenUDP that hands each listener its own recording transport."""

    def __init__(self) -> None:
        self.transports: dict[int, _RecordingTransport] = {}

    def listenUDP(self, port: int, protocol: object, interface: str = "") -> _FakeUdpPort:
        transport = _RecordingTransport()
        transport.port = port
        protocol.transport = transport  # type: ignore[attr-defined]
        self.transports[port] = transport
        return _FakeUdpPort(port, protocol)


def _sample_dmr_voice(stream_id: int = 0xAABBCCDD) -> bytes:
    return b"".join(
        [
            DMRD,
            bytes([1]),
            bytes_4(1001)[1:4],
            bytes_4(52090)[1:4],
            bytes_4(1),
            bytes([0x10]),
            bytes_4(stream_id),
            b"\x00" * 33,
        ]
    )


def _obp_config(*, bind_legacy: bool = True) -> dict:
    return {
        "GLOBAL": {"SERVER_ID": 73010},
        "DATABASE": {
            "DB_SERVER": "localhost",
            "DB_USERNAME": "hbmon",
            "DB_PASSWORD": "secret",
            "DB_NAME": "hbmon",
            "DB_PORT": 3306,
        },
        "PROXY": {"LISTEN_PORT": 62031, "TARGET_SYSTEM": "HOTSPOT"},
        "OBP_PROXY": {
            "ENABLED": True,
            "LISTEN_PORT": 62032,
            "BIND_LEGACY_PORTS": bind_legacy,
        },
        "SYSTEMS": {
            "HOTSPOT": {"MODE": "MASTER", "ENABLED": True, "MAX_PEERS": 1},
            "OBP-CL": {
                "MODE": "OPENBRIDGE",
                "ENABLED": True,
                "PORT": 62044,
                "NETWORK_ID": 73044,
                "PASSPHRASE": "test-passphrase",
                "TARGET_IP": "127.0.0.1",
                "TARGET_PORT": 62030,
            },
        },
    }


def test_obp_proxy_disabled_when_block_absent_and_no_openbridge() -> None:
    config = minimal_valid_config()
    assert not config_has_enabled_openbridge(config)
    assert not obp_proxy_enabled(config)


def test_obp_proxy_defaults_when_block_absent_with_openbridge() -> None:
    config = _obp_config()
    del config["OBP_PROXY"]
    assert obp_proxy_enabled(config)
    assert obp_proxy_bind_legacy_ports(config)
    settings = obp_proxy_settings(config)
    assert settings == {
        "enabled": True,
        "listen_port": 62032,
        "listen_ip": "",
        "bind_legacy_ports": True,
        "debug": False,
    }
    normalize_obp_proxy_targets(config)
    assert "PORT" not in config["SYSTEMS"]["OBP-CL"]
    assert config["SYSTEMS"]["OBP-CL"]["_REPORT_PORT"] == 62044


def test_obp_proxy_explicit_disable_with_openbridge() -> None:
    config = _obp_config()
    config["OBP_PROXY"] = {"ENABLED": False}
    assert not obp_proxy_enabled(config)
    assert not is_obp_proxy_managed(config, "OBP-CL")


def test_validate_obp_proxy_duplicate_legacy_port() -> None:
    config = _obp_config()
    config["SYSTEMS"]["OBP-EU"] = {
        **config["SYSTEMS"]["OBP-CL"],
        "NETWORK_ID": 73045,
    }
    with pytest.raises(ConfigError) as exc:
        validate_config(config)
    assert "duplicate" in str(exc.value).lower()


def test_validate_obp_per_bridge_fanin_migration() -> None:
    """7301-style: BIND_LEGACY_PORTS true; one bridge on fan-in, another on legacy."""
    config = _obp_config(bind_legacy=True)
    config["SYSTEMS"]["OBP-CL"]["PORT"] = 62032
    config["SYSTEMS"]["OBP-EU"] = {
        **config["SYSTEMS"]["OBP-CL"],
        "PORT": 62999,
        "NETWORK_ID": 73045,
    }
    validate_config(config)


def test_validate_obp_fanin_only_port_equals_listen_with_remote_target() -> None:
    """7302-style: no legacy bind; PORT=62032 is metadata; TARGET_PORT is remote fan-in."""
    config = _obp_config(bind_legacy=False)
    config["OBP_PROXY"]["BIND_LEGACY_PORTS"] = False
    config["SYSTEMS"]["OBP-CL"]["PORT"] = 62032
    config["SYSTEMS"]["OBP-CL"]["TARGET_IP"] = "44.31.61.66"
    config["SYSTEMS"]["OBP-CL"]["TARGET_PORT"] = 62032
    validate_config(config)


def test_validate_obp_legacy_local_port_with_remote_fanin_target() -> None:
    """7301-style: legacy 62999 locally; mesh to peer fan-in on 62032."""
    config = _obp_config(bind_legacy=True)
    config["SYSTEMS"]["OBP-CL"]["PORT"] = 62999
    config["SYSTEMS"]["OBP-CL"]["TARGET_IP"] = "44.31.61.68"
    config["SYSTEMS"]["OBP-CL"]["TARGET_PORT"] = 62032
    validate_config(config)


def test_obp_bridge_legacy_listen_port_per_bridge_migration() -> None:
    migrated = {"_REPORT_PORT": 62032}
    legacy = {"_REPORT_PORT": 62999}
    assert obp_bridge_legacy_listen_port(migrated, listen_port=62032, bind_legacy_ports=True) is None
    assert obp_bridge_legacy_listen_port(legacy, listen_port=62032, bind_legacy_ports=True) == 62999


def test_obp_proxy_enabled_defaults() -> None:
    config = _obp_config()
    assert obp_proxy_enabled(config)
    assert obp_proxy_bind_legacy_ports(config)


def test_normalize_obp_proxy_targets_omitted_port_uses_fanin() -> None:
    config = _obp_config(bind_legacy=True)
    del config["SYSTEMS"]["OBP-CL"]["PORT"]
    normalize_obp_proxy_targets(config)
    assert config["SYSTEMS"]["OBP-CL"]["_REPORT_PORT"] == 62032
    assert obp_bridge_legacy_listen_port(
        config["SYSTEMS"]["OBP-CL"],
        listen_port=62032,
        bind_legacy_ports=True,
    ) is None


def test_is_obp_proxy_managed_only_for_openbridge() -> None:
    config = _obp_config()
    assert is_obp_proxy_managed(config, "OBP-CL")
    assert not is_obp_proxy_managed(config, "HOTSPOT")


def test_normalize_obp_proxy_targets_strips_bind_fields() -> None:
    config = _obp_config()
    normalize_obp_proxy_targets(config)
    obp = config["SYSTEMS"]["OBP-CL"]
    assert "PORT" not in obp
    assert obp["_REPORT_PORT"] == 62044


def test_validate_obp_proxy_duplicate_network_id() -> None:
    config = _obp_config()
    config["SYSTEMS"]["OBP-EU"] = {
        **config["SYSTEMS"]["OBP-CL"],
        "PORT": 62045,
    }
    with pytest.raises(ConfigError) as exc:
        validate_config(config)
    assert "NETWORK_ID" in str(exc.value)


def test_validate_obp_proxy_migrated_bridge_port_matches_listen() -> None:
    config = _obp_config()
    config["OBP_PROXY"]["LISTEN_PORT"] = 62044
    config["SYSTEMS"]["OBP-CL"]["PORT"] = 62044
    validate_config(config)


def test_obp_proxy_settings_resolved() -> None:
    settings = obp_proxy_settings(_obp_config())
    assert settings["listen_port"] == 62032
    assert settings["bind_legacy_ports"] is True


def test_obp_proxy_settings_default_listen_port() -> None:
    config = _obp_config()
    del config["OBP_PROXY"]["LISTEN_PORT"]
    settings = obp_proxy_settings(config)
    assert settings["listen_port"] == 62032
    assert settings["enabled"] is True


def test_obp_fanin_demux_by_network_id() -> None:
    receiver = _RecordingObp()
    transport = _RecordingTransport()
    reply = ObpIngressReplyTransport(transport)
    registry = ObpBridgeRegistry()
    registry.register(
        ObpBridgeEntry(
            system_name="OBP-CL",
            network_id=_NETWORK,
            passphrase=_PASS,
            sink=InProcessObpSink(receiver),
            reply_transport=reply,
        )
    )
    demux = ObpFanInDemux(registry)
    wire = build_dmrd_v1(_sample_dmr_voice(), _NETWORK, _PASS)
    demux.deliver(wire, _ADDR, local_port=62032, transport=transport)
    assert len(receiver.packets) == 1
    assert receiver.packets[0][0] == wire


def test_obp_fanin_demux_legacy_port_routes_without_network_id() -> None:
    receiver = _RecordingObp()
    transport = _RecordingTransport()
    transport.port = 62044
    reply = ObpIngressReplyTransport(transport)
    registry = ObpBridgeRegistry()
    registry.register(
        ObpBridgeEntry(
            system_name="OBP-CL",
            network_id=_NETWORK,
            passphrase=_PASS,
            sink=InProcessObpSink(receiver),
            reply_transport=reply,
            legacy_port=62044,
        )
    )
    demux = ObpFanInDemux(registry)
    wire = build_bcka(_PASS)
    demux.deliver(wire, _ADDR, local_port=62044, transport=transport)
    assert len(receiver.packets) == 1


def test_obp_fanin_demux_control_on_listen_port() -> None:
    receiver = _RecordingObp()
    transport = _RecordingTransport()
    reply = ObpIngressReplyTransport(transport)
    registry = ObpBridgeRegistry()
    registry.register(
        ObpBridgeEntry(
            system_name="OBP-CL",
            network_id=_NETWORK,
            passphrase=_PASS,
            sink=InProcessObpSink(receiver),
            reply_transport=reply,
        )
    )
    demux = ObpFanInDemux(registry)
    wire = build_bcka(_PASS)
    demux.deliver(wire, _ADDR, local_port=62032, transport=transport)
    assert len(receiver.packets) == 1


def test_build_obp_bridge_registry_starts_inject_protocol() -> None:
    class _InjectProto:
        def __init__(self) -> None:
            self.transport = None
            self.started = False

        def startProtocol(self) -> None:
            self.started = True

    proto = _InjectProto()
    config = {
        "SYSTEMS": {
            "OBP-CL": {
                "MODE": "OPENBRIDGE",
                "ENABLED": True,
                "NETWORK_ID": _NETWORK,
                "PASSPHRASE": _PASS,
            }
        }
    }
    build_obp_bridge_registry(
        config,
        {"OBP-CL": proto},
        bind_legacy_ports=False,
        listen_port=62032,
        primary_transport=_RecordingTransport(),
    )
    assert proto.started is True
    assert proto.transport is not None


def _shared_passphrase_registry() -> tuple[ObpBridgeRegistry, dict[str, _RecordingObp]]:
    """Two bridges, one passphrase (ADN Systems mesh), different peers."""
    registry = ObpBridgeRegistry()
    protos: dict[str, _RecordingObp] = {}
    for name, network, peer in (
        ("OBP-FR", 20840, ("82.65.127.86", 62201)),
        ("OBP-PT", 26811, ("85.241.222.7", 62268)),
    ):
        proto = _RecordingObp()
        protos[name] = proto
        registry.register(
            ObpBridgeEntry(
                system_name=name,
                network_id=bytes_4(network),
                passphrase=_PASS,
                sink=InProcessObpSink(proto),
                reply_transport=ObpIngressReplyTransport(_RecordingTransport()),
                sys_cfg={"TARGET_SOCK": peer, "TARGET_IP": peer[0], "TARGET_PORT": peer[1]},
            )
        )
    return registry, protos


def test_control_frame_goes_to_the_bridge_that_sent_it() -> None:
    """BCKA carries no NETWORK_ID: with a shared passphrase, use the peer address."""
    registry, protos = _shared_passphrase_registry()
    demux = ObpFanInDemux(registry)
    transport = _RecordingTransport()
    peer = ("85.241.222.7", 62268)
    demux.deliver(build_bcka(_PASS), peer, local_port=62032, transport=transport)
    assert protos["OBP-PT"].packets, "the keepalive was attributed to the wrong bridge"
    assert not protos["OBP-FR"].packets


def test_control_frame_matches_peer_answering_from_another_port() -> None:
    """A peer whose own fan-in answers from LISTEN_PORT still resolves by IP."""
    registry, protos = _shared_passphrase_registry()
    demux = ObpFanInDemux(registry)
    demux.deliver(
        build_bcka(_PASS),
        ("85.241.222.7", 62032),
        local_port=62032,
        transport=_RecordingTransport(),
    )
    assert protos["OBP-PT"].packets
    assert not protos["OBP-FR"].packets


def test_control_frame_unknown_peer_falls_back_to_passphrase() -> None:
    """No address evidence: the shared passphrase can only pick the first bridge."""
    registry, protos = _shared_passphrase_registry()
    demux = ObpFanInDemux(registry)
    demux.deliver(
        build_bcka(_PASS),
        ("203.0.113.9", 62044),
        local_port=62032,
        transport=_RecordingTransport(),
    )
    assert protos["OBP-FR"].packets
    assert not protos["OBP-PT"].packets


def test_reply_transport_prefers_pinned_socket() -> None:
    fanin = _RecordingTransport()
    legacy = _RecordingTransport()
    legacy.port = 62268
    ingress = _RecordingTransport()
    reply = ObpIngressReplyTransport(fanin)
    reply.pin(legacy)
    reply.note_ingress(ingress)
    reply.write(b"ping", _ADDR)
    assert legacy.sent and not fanin.sent and not ingress.sent


def test_yaml_loader_keeps_obp_proxy_block(tmp_path) -> None:
    """OBP_PROXY was dropped while normalizing top-level keys, so ENABLED/LISTEN_PORT
    in adn-server.yaml were silently ignored."""
    import yaml as _yaml

    from adn_server.infrastructure.config_loader import YamlConfigLoader

    raw = minimal_valid_config()
    raw["SYSTEMS"]["OBP-CL"] = {
        "MODE": "OPENBRIDGE",
        "ENABLED": True,
        "PORT": 62044,
        "NETWORK_ID": 73044,
        "PASSPHRASE": "test-passphrase",
        "TARGET_IP": "127.0.0.1",
        "TARGET_PORT": 62030,
    }
    raw["OBP_PROXY"] = {"ENABLED": False, "LISTEN_PORT": 62099}
    path = tmp_path / "adn-server.yaml"
    path.write_text(_yaml.safe_dump(raw), encoding="utf-8")
    config = YamlConfigLoader().load(str(path))
    assert config.get("OBP_PROXY") == {"ENABLED": False, "LISTEN_PORT": 62099}
    assert not obp_proxy_enabled(config)


def _obp_mesh_config() -> dict:
    """Two bridges on their own legacy ports, one shared passphrase."""
    config = _obp_config()
    del config["SYSTEMS"]["OBP-CL"]
    for name, network, port, peer in (
        ("OBP-FR", 20840, 62201, ("82.65.127.86", 62201)),
        ("OBP-PT", 26811, 62268, ("85.241.222.7", 62268)),
    ):
        config["SYSTEMS"][name] = {
            "MODE": "OPENBRIDGE",
            "ENABLED": True,
            "PORT": port,
            "NETWORK_ID": network,
            "PASSPHRASE": "test-passphrase",
            "TARGET_IP": peer[0],
            "TARGET_PORT": peer[1],
        }
    normalize_obp_config(config)
    normalize_obp_proxy_targets(config)
    return config


def test_reload_keeps_egress_pinned_to_the_bridge_socket(monkeypatch) -> None:
    """SIGHUP rebuilds the registry: the new reply transports must be re-pinned,
    or egress silently goes back out of the shared fan-in port."""
    import logging

    from adn_server.infrastructure.proxy import obp_runtime

    fake = _FakeReactor()
    monkeypatch.setattr(obp_runtime, "reactor", fake)
    config = _obp_mesh_config()
    protocols = {name: _RecordingObp() for name in ("OBP-FR", "OBP-PT")}
    logger = logging.getLogger("test-obp-proxy")

    state = start_obp_proxy_service(config, protocols, logger=logger)
    state.registry.bridges["OBP-FR"].reply_transport.write(b"ping", ("82.65.127.86", 62201))
    assert len(fake.transports[62201].sent) == 1

    for _ in range(2):
        apply_obp_proxy_config_reload(state, config, protocols, logger=logger)

    state.registry.bridges["OBP-FR"].reply_transport.write(b"ping", ("82.65.127.86", 62201))
    state.registry.bridges["OBP-PT"].reply_transport.write(b"ping", ("85.241.222.7", 62268))
    assert len(fake.transports[62201].sent) == 2
    assert len(fake.transports[62268].sent) == 1
    assert fake.transports[62032].sent == []


def test_control_frame_prefers_configured_peer_over_relaxed_target() -> None:
    """RELAX_CHECKS rewrites TARGET_SOCK in place; a bridge dragged onto another
    bridge's address must not start stealing that peer's control frames."""
    config = _obp_mesh_config()
    protocols = {name: _RecordingObp() for name in ("OBP-FR", "OBP-PT")}
    registry = build_obp_bridge_registry(
        config,
        protocols,
        bind_legacy_ports=True,
        listen_port=62032,
        primary_transport=_RecordingTransport(),
    )
    peer_pt = ("85.241.222.7", 62268)
    # What _obp_sync_target_sock_from_peer() does after a misattributed frame.
    poisoned = config["SYSTEMS"]["OBP-FR"]
    poisoned["TARGET_IP"], poisoned["TARGET_PORT"] = peer_pt
    poisoned["TARGET_SOCK"] = peer_pt

    demux = ObpFanInDemux(registry)
    demux.deliver(build_bcka(_PASS), peer_pt, local_port=62032, transport=_RecordingTransport())
    assert protocols["OBP-PT"].packets
    assert not protocols["OBP-FR"].packets


def test_debug_rx_log_once_per_stream(caplog) -> None:
    """A call sends many DMRD/DMRE packets; the RX debug line must fire once per
    stream_id, not once per packet, or an active call floods the log."""
    import logging as _logging

    receiver = _RecordingObp()
    transport = _RecordingTransport()
    registry = ObpBridgeRegistry()
    registry.register(
        ObpBridgeEntry(
            system_name="OBP-CL",
            network_id=_NETWORK,
            passphrase=_PASS,
            sink=InProcessObpSink(receiver),
            reply_transport=ObpIngressReplyTransport(transport),
        )
    )
    demux = ObpFanInDemux(registry, debug=True)
    caplog.set_level(_logging.DEBUG)

    wire_a = build_dmrd_v1(_sample_dmr_voice(stream_id=0x11111111), _NETWORK, _PASS)
    for _ in range(5):
        demux.deliver(wire_a, _ADDR, local_port=62032, transport=transport)
    assert sum("RX" in r.getMessage() for r in caplog.records) == 1

    wire_b = build_dmrd_v1(_sample_dmr_voice(stream_id=0x22222222), _NETWORK, _PASS)
    demux.deliver(wire_b, _ADDR, local_port=62032, transport=transport)
    assert sum("RX" in r.getMessage() for r in caplog.records) == 2
