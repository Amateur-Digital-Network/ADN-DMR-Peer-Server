# ADN DMR Peer Server - YAML config validation
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

"""Validate adn-server.yaml types and required combinations before startup."""

from __future__ import annotations

from typing import Any

from adn_server.application.proxy.deployment import config_has_enabled_openbridge

from ..domain.errors import ConfigError

ACL_KEYS = frozenset(
    {
        "REG_ACL",
        "SUB_ACL",
        "TG1_ACL",
        "TG2_ACL",
        "TGID_ACL",
        "TGID_TS1_ACL",
        "TGID_TS2_ACL",
    }
)

GLOBAL_STRING_KEYS = frozenset(
    {
        "PATH",
        "ANNOUNCEMENT_LANGUAGES",
        "URL_SECURITY",
        "PORT_SECURITY",
        "PASS_SECURITY",
        "USERS_PASS",
        "HASH_ENCRYPT",
        "TALKER_ALIAS_MODE",
        "TALKER_ALIAS_FORMAT",
        "TALKER_ALIAS_TEXT_FORMAT",
        *ACL_KEYS,
    }
)

LOGGER_STRING_KEYS = frozenset({"LOG_FILE", "LOG_HANDLERS", "LOG_LEVEL", "LOG_NAME"})

SYSTEM_STRING_KEYS = frozenset(
    {
        "MODE",
        "IP",
        "PASSPHRASE",
        "MASTER_IP",
        "CALLSIGN",
        "LOCATION",
        "DESCRIPTION",
        "URL",
        "SOFTWARE_ID",
        "PACKAGE_ID",
        "OPTIONS",
        "TARGET_IP",
        "TS1_STATIC",
        "TS2_STATIC",
        "ANNOUNCEMENT_LANGUAGE",
        "OVERRIDE_IDENT_TG",
        *ACL_KEYS,
    }
)


def _is_empty(value: Any) -> bool:
    return value is None or value == ""


def _expect_str(path: str, value: Any, errors: list[str]) -> None:
    if _is_empty(value):
        return
    if isinstance(value, str):
        return
    errors.append(
        f"{path}: expected string, got {type(value).__name__} ({value!r}). "
        "Use quotes in YAML or re-run scripts/freedmr_cfg_to_yaml.py."
    )


def _expect_bool(path: str, value: Any, errors: list[str]) -> None:
    if _is_empty(value):
        return
    if isinstance(value, bool):
        return
    errors.append(f"{path}: expected boolean (true/false), got {type(value).__name__} ({value!r}).")


def _expect_int(path: str, value: Any, errors: list[str]) -> None:
    if _is_empty(value):
        return
    if isinstance(value, bool):
        errors.append(f"{path}: expected integer, got boolean ({value!r}).")
        return
    if isinstance(value, int):
        return
    errors.append(f"{path}: expected integer, got {type(value).__name__} ({value!r}).")


def _expect_number(path: str, value: Any, errors: list[str]) -> None:
    if _is_empty(value):
        return
    if isinstance(value, bool):
        errors.append(f"{path}: expected number, got boolean ({value!r}).")
        return
    if isinstance(value, (int, float)):
        return
    errors.append(f"{path}: expected number, got {type(value).__name__} ({value!r}).")


def _section_string_keys(section_name: str, section: dict[str, Any], keys: frozenset[str], errors: list[str]) -> None:
    for key in keys:
        if key in section:
            _expect_str(f"{section_name}.{key}", section[key], errors)


def _validate_global(global_cfg: dict[str, Any], errors: list[str]) -> None:
    _section_string_keys("GLOBAL", global_cfg, GLOBAL_STRING_KEYS, errors)
    for key in ("PING_TIME", "MAX_MISSED", "SERVER_ID", "UDP_RCVBUF"):
        if key in global_cfg:
            _expect_int(f"GLOBAL.{key}", global_cfg[key], errors)
    for key in (
        "USE_ACL",
        "GEN_STAT_BRIDGES",
        "ALLOW_NULL_PASSPHRASE",
        "DATA_GATEWAY",
        "VALIDATE_SERVER_IDS",
        "DEBUG_BRIDGES",
        "ENABLE_API",
        "TALKER_ALIAS",
        "TALKER_ALIAS_SEND_DMRA",
    ):
        if key in global_cfg:
            _expect_bool(f"GLOBAL.{key}", global_cfg[key], errors)

    url = global_cfg.get("URL_SECURITY")
    port = global_cfg.get("PORT_SECURITY")
    password = global_cfg.get("PASS_SECURITY")
    if not _is_empty(url):
        if _is_empty(port):
            errors.append("GLOBAL.PORT_SECURITY: required when GLOBAL.URL_SECURITY is set.")
        if _is_empty(password):
            errors.append("GLOBAL.PASS_SECURITY: required when GLOBAL.URL_SECURITY is set.")


