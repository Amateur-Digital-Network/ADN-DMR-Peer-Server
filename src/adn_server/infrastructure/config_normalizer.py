# ADN DMR Peer Server - config normalization helpers
# Copyright (C) 2026  Rodrigo Pérez, CE5RPY <ce5rpy@qmd.cl>
#
# Derived from ADN DMR Server / FreeDMR  / HBlink. Original license:
###############################################################################
# Copyright (C) 2026 Joaquin Madrid Belando, EA5GVK <ea5gvk@gmail.com>
# Copyright (C) 2020 Simon Adlem, G7RZU <g7rzu@gb7fr.org.uk>
# Copyright (C) 2016-2019 Cortney T. Buffington, N0MJS <n0mjs@me.com>
#
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

"""Shared config normalization: PEER, OBP, MASTER runtime state. Used by main and echo runtime."""

from __future__ import annotations

import copy
import logging
import socket
import time


def expand_generator(config: dict, logger: logging.Logger) -> None:
    """Replace MASTER systems with GENERATOR > 1 by SYSTEM-0, SYSTEM-1, ... (legacy generator)."""
    from adn_server.application.proxy.deployment import is_proxy_inject_only

    systems = config.get("SYSTEMS", {})
    to_remove: list[str] = []
    new_systems: dict = {}
    for system_name, sys_cfg in list(systems.items()):
        if not sys_cfg.get("ENABLED", True):
            continue
        if sys_cfg.get("MODE") != "MASTER":
            continue
        if is_proxy_inject_only(config, system_name):
            continue
        generator = int(sys_cfg.get("GENERATOR", 1))
        if generator <= 1:
            continue
        for count in range(generator):
            new_name = f"{system_name}-{count}"
            new_cfg = copy.deepcopy(sys_cfg)
            base_port = int(new_cfg.get("PORT", 56400))
            new_cfg["PORT"] = base_port + count
            new_cfg["_default_options"] = "SINGLE={};DEFAULT_UA_TIMER={};VOICE={};LANG={}".format(
                int(new_cfg.get("SINGLE_MODE", False)),
                new_cfg.get("DEFAULT_UA_TIMER", 60),
                int(new_cfg.get("VOICE_IDENT", False)),
                new_cfg.get("ANNOUNCEMENT_LANGUAGE", "en_GB"),
            )
            new_systems[new_name] = new_cfg
            logger.debug("(GLOBAL) Generator - generated system %s", new_name)
        to_remove.append(system_name)
    for name in to_remove:
        systems.pop(name, None)
    for name, cfg in new_systems.items():
        systems[name] = cfg


def ensure_system_runtime_config(config: dict) -> None:
    """Ensure MASTER has PEERS and PEER has STATS (legacy config.py runtime state)."""
    for name, sys_cfg in config.get("SYSTEMS", {}).items():
        if sys_cfg.get("MODE") == "MASTER":
            sys_cfg.setdefault("PEERS", {})
        elif sys_cfg.get("MODE") == "PEER":
            sys_cfg.setdefault("STATS", {
                "CONNECTION": "NO",
                "CONNECTED": None,
                "PINGS_SENT": 0,
                "PINGS_ACKD": 0,
                "NUM_OUTSTANDING": 0,
                "PING_OUTSTANDING": False,
                "LAST_PING_TX_TIME": 0,
                "LAST_PING_ACK_TIME": 0,
            })


def apply_talker_alias_defaults(config: dict) -> None:
    """Default GLOBAL Talker Alias keys (mode both; max length is protocol-fixed in code)."""
    g = config.setdefault("GLOBAL", {})
    g.setdefault("TALKER_ALIAS", False)
    g.setdefault("TALKER_ALIAS_MODE", "both")
    g.setdefault("TALKER_ALIAS_FORMAT", "{callsign} {fname}")
    g.setdefault("TALKER_ALIAS_TEXT_FORMAT", "utf8")
    # Standalone DMRA UDP off by default (radios use embedded LC; matches legacy wire).
    g.setdefault("TALKER_ALIAS_SEND_DMRA", False)


