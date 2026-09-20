# ADN DMR Peer Server - infrastructure proxy obp runtime
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

"""Wire integrated OBP proxy at startup (composition root wiring)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from twisted.internet import reactor

from adn_server.application.proxy.deployment import obp_bridge_legacy_listen_port, obp_proxy_enabled
from adn_server.infrastructure.proxy.obp_config import obp_proxy_settings
from adn_server.infrastructure.proxy.obp_fanin import (
    InProcessObpSink,
    ObpBridgeEntry,
    ObpBridgeRegistry,
    ObpFanInDemux,
    ObpIngressReplyTransport,
    listen_obp_fanin,
    peer_sock_from_config,
)


def build_obp_bridge_registry(
    config: dict[str, Any],
    protocols: dict[str, Any],
    *,
    bind_legacy_ports: bool,
    listen_port: int,
    primary_transport: Any,
) -> ObpBridgeRegistry:
    """Register enabled OPENBRIDGE systems for fan-in demux."""
    registry = ObpBridgeRegistry()
    systems = config.get("SYSTEMS", {})
    if not isinstance(systems, dict):
        return registry
    for name, sys_cfg in systems.items():
        if not isinstance(sys_cfg, dict):
            continue
        if not sys_cfg.get("ENABLED", True):
            continue
        if sys_cfg.get("MODE") != "OPENBRIDGE":
            continue
        proto = protocols.get(name)
        if proto is None:
            continue
        network_id = sys_cfg.get("NETWORK_ID")
        if not isinstance(network_id, bytes) or len(network_id) != 4:
            continue
        legacy_port = obp_bridge_legacy_listen_port(
            sys_cfg,
            listen_port=listen_port,
            bind_legacy_ports=bind_legacy_ports,
        )
        reply = ObpIngressReplyTransport(primary_transport)
        passphrase = sys_cfg.get("PASSPHRASE") or b""
        if isinstance(passphrase, str):
            passphrase = (passphrase.strip().encode("utf-8") + b"\x00" * 20)[:20]
        entry = ObpBridgeEntry(
            system_name=name,
            network_id=network_id,
            passphrase=passphrase,
            sink=InProcessObpSink(proto),
            reply_transport=reply,
            legacy_port=legacy_port if legacy_port and legacy_port > 0 else None,
            sys_cfg=sys_cfg,
            peer_hint=peer_sock_from_config(sys_cfg),
        )
        registry.register(entry)
        proto.transport = reply  # type: ignore[assignment]
        start = getattr(proto, "startProtocol", None)
        if callable(start):
            start()
    return registry


@dataclass
class ObpProxyServiceState:
    """Live OBP proxy handles (for shutdown / reload)."""

    demux: ObpFanInDemux
    registry: ObpBridgeRegistry
    udp_ports: list[Any] = field(default_factory=list)
    # Bound legacy port -> its own transport, so egress can be re-pinned on reload.
    legacy_transports: dict[int, Any] = field(default_factory=dict)
    listen_port: int = 62032
    listen_ip: str = ""
    bind_legacy_ports: bool = True
    _runtime: dict[str, Any] = field(default_factory=dict)

    def stop(self) -> list[Any]:
        """Stop all UDP listeners. Returns Deferred list when ports were bound."""
        deferreds: list[Any] = []
        for port in self.udp_ports:
            if port is not None:
                deferreds.append(port.stopListening())
        self.udp_ports.clear()
        self.legacy_transports.clear()
        return deferreds


def pin_legacy_egress(state: ObpProxyServiceState) -> None:
    """Pin each bridge's egress to its own bound socket.

    Egress leaves through the bridge's own socket, so remote peers keep seeing the
    port they are configured against instead of LISTEN_PORT. Must run again after
    every registry rebuild (SIGHUP), because a rebuild creates fresh reply
    transports that would otherwise fall back to the shared fan-in socket.
    """
    for entry in state.registry.bridges.values():
        if entry.legacy_port is None:
            continue
        transport = state.legacy_transports.get(entry.legacy_port)
        if transport is not None:
            entry.reply_transport.pin(transport)


def _start_listeners(
    state: ObpProxyServiceState,
    config: dict[str, Any],
    protocols: dict[str, Any],
    runtime: dict[str, Any],
    *,
    logger: logging.Logger,
) -> None:
    primary_proto, primary_port = listen_obp_fanin(
        reactor,
        runtime["listen_ip"],
        runtime["listen_port"],
        state.demux,
        config=config,
        logger=logger,
    )
    state.udp_ports.append(primary_port)
    primary_transport = primary_proto.transport
    if primary_transport is None:
        raise RuntimeError("(OBP_PROXY) fan-in transport missing after bind")

    state.registry = build_obp_bridge_registry(
        config,
        protocols,
        bind_legacy_ports=runtime["bind_legacy_ports"],
        listen_port=runtime["listen_port"],
        primary_transport=primary_transport,
    )
    state.demux._registry = state.registry  # noqa: SLF001

    if runtime["bind_legacy_ports"]:
        seen_ports: set[int] = {runtime["listen_port"]}
        for entry in state.registry.bridges.values():
            if entry.legacy_port is None or entry.legacy_port in seen_ports:
                continue
            seen_ports.add(entry.legacy_port)
            sys_cfg = config.get("SYSTEMS", {}).get(entry.system_name, {})
            bind_ip = str(sys_cfg.get("_REPORT_BIND_IP") or runtime["listen_ip"] or "")
            legacy_proto, legacy_port = listen_obp_fanin(
                reactor,
                bind_ip,
                entry.legacy_port,
                state.demux,
                config=config,
                logger=logger,
            )
            state.udp_ports.append(legacy_port)
            if legacy_proto.transport is not None:
                state.legacy_transports[entry.legacy_port] = legacy_proto.transport
            logger.info(
                "(OBP_PROXY) Legacy port %s:%s -> %s",
                bind_ip or "*",
                entry.legacy_port,
                entry.system_name,
            )
        pin_legacy_egress(state)


def start_obp_proxy_service(
    config: dict[str, Any],
    protocols: dict[str, Any],
    *,
    logger: logging.Logger,
) -> ObpProxyServiceState:
    """Start OBP fan-in and wire inject-only OPENBRIDGE instances."""
    if not obp_proxy_enabled(config):
        raise RuntimeError("(OBP_PROXY) start_obp_proxy_service called but OBP_PROXY is disabled")

    runtime = obp_proxy_settings(config)
    registry = ObpBridgeRegistry()
    demux = ObpFanInDemux(registry, debug=runtime["debug"], logger=logger)
    state = ObpProxyServiceState(
        demux=demux,
        registry=registry,
        listen_port=runtime["listen_port"],
        listen_ip=runtime["listen_ip"],
        bind_legacy_ports=runtime["bind_legacy_ports"],
        _runtime=dict(runtime),
    )
    _start_listeners(state, config, protocols, runtime, logger=logger)

    bridge_count = len(state.registry.bridges)
    logger.info(
        "(OBP_PROXY) Fan-in on %s:%s (%s bridge(s), BIND_LEGACY_PORTS=%s)",
        runtime["listen_ip"] or "*",
        runtime["listen_port"],
        bridge_count,
        runtime["bind_legacy_ports"],
    )
    if bridge_count == 0:
        logger.warning("(OBP_PROXY) No enabled OPENBRIDGE systems registered")
    return state


def apply_obp_proxy_config_reload(
    state: ObpProxyServiceState,
    config: dict[str, Any],
    protocols: dict[str, Any],
    *,
    logger: logging.Logger,
) -> None:
    """Hot-apply OBP_PROXY settings on SIGHUP without rebinding listeners."""
    incoming = obp_proxy_settings(config)
    bind_changed = (
        state.listen_port != incoming["listen_port"]
        or state.listen_ip != incoming["listen_ip"]
        or state.bind_legacy_ports != incoming["bind_legacy_ports"]
    )
    state._runtime.update(incoming)
    state.demux.debug = bool(incoming["debug"])
    if state.udp_ports:
        primary_proto = getattr(state.udp_ports[0], "protocol", None)
        transport = getattr(primary_proto, "transport", None) if primary_proto is not None else None
        if transport is not None:
            # Listeners are not rebound here, so the registry must describe the ports
            # that are actually bound, not the ones the new config asks for.
            state.registry = build_obp_bridge_registry(
                config,
                protocols,
                bind_legacy_ports=state.bind_legacy_ports,
                listen_port=state.listen_port,
                primary_transport=transport,
            )
            state.demux._registry = state.registry  # noqa: SLF001
            pin_legacy_egress(state)
    if bind_changed:
        logger.warning(
            "(CONFIG-RELOAD) OBP_PROXY bind change ignored at runtime "
            "(still listening on %s:%s BIND_LEGACY_PORTS=%s); restart adn-server to apply "
            "%s:%s BIND_LEGACY_PORTS=%s",
            state.listen_ip or "*",
            state.listen_port,
            state.bind_legacy_ports,
            incoming["listen_ip"] or "*",
            incoming["listen_port"],
            incoming["bind_legacy_ports"],
        )
    logger.debug(
        "(CONFIG-RELOAD) OBP proxy settings applied (%s bridge(s))",
        len(state.registry.bridges),
    )


__all__ = [
    "ObpProxyServiceState",
    "apply_obp_proxy_config_reload",
    "build_obp_bridge_registry",
    "pin_legacy_egress",
    "start_obp_proxy_service",
]