def _validate_mqtt(reports_cfg: dict[str, Any], errors: list[str]) -> None:
    mqtt_block = reports_cfg.get("MQTT")
    if isinstance(mqtt_block, dict):
        if "ENABLED" in mqtt_block:
            _expect_bool("REPORTS.MQTT.ENABLED", mqtt_block["ENABLED"], errors)
        if mqtt_block.get("ENABLED") is True:
            url = mqtt_block.get("URL") or reports_cfg.get("MQTT_URL")
            if _is_empty(url):
                errors.append("REPORTS.MQTT.URL: required when REPORTS.MQTT.ENABLED is true.")
        if "QOS" in mqtt_block:
            qos = mqtt_block["QOS"]
            if not isinstance(qos, int) or qos < 0 or qos > 2:
                errors.append(f"REPORTS.MQTT.QOS: expected integer 0–2, got {qos!r}.")
    if "MQTT_ENABLED" in reports_cfg:
        _expect_bool("REPORTS.MQTT_ENABLED", reports_cfg["MQTT_ENABLED"], errors)
    if reports_cfg.get("MQTT_ENABLED") is True and _is_empty(reports_cfg.get("MQTT_URL")):
        errors.append("REPORTS.MQTT_URL: required when REPORTS.MQTT_ENABLED is true.")
    if "MQTT_QOS" in reports_cfg:
        qos = reports_cfg["MQTT_QOS"]
        if not isinstance(qos, int) or qos < 0 or qos > 2:
            errors.append(f"REPORTS.MQTT_QOS: expected integer 0–2, got {qos!r}.")


def _validate_reports(reports_cfg: dict[str, Any], errors: list[str]) -> None:
    if "REPORT" in reports_cfg:
        _expect_bool("REPORTS.REPORT", reports_cfg["REPORT"], errors)
    for key in ("REPORT_INTERVAL", "REPORT_PORT"):
        if key in reports_cfg:
            _expect_int(f"REPORTS.{key}", reports_cfg[key], errors)
    if "REPORT_CLIENTS" in reports_cfg and not _is_empty(reports_cfg["REPORT_CLIENTS"]):
        clients = reports_cfg["REPORT_CLIENTS"]
        if not isinstance(clients, (str, list)):
            errors.append(
                f"REPORTS.REPORT_CLIENTS: expected string or list, got {type(clients).__name__} ({clients!r})."
            )
    _validate_mqtt(reports_cfg, errors)


def _validate_logger(logger_cfg: dict[str, Any], errors: list[str]) -> None:
    _section_string_keys("LOGGER", logger_cfg, LOGGER_STRING_KEYS, errors)


def _validate_proxy(proxy_cfg: dict[str, Any] | None, systems: dict[str, Any], errors: list[str]) -> int | None:
    from adn_server.application.proxy.deployment import config_has_enabled_master

    if not isinstance(systems, dict) or not config_has_enabled_master({"SYSTEMS": systems}):
        return None
    if not proxy_cfg or not isinstance(proxy_cfg, dict):
        errors.append("PROXY: required when config has enabled MASTER systems (adn-server).")
        return None
    for key in ("DEBUG", "CLIENT_INFO", "STATS"):
        if key in proxy_cfg:
            _expect_bool(f"PROXY.{key}", proxy_cfg[key], errors)
    if "LISTEN_PORT" in proxy_cfg:
        _expect_int("PROXY.LISTEN_PORT", proxy_cfg["LISTEN_PORT"], errors)
    if "TIMEOUT" in proxy_cfg:
        _expect_number("PROXY.TIMEOUT", proxy_cfg["TIMEOUT"], errors)
    if "TARGET_SYSTEM" in proxy_cfg and not _is_empty(proxy_cfg["TARGET_SYSTEM"]):
        _expect_str("PROXY.TARGET_SYSTEM", proxy_cfg["TARGET_SYSTEM"], errors)
    if "LISTEN_IP" in proxy_cfg:
        _expect_str("PROXY.LISTEN_IP", proxy_cfg["LISTEN_IP"], errors)
    if "BLACK_LIST" in proxy_cfg and not isinstance(proxy_cfg["BLACK_LIST"], list):
        errors.append(
            f"PROXY.BLACK_LIST: expected list, got {type(proxy_cfg['BLACK_LIST']).__name__}."
        )
    if "IP_BLACK_LIST" in proxy_cfg and not isinstance(proxy_cfg["IP_BLACK_LIST"], dict):
        errors.append(
            f"PROXY.IP_BLACK_LIST: expected mapping, got {type(proxy_cfg['IP_BLACK_LIST']).__name__}."
        )
    for key in ("DISPATCH", "ENABLED", "PORT", "GENERATOR", "MASTER", "MAX_PROXY_SESSIONS", "udp_pool"):
        if key in proxy_cfg:
            errors.append(f"PROXY.{key}: removed in v2; integrated proxy is always enabled.")

    listen_port = proxy_cfg.get("LISTEN_PORT", 62031)
    if isinstance(listen_port, bool) or not isinstance(listen_port, int) or listen_port < 1:
        errors.append("PROXY.LISTEN_PORT: required >= 1.")
        listen_port = None

    target = proxy_cfg.get("TARGET_SYSTEM")
    if _is_empty(target):
        errors.append("PROXY.TARGET_SYSTEM: required.")
        return listen_port
    if not isinstance(systems, dict) or target not in systems:
        errors.append(f"PROXY.TARGET_SYSTEM: unknown system {target!r}.")
        return listen_port
    target_cfg = systems[target]
    if not isinstance(target_cfg, dict):
        errors.append(f"SYSTEMS.{target}: expected mapping.")
        return listen_port
    if not target_cfg.get("ENABLED", True):
        errors.append(f"PROXY.TARGET_SYSTEM: SYSTEMS.{target} must be ENABLED.")
    if target_cfg.get("MODE") != "MASTER":
        errors.append(f"PROXY.TARGET_SYSTEM: SYSTEMS.{target} must be MODE MASTER.")

    port = target_cfg.get("PORT", 0)
    if not _is_empty(port) and int(port) > 0:
        errors.append(
            f"SYSTEMS.{target}.PORT: must be omitted or 0 for inject-only proxy target (D-23)."
        )
    generator = int(target_cfg.get("GENERATOR", 1) or 1)
    if generator > 1:
        errors.append(
            f"SYSTEMS.{target}.GENERATOR: must be 0 or 1 on proxy target (use MAX_PEERS, not GENERATOR)."
        )
    return listen_port