def normalize_peer_config(config: dict) -> None:
    """Convert PEER systems from YAML to legacy format: MASTER_SOCKADDR, RADIO_ID/CALLSIGN/OPTIONS as bytes (config.py)."""
    for name, sys_cfg in config.get("SYSTEMS", {}).items():
        if sys_cfg.get("MODE") != "PEER":
            continue
        master_ip_str = str(sys_cfg.get("MASTER_IP", "127.0.0.1"))
        master_port = int(sys_cfg.get("MASTER_PORT", 56400))
        try:
            resolved_ip = socket.gethostbyname(master_ip_str)
        except OSError:
            resolved_ip = master_ip_str
        sys_cfg["_MASTER_IP"] = master_ip_str
        sys_cfg["MASTER_IP"] = resolved_ip
        sys_cfg["MASTER_PORT"] = master_port
        sys_cfg["MASTER_SOCKADDR"] = (resolved_ip, master_port)
        radio_id = int(sys_cfg.get("RADIO_ID", 0))
        sys_cfg["RADIO_ID"] = (radio_id & 0xFFFFFFFF).to_bytes(4, "big")
        for field, length in [
            ("CALLSIGN", 8), ("RX_FREQ", 9), ("TX_FREQ", 9), ("TX_POWER", 2), ("COLORCODE", 2),
            ("LATITUDE", 8), ("LONGITUDE", 9), ("HEIGHT", 3), ("LOCATION", 20), ("DESCRIPTION", 19),
            ("SLOTS", 1), ("URL", 124), ("SOFTWARE_ID", 40), ("PACKAGE_ID", 40),
        ]:
            val = sys_cfg.get(field, "")
            if isinstance(val, (int, float)):
                val = str(val)
            b = val.encode("utf-8") if isinstance(val, str) else val
            if field == "CALLSIGN":
                sys_cfg[field] = b.ljust(length)[:length]
            elif field in ("RX_FREQ", "TX_FREQ", "LATITUDE", "LONGITUDE", "LOCATION", "DESCRIPTION", "URL", "SOFTWARE_ID", "PACKAGE_ID"):
                sys_cfg[field] = b.ljust(length)[:length]
            else:
                sys_cfg[field] = b.rjust(length, b"0")[:length] if length <= 3 else b.ljust(length)[:length]
        opt = sys_cfg.get("OPTIONS", "")
        sys_cfg["OPTIONS"] = opt.encode("utf-8") if isinstance(opt, str) else (opt or b"")
        passphrase = sys_cfg.get("PASSPHRASE", "")
        sys_cfg["PASSPHRASE"] = passphrase.encode("utf-8") if isinstance(passphrase, str) else (passphrase or b"")
        sys_cfg.setdefault("LOOSE", False)
        stats = sys_cfg.get("STATS", {})
        stats["DNS_TIME"] = time.time()


def normalize_obp_config(config: dict) -> None:
    """Normalize OPENBRIDGE systems and GLOBAL SERVER_ID (legacy config.py)."""
    g = config.setdefault("GLOBAL", {})
    sid = g.get("SERVER_ID", 0)
    g["SERVER_ID"] = (int(sid) & 0xFFFFFFFF).to_bytes(4, "big") if not isinstance(sid, bytes) else sid
    for name, sys_cfg in config.get("SYSTEMS", {}).items():
        if sys_cfg.get("MODE") != "OPENBRIDGE":
            continue
        net_id = int(sys_cfg.get("NETWORK_ID", 0))
        sys_cfg["NETWORK_ID"] = (net_id & 0xFFFFFFFF).to_bytes(4, "big")
        target_ip = str(sys_cfg.get("TARGET_IP", ""))
        target_port = int(sys_cfg.get("TARGET_PORT", 62044))
        # Keep the name the operator wrote, like PEER keeps _MASTER_IP: resolving
        # here overwrites TARGET_IP, and a hostname has to stay re-resolvable.
        sys_cfg["_TARGET_IP"] = target_ip
        if target_ip:
            try:
                resolved = socket.gethostbyname(target_ip)
                sys_cfg["TARGET_IP"] = resolved
                sys_cfg["TARGET_SOCK"] = (resolved, target_port)
            except OSError:
                sys_cfg["TARGET_IP"] = None
                sys_cfg["TARGET_SOCK"] = (None, target_port)
        else:
            sys_cfg["TARGET_IP"] = None
            sys_cfg["TARGET_SOCK"] = (None, target_port)
        ver = int(sys_cfg.get("PROTO_VER", sys_cfg.get("VER", 5)))
        if ver in (0, 2, 3) or ver > 5:
            ver = 5
        sys_cfg["VER"] = ver
        p = sys_cfg.get("PASSPHRASE") or b""
        if isinstance(p, str):
            p = p.strip().encode("utf-8")
        else:
            p = p or b""
        sys_cfg["PASSPHRASE"] = (p + b"\x00" * 20)[:20]
        sys_cfg.setdefault("RELAX_CHECKS", True)
        sys_cfg.setdefault("ENHANCED_OBP", True)
        if "TG1_ACL" not in sys_cfg and "TGID_ACL" in sys_cfg:
            sys_cfg["TG1_ACL"] = sys_cfg["TGID_ACL"]
        sys_cfg.setdefault("TG2_ACL", "PERMIT:ALL")
