# ADN DMR Peer Server - application proxy deployment
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

"""Proxy deployment policy from config dict (no I/O; used at startup/reload)."""

from __future__ import annotations

from typing import Any


def proxy_target_system(config: dict[str, Any]) -> str | None:
    proxy = config.get("PROXY", {})
    target = proxy.get("TARGET_SYSTEM")
    return str(target) if target else None


def config_has_enabled_master(config: dict[str, Any]) -> bool:
    """True when config defines at least one enabled MASTER (adn-server, not echo-only)."""
    systems = config.get("SYSTEMS", {})
    if not isinstance(systems, dict):
        return False
    return any(
        isinstance(cfg, dict) and cfg.get("ENABLED", True) and cfg.get("MODE") == "MASTER"
        for cfg in systems.values()
    )


def is_proxy_inject_only(config: dict[str, Any], system_name: str) -> bool:
    target = proxy_target_system(config)
    return target is not None and target == system_name


def normalize_proxy_target(config: dict[str, Any]) -> None:
    """Strip direct UDP bind fields from inject-only proxy target (D-23)."""
    target = proxy_target_system(config)
    if not target:
        return
    sys_cfg = config.get("SYSTEMS", {}).get(target)
    if not isinstance(sys_cfg, dict):
        return
    port = sys_cfg.pop("PORT", None)
    sys_cfg.pop("IP", None)
    if port is not None:
        sys_cfg["_REPORT_BASE_PORT"] = int(port)
    else:
        sys_cfg.setdefault("_REPORT_BASE_PORT", 56400)


def _obp_proxy_block(config: dict[str, Any]) -> dict[str, Any] | None:
    block = config.get("OBP_PROXY")
    return block if isinstance(block, dict) else None


def config_has_enabled_openbridge(config: dict[str, Any]) -> bool:
    """True when config defines at least one enabled OPENBRIDGE system."""
    systems = config.get("SYSTEMS", {})
    if not isinstance(systems, dict):
        return False
    return any(
        isinstance(cfg, dict) and cfg.get("ENABLED", True) and cfg.get("MODE") == "OPENBRIDGE"
        for cfg in systems.values()
    )


def openbridge_passphrase_collisions(config: dict[str, Any]) -> list[list[str]]:
    """Enabled OPENBRIDGE systems grouped by a passphrase more than one of them uses.

    Control frames carry no NETWORK_ID, so on the shared fan-in port the passphrase
    is what tells two bridges apart when the source address does not. The groups are
    returned, never the passphrase.
    """
    systems = config.get("SYSTEMS", {})
    if not isinstance(systems, dict):
        return []
    by_passphrase: dict[bytes, list[str]] = {}
    for name, sys_cfg in systems.items():
        if not isinstance(sys_cfg, dict) or sys_cfg.get("MODE") != "OPENBRIDGE":
            continue
        if not sys_cfg.get("ENABLED", True):
            continue
        passphrase = sys_cfg.get("PASSPHRASE") or b""
        if isinstance(passphrase, str):
            passphrase = passphrase.encode("utf-8")
        passphrase = bytes(passphrase).strip().rstrip(b"\x00")
        if not passphrase:
            continue
        by_passphrase.setdefault(passphrase, []).append(str(name))
    return sorted(
        (sorted(names) for names in by_passphrase.values() if len(names) > 1),
        key=lambda names: names[0],
    )


def obp_proxy_enabled(config: dict[str, Any]) -> bool:
    """True when OBP proxy manages inbound UDP (default on for OPENBRIDGE configs)."""
    block = _obp_proxy_block(config)
    if block is not None:
        return bool(block.get("ENABLED", True))
    return config_has_enabled_openbridge(config)


def obp_proxy_bind_legacy_ports(config: dict[str, Any]) -> bool:
    """When OBP proxy is enabled, also listen on each OPENBRIDGE section PORT (default true)."""
    if not obp_proxy_enabled(config):
        return False
    block = _obp_proxy_block(config)
    if block is None:
        return True
    return bool(block.get("BIND_LEGACY_PORTS", True))


def obp_bridge_legacy_listen_port(
    sys_cfg: dict[str, Any],
    *,
    listen_port: int,
    bind_legacy_ports: bool,
) -> int | None:
    """Per-bridge legacy UDP port, or None when inbound uses OBP_PROXY fan-in only.

    When BIND_LEGACY_PORTS is true globally, a bridge with PORT equal to LISTEN_PORT is
    treated as migrated to the shared fan-in (no extra legacy listener).
    """
    if not bind_legacy_ports:
        return None
    report_port = sys_cfg.get("_REPORT_PORT")
    if report_port is not None:
        port = int(report_port)
    elif "PORT" in sys_cfg:
        port = int(sys_cfg.get("PORT", 0) or 0)
    else:
        return None
    if port <= 0 or port == listen_port:
        return None
    return port


def is_obp_proxy_managed(config: dict[str, Any], system_name: str) -> bool:
    """OPENBRIDGE under active OBP proxy — no direct HBPProtocol UDP bind."""
    if not obp_proxy_enabled(config):
        return False
    sys_cfg = config.get("SYSTEMS", {}).get(system_name)
    if not isinstance(sys_cfg, dict):
        return False
    return sys_cfg.get("MODE") == "OPENBRIDGE"


def normalize_obp_proxy_targets(config: dict[str, Any]) -> None:
    """Strip bind fields from OPENBRIDGE systems when OBP proxy manages inbound UDP."""
    if not obp_proxy_enabled(config):
        return
    block = _obp_proxy_block(config) or {}
    listen_port = int(block.get("LISTEN_PORT", 62032))
    systems = config.get("SYSTEMS", {})
    if not isinstance(systems, dict):
        return
    for sys_cfg in systems.values():
        if not isinstance(sys_cfg, dict):
            continue
        if sys_cfg.get("MODE") != "OPENBRIDGE":
            continue
        port = sys_cfg.pop("PORT", None)
        bind_ip = sys_cfg.pop("IP", None)
        parsed_port = 0
        if port is not None and port != "":
            try:
                parsed_port = int(port)
            except (TypeError, ValueError):
                parsed_port = 0
        if parsed_port > 0:
            sys_cfg["_REPORT_PORT"] = parsed_port
        else:
            sys_cfg["_REPORT_PORT"] = listen_port
        if bind_ip is not None and str(bind_ip).strip():
            sys_cfg["_REPORT_BIND_IP"] = str(bind_ip)