def _validate_obp_proxy(
    obp_cfg: dict[str, Any] | None,
    systems: dict[str, Any],
    errors: list[str],
) -> int | None:
    if isinstance(obp_cfg, dict) and not obp_cfg.get("ENABLED", True):
        return None
    if not isinstance(obp_cfg, dict) and not config_has_enabled_openbridge({"SYSTEMS": systems}):
        return None
    effective = obp_cfg if isinstance(obp_cfg, dict) else {}
    for key in ("DEBUG", "BIND_LEGACY_PORTS"):
        if key in effective:
            _expect_bool(f"OBP_PROXY.{key}", effective[key], errors)
    if "LISTEN_IP" in effective:
        _expect_str("OBP_PROXY.LISTEN_IP", effective["LISTEN_IP"], errors)
    listen_port = effective.get("LISTEN_PORT", 62032)
    if isinstance(listen_port, bool) or not isinstance(listen_port, int) or listen_port < 1:
        errors.append("OBP_PROXY.LISTEN_PORT: required >= 1 when ENABLED.")
        return None
    bind_legacy = bool(effective.get("BIND_LEGACY_PORTS", True))
    network_ids: dict[Any, str] = {}
    legacy_ports: set[int] = set()
    if not isinstance(systems, dict):
        return listen_port
    for name, sys_cfg in systems.items():
        if not isinstance(sys_cfg, dict) or not sys_cfg.get("ENABLED", True):
            continue
        if sys_cfg.get("MODE") != "OPENBRIDGE":
            continue
        raw_nid = sys_cfg.get("NETWORK_ID")
        if raw_nid is not None and not _is_empty(raw_nid):
            if isinstance(raw_nid, bytes):
                nid_key = raw_nid
            else:
                try:
                    nid_key = int(raw_nid) & 0xFFFFFFFF
                except (TypeError, ValueError):
                    errors.append(f"SYSTEMS.{name}.NETWORK_ID: invalid value {raw_nid!r}.")
                    continue
            prev = network_ids.get(nid_key)
            if prev is not None:
                errors.append(
                    f"SYSTEMS.{name}.NETWORK_ID: duplicate OPENBRIDGE identity "
                    f"(same as SYSTEMS.{prev})."
                )
            else:
                network_ids[nid_key] = name
        if bind_legacy:
            port = sys_cfg.get("PORT", 0)
            if not _is_empty(port):
                try:
                    legacy_port = int(port)
                except (TypeError, ValueError):
                    errors.append(f"SYSTEMS.{name}.PORT: expected integer.")
                    continue
                if legacy_port > 0:
                    if legacy_port == listen_port:
                        continue
                    if legacy_port in legacy_ports:
                        errors.append(
                            f"SYSTEMS.{name}.PORT: duplicate OPENBRIDGE listen port {legacy_port}."
                        )
                    legacy_ports.add(legacy_port)
    return listen_port


