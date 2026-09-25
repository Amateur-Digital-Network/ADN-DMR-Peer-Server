# ADN DMR Peer Server - YAML config loader
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

"""Load config from YAML at project root; same semantic structure as legacy INI."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from ..domain import ID_MAX, ID_MIN, PEER_MAX
from ..domain.errors import ConfigError
from .config_validator import validate_config
from .proxy.config import apply_proxy_env_overrides


def acl_build(acl_str: str | None, max_id: int) -> tuple[bool, list[tuple[int, int]]]:
    """Build ACL from string e.g. 'DENY:1-5,3120101'. Returns (action, [(lo, hi), ...])."""
    if not acl_str:
        return (True, [(ID_MIN, max_id)])
    parts = acl_str.split(":", 1)
    if len(parts) != 2:
        return (True, [(ID_MIN, max_id)])
    action = parts[0].strip().upper() == "PERMIT"
    acl: list[tuple[int, int]] = []
    for entry in parts[1].strip().split(","):
        entry = entry.strip()
        if entry == "ALL":
            acl.append((ID_MIN, max_id))
            break
        if "-" in entry:
            start_s, end_s = entry.split("-", 1)
            start, end = int(start_s.strip()), int(end_s.strip())
            if not (ID_MIN <= start <= max_id) and not (ID_MIN <= end <= max_id):
                raise ConfigError(f"ACL range out of bounds: {entry}")
            acl.append((start, end))
        else:
            i = int(entry)
            if not (ID_MIN <= i <= max_id):
                raise ConfigError(f"ACL id out of bounds: {entry}")
            acl.append((i, i))
    return (action, acl)


def process_acls(config: dict[str, Any]) -> None:
    """Inject processed ACLs into CONFIG (GLOBAL and per SYSTEM). Mutates config."""

    g = config.get("GLOBAL", {})
    g["REG_ACL"] = acl_build(g.get("REG_ACL", "PERMIT:ALL"), PEER_MAX)
    for key in ("SUB_ACL", "TG1_ACL", "TG2_ACL"):
        acl_key = "TGID_TS1_ACL" if key == "TG1_ACL" else ("TGID_TS2_ACL" if key == "TG2_ACL" else key)
        g[key] = acl_build(g.get(acl_key, g.get(key, "PERMIT:ALL")), ID_MAX)
    for system_name, sys_cfg in config.get("SYSTEMS", {}).items():
        if sys_cfg.get("MODE") == "MASTER":
            sys_cfg["REG_ACL"] = acl_build(sys_cfg.get("REG_ACL", "PERMIT:ALL"), PEER_MAX)
        for key in ("SUB_ACL", "TG1_ACL", "TG2_ACL"):
            acl_key = "TGID_TS1_ACL" if key == "TG1_ACL" else ("TGID_TS2_ACL" if key == "TG2_ACL" else key)
            sys_cfg[key] = acl_build(sys_cfg.get(acl_key, sys_cfg.get(key, "PERMIT:ALL")), ID_MAX)


class YamlConfigLoader:
    """Load config from YAML file; same semantics as legacy build_config."""

    def __init__(self, project_root: str | Path = ".") -> None:
        self._root = Path(project_root).resolve()

    def load(self, path: str | None = None) -> dict[str, Any]:
        """Load config. path=None uses project root adn-server.yaml."""
        if path is None:
            path = str(self._root / "adn-server.yaml")
        if not os.path.isfile(path):
            raise ConfigError(f"Config file not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not data or not isinstance(data, dict):
            raise ConfigError("Invalid YAML or empty config")
        # Normalize to same top-level keys as legacy
        config: dict[str, Any] = {
            "GLOBAL": data.get("GLOBAL", {}),
            "VOICE": data.get("VOICE", {}),
            "REPORTS": data.get("REPORTS", {}),
            "LOGGER": data.get("LOGGER", {}),
            "ALIASES": data.get("ALIASES", {}),
            "SYSTEMS": data.get("SYSTEMS", {}),
            "PROXY": data.get("PROXY", {}),
            "DATABASE": data.get("DATABASE", {}),
            "SELF_SERVICE": data.get("SELF_SERVICE", {}),
        }
        # OBP_PROXY is optional: keep it only when present so callers can tell
        # "absent" (defaults apply) from "present but disabled".
        if isinstance(data.get("OBP_PROXY"), dict):
            config["OBP_PROXY"] = data["OBP_PROXY"]
        # PLUGINS (directory, master_kill, overrides, send) is read by the plugin manager.
        if isinstance(data.get("PLUGINS"), dict):
            config["PLUGINS"] = data["PLUGINS"]
        apply_proxy_env_overrides(config)
        # Ensure REPORT_CLIENTS is list
        if "REPORT_CLIENTS" in config["REPORTS"] and isinstance(config["REPORTS"]["REPORT_CLIENTS"], str):
            config["REPORTS"]["REPORT_CLIENTS"] = [
                x.strip() for x in config["REPORTS"]["REPORT_CLIENTS"].split(",")
            ]
        validate_config(config, config_path=path)
        process_acls(config)
        return config

    def load_voice_config(self, voice_path: str | None = None) -> dict[str, Any]:
        """Load voice config from adn-voice.yaml. Returns the VOICE dict (empty if file missing)."""
        if not voice_path:
            voice_path = str(self._root / "adn-voice.yaml")
        if not os.path.isfile(voice_path):
            return {}
        try:
            with open(voice_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except (OSError, yaml.YAMLError):
            return {}
        if not data or not isinstance(data, dict):
            return {}
        return data.get("VOICE", {})

    def reload_voice_config(self, config: dict[str, Any], voice_path: str | None = None) -> None:
        """Hot-reload adn-voice.yaml into config["VOICE"]. Called every 15 s."""
        new_voice = self.load_voice_config(voice_path)
        if new_voice:
            config.setdefault("VOICE", {}).update(new_voice)
