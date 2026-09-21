# ADN DMR Peer Server - infrastructure doctor
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

"""Config and bind readiness checks (no reactor)."""

from __future__ import annotations

import logging
import os
import socket
import sys
from dataclasses import dataclass
from typing import TextIO

from adn_server.application.proxy.deployment import (
    is_proxy_inject_only,
    normalize_obp_proxy_targets,
    normalize_proxy_target,
    obp_bridge_legacy_listen_port,
    obp_proxy_enabled,
    openbridge_passphrase_collisions,
    proxy_target_system,
)
from adn_server.domain.errors import ConfigError
from adn_server.infrastructure.config_loader import YamlConfigLoader
from adn_server.infrastructure.config_normalizer import (
    apply_talker_alias_defaults,
    ensure_system_runtime_config,
    expand_generator,
    normalize_obp_config,
    normalize_peer_config,
)
from adn_server.infrastructure.proxy.obp_config import obp_proxy_settings


@dataclass(frozen=True)
class Finding:
    level: str  # ok, warn, error
    section: str
    message: str


class _NullLogger(logging.Logger):
    def __init__(self) -> None:
        super().__init__("doctor", level=logging.CRITICAL)


def _check_udp_bind(ip: str, port: int) -> tuple[bool, str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((ip or "0.0.0.0", port))
        return True, "available"
    except OSError as exc:
        return False, str(exc)
    finally:
        sock.close()


def _check_tcp_bind(ip: str, port: int) -> tuple[bool, str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((ip or "0.0.0.0", port))
        return True, "available"
    except OSError as exc:
        return False, str(exc)
    finally:
        sock.close()


def _resolve_host(host: str) -> tuple[bool, str]:
    try:
        return True, socket.gethostbyname(host)
    except OSError as exc:
        return False, str(exc)


def _alias_path(project_root: str, config: dict, filename: str) -> str:
    aliases = config.get("ALIASES", {})
    data_path = (aliases.get("PATH") or ".").rstrip("/")
    return os.path.join(project_root, data_path, filename)


def collect_findings(
    config: dict,
    *,
    project_root: str,
    config_path: str,
    echo: bool = False,
    no_proxy: bool = False,
) -> list[Finding]:
    findings: list[Finding] = []
    logger = _NullLogger()

    if echo:
        peers = [
            name
            for name, cfg in config.get("SYSTEMS", {}).items()
            if isinstance(cfg, dict) and cfg.get("MODE") == "PEER" and cfg.get("ENABLED", True)
        ]
        if peers:
            findings.append(Finding("ok", "echo", f"{len(peers)} PEER system(s): {', '.join(peers)}"))
        else:
            findings.append(Finding("error", "echo", "no enabled PEER systems in config"))
    else:
        apply_talker_alias_defaults(config)
        expand_generator(config, logger)
        normalize_proxy_target(config)
        ensure_system_runtime_config(config)
        normalize_peer_config(config)
        normalize_obp_config(config)
        normalize_obp_proxy_targets(config)

    findings.append(Finding("ok", "config", f"loaded {config_path}"))

    server_id = config.get("GLOBAL", {}).get("SERVER_ID", "?")
    findings.append(Finding("ok", "global", f"SERVER_ID={server_id}"))

    systems = config.get("SYSTEMS", {})
    if not isinstance(systems, dict) or not systems:
        findings.append(Finding("error", "systems", "SYSTEMS section missing or empty"))
        return findings

    for name, sys_cfg in sorted(systems.items()):
        if not isinstance(sys_cfg, dict):
            findings.append(Finding("error", "systems", f"{name}: invalid system entry"))
            continue
        if not sys_cfg.get("ENABLED", True):
            findings.append(Finding("warn", "systems", f"{name}: disabled"))
            continue

        mode = sys_cfg.get("MODE", "?")
        if mode == "MASTER":
            if is_proxy_inject_only(config, name):
                max_peers = sys_cfg.get("MAX_PEERS", "?")
                findings.append(
                    Finding("ok", "systems", f"{name}: MASTER inject-only, MAX_PEERS={max_peers}")
                )
            else:
                ip = str(sys_cfg.get("IP") or "0.0.0.0")
                port = int(sys_cfg.get("PORT", 56400))
                ok, detail = _check_udp_bind(ip, port)
                level = "ok" if ok else "error"
                findings.append(
                    Finding(level, "ports", f"{name}: UDP {ip}:{port} — {detail}")
                )
        elif mode == "PEER":
            master_ip = str(sys_cfg.get("_MASTER_IP") or sys_cfg.get("MASTER_IP", "127.0.0.1"))
            master_port = int(sys_cfg.get("MASTER_PORT", 56400))
            mesh = str(sys_cfg.get("MESH_PROTOCOL", "auto"))
            ok, resolved = _resolve_host(master_ip)
            level = "ok" if ok else "error"
            host_detail = resolved if ok else resolved
            findings.append(
                Finding(
                    level,
                    "peer",
                    f"{name}: upstream {master_ip}:{master_port} ({host_detail}), MESH_PROTOCOL={mesh}",
                )
            )
        elif mode == "OPENBRIDGE":
            proto_ver = sys_cfg.get("VER", sys_cfg.get("PROTO_VER", 5))
            enhanced = sys_cfg.get("ENHANCED_OBP", True)
            if obp_proxy_enabled(config):
                obp = obp_proxy_settings(config)
                legacy_port = obp_bridge_legacy_listen_port(
                    sys_cfg,
                    listen_port=int(obp["listen_port"]),
                    bind_legacy_ports=bool(obp["bind_legacy_ports"]),
                )
                if legacy_port is not None:
                    ip = str(sys_cfg.get("_REPORT_BIND_IP") or "0.0.0.0")
                    ok, detail = _check_udp_bind(ip, legacy_port)
                    level = "ok" if ok else "error"
                    findings.append(
                        Finding(
                            level,
                            "ports",
                            f"{name}: OPENBRIDGE legacy UDP {ip}:{legacy_port} — {detail}; "
                            f"PROTO_VER={proto_ver}, ENHANCED_OBP={enhanced}",
                        )
                    )
                else:
                    findings.append(
                        Finding(
                            "ok",
                            "systems",
                            f"{name}: OPENBRIDGE fan-in only (OBP_PROXY {obp['listen_port']}); "
                            f"PROTO_VER={proto_ver}, ENHANCED_OBP={enhanced}",
                        )
                    )
            else:
                ip = str(sys_cfg.get("IP") or "0.0.0.0")
                port = int(sys_cfg.get("PORT", 62044))
                ok, detail = _check_udp_bind(ip, port)
                level = "ok" if ok else "error"
                findings.append(
                    Finding(
                        level,
                        "ports",
                        f"{name}: OPENBRIDGE UDP {ip}:{port} — {detail}; "
                        f"PROTO_VER={proto_ver}, ENHANCED_OBP={enhanced}",
                    )
                )
            target_ip = sys_cfg.get("TARGET_IP", "")
            if isinstance(target_ip, bytes):
                target_ip = target_ip.decode("utf-8", errors="replace")
            if target_ip:
                tport = int(sys_cfg.get("TARGET_PORT", 62044))
                tok, tres = _resolve_host(str(target_ip))
                tl = "ok" if tok else "warn"
                findings.append(
                    Finding(tl, "peer", f"{name}: target {target_ip}:{tport} ({tres if tok else tres})")
                )
        else:
            findings.append(Finding("warn", "systems", f"{name}: unknown MODE={mode}"))

    for shared in openbridge_passphrase_collisions(config):
        findings.append(
            Finding(
                "warn",
                "peer",
                f"{', '.join(shared)}: same PASSPHRASE — a control frame from an address "
                "none of them is configured with goes to whichever is registered first",
            )
        )

    if not echo:
        reports = config.get("REPORTS", {})
        if reports.get("REPORT", True):
            port = int(reports.get("REPORT_PORT", 4321))
            ok, detail = _check_tcp_bind("0.0.0.0", port)
            level = "ok" if ok else "error"
            findings.append(Finding(level, "ports", f"report TCP 0.0.0.0:{port} — {detail}"))

        proxy_target = proxy_target_system(config)
        if proxy_target and not no_proxy:
            proxy = config.get("PROXY", {})
            port = int(proxy.get("LISTEN_PORT", 62031))
            ip = str(proxy.get("LISTEN_IP") or "0.0.0.0")
            ok, detail = _check_udp_bind(ip, port)
            level = "ok" if ok else "error"
            findings.append(
                Finding(level, "proxy", f"PROXY UDP {ip}:{port} → {proxy_target} — {detail}")
            )
        elif proxy_target and no_proxy:
            findings.append(Finding("warn", "proxy", f"PROXY configured ({proxy_target}) but --no-proxy set"))

        if obp_proxy_enabled(config):
            obp = obp_proxy_settings(config)
            ip = str(obp["listen_ip"] or "0.0.0.0")
            port = int(obp["listen_port"])
            ok, detail = _check_udp_bind(ip, port)
            level = "ok" if ok else "error"
            findings.append(
                Finding(
                    level,
                    "proxy",
                    f"OBP_PROXY UDP {ip}:{port} — {detail}; BIND_LEGACY_PORTS={obp['bind_legacy_ports']}",
                )
            )

        aliases = config.get("ALIASES", {})
        for key, default in (
            ("PEER_FILE", "peer_ids.json"),
            ("SUBSCRIBER_FILE", "subscriber_ids.json"),
            ("TGID_FILE", "talkgroup_ids.json"),
        ):
            path = _alias_path(project_root, config, aliases.get(key) or default)
            if os.path.isfile(path):
                findings.append(Finding("ok", "aliases", f"{key}: {path}"))
            else:
                findings.append(Finding("warn", "aliases", f"{key}: missing {path}"))

    return findings


def format_report(
    findings: list[Finding],
    *,
    version: str,
    config_path: str,
) -> str:
    lines = [f"adn-server doctor {version}", f"config: {config_path}", ""]
    for item in findings:
        prefix = {"ok": "OK", "warn": "WARN", "error": "ERROR"}.get(item.level, item.level.upper())
        lines.append(f"[{prefix}] {item.section}: {item.message}")
    errors = sum(1 for f in findings if f.level == "error")
    warns = sum(1 for f in findings if f.level == "warn")
    lines.append("")
    lines.append(f"summary: {errors} error(s), {warns} warning(s)")
    return "\n".join(lines)


def run_doctor(
    config_path: str,
    project_root: str,
    *,
    echo: bool = False,
    no_proxy: bool = False,
    version: str = "",
    out: TextIO | None = None,
) -> int:
    """Load config, run checks, print report. Returns 0 if no errors."""
    stream = out or sys.stdout
    loader = YamlConfigLoader(project_root)
    try:
        config = loader.load(config_path)
    except ConfigError as exc:
        print(f"ERROR config: {exc}", file=sys.stderr)
        return 1

    findings = collect_findings(
        config,
        project_root=project_root,
        config_path=config_path,
        echo=echo,
        no_proxy=no_proxy,
    )
    print(format_report(findings, version=version, config_path=config_path), file=stream)
    return 1 if any(f.level == "error" for f in findings) else 0