def _config_requires_database(config: dict[str, Any]) -> bool:
    """True for ``run_peer_server`` configs (proxy/master); not echo-only PEER fleets."""
    proxy = config.get("PROXY")
    if isinstance(proxy, dict) and proxy:
        return True
    systems = config.get("SYSTEMS")
    if not isinstance(systems, dict):
        return False
    for sys_cfg in systems.values():
        if not isinstance(sys_cfg, dict):
            continue
        mode = str(sys_cfg.get("MODE", "")).upper()
        if mode in ("MASTER", "OPENBRIDGE"):
            return True
    return False


def _validate_database(db_cfg: Any, errors: list[str]) -> None:
    if db_cfg is None:
        errors.append("DATABASE: required block missing in adn-server.yaml")
        return
    if not isinstance(db_cfg, dict):
        errors.append(f"DATABASE: expected mapping, got {type(db_cfg).__name__}.")
        return
    if not str(db_cfg.get("DB_NAME", "")).strip():
        errors.append("DATABASE.DB_NAME: required.")
    if not str(db_cfg.get("DB_USERNAME", "")).strip():
        errors.append("DATABASE.DB_USERNAME: required.")
    port = db_cfg.get("DB_PORT", 3306)
    if isinstance(port, bool) or not isinstance(port, int) or port < 1:
        errors.append("DATABASE.DB_PORT: expected integer >= 1.")


def _validate_system(name: str, sys_cfg: dict[str, Any], errors: list[str]) -> None:
    prefix = f"SYSTEMS.{name}"
    _section_string_keys(prefix, sys_cfg, SYSTEM_STRING_KEYS, errors)
    mode = sys_cfg.get("MODE")
    if mode is not None and not _is_empty(mode) and not isinstance(mode, str):
        errors.append(f"{prefix}.MODE: expected string, got {type(mode).__name__} ({mode!r}).")
    for key in ("ENABLED", "REPEAT", "USE_ACL", "SINGLE_MODE", "VOICE_IDENT", "ALLOW_UNREG_ID", "PROXY_CONTROL", "EXPORT_AMBE", "LOOSE", "RELAX_CHECKS", "ENHANCED_OBP", "BOTH_SLOTS"):
        if key in sys_cfg:
            _expect_bool(f"{prefix}.{key}", sys_cfg[key], errors)
    for key in (
        "PORT",
        "MASTER_PORT",
        "MAX_PEERS",
        "GROUP_HANGTIME",
        "DEFAULT_UA_TIMER",
        "DEFAULT_REFLECTOR",
        "GENERATOR",
        "NETWORK_ID",
        "TARGET_PORT",
        "PROTO_VER",
        "RADIO_ID",
        "RX_FREQ",
        "TX_FREQ",
        "TX_POWER",
        "COLORCODE",
        "SLOTS",
        "HEIGHT",
    ):
        if key in sys_cfg:
            _expect_int(f"{prefix}.{key}", sys_cfg[key], errors)
    for key in ("LATITUDE", "LONGITUDE"):
        if key in sys_cfg:
            _expect_number(f"{prefix}.{key}", sys_cfg[key], errors)


def validate_config(config: dict[str, Any], *, config_path: str | None = None) -> None:
    """Validate config structure and scalar types. Raises ConfigError with all issues found."""
    errors: list[str] = []
    if not isinstance(config, dict):
        raise ConfigError("Configuration root must be a mapping.")

    _validate_global(config.get("GLOBAL", {}), errors)
    _validate_reports(config.get("REPORTS", {}), errors)
    _validate_logger(config.get("LOGGER", {}), errors)

    systems = config.get("SYSTEMS", {})
    if systems is not None and not isinstance(systems, dict):
        errors.append(f"SYSTEMS: expected mapping, got {type(systems).__name__}.")
    elif isinstance(systems, dict):
        for name, sys_cfg in systems.items():
            if not isinstance(sys_cfg, dict):
                errors.append(f"SYSTEMS.{name}: expected mapping, got {type(sys_cfg).__name__}.")
                continue
            _validate_system(name, sys_cfg, errors)

    proxy_cfg = config.get("PROXY")
    proxy_port = _validate_proxy(proxy_cfg if isinstance(proxy_cfg, dict) else None, systems if isinstance(systems, dict) else {}, errors)
    obp_proxy_cfg = config.get("OBP_PROXY")
    obp_port = _validate_obp_proxy(
        obp_proxy_cfg if isinstance(obp_proxy_cfg, dict) else None,
        systems if isinstance(systems, dict) else {},
        errors,
    )
    if proxy_port is not None and proxy_port == obp_port:
        errors.append(f"PROXY.LISTEN_PORT and OBP_PROXY.LISTEN_PORT: both {proxy_port}, would collide on bind.")
    if _config_requires_database(config):
        _validate_database(config.get("DATABASE"), errors)

    if errors:
        header = f"Configuration error in {config_path}:" if config_path else "Configuration error:"
        raise ConfigError("\n".join([header, *[f"  - {err}" for err in errors]]))
