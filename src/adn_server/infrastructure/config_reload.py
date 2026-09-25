# ADN DMR Peer Server - hot reload adn-server.yaml
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

"""Reload SYSTEMS / GLOBAL from adn-server.yaml without full process restart."""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from typing import Any, Callable

from twisted.internet import defer

from adn_server.application.proxy.deployment import normalize_obp_proxy_targets, normalize_proxy_target

from ..domain.errors import ConfigError
from .config_loader import YamlConfigLoader
from .config_normalizer import (
    apply_talker_alias_defaults,
    ensure_system_runtime_config,
    expand_generator,
    normalize_obp_config,
    normalize_peer_config,
)
from .logging_config import reapply_log_level

logger = logging.getLogger(__name__)

_RUNTIME_TOP_KEYS = frozenset({
    "_MESH_SESSIONS",
    "_SUB_MAP",
    "_SUB_IDS",
    "_SUB_PROFILES",
    "_PEER_IDS",
    "_TG_IDS",
    "_LOCAL_SUBSCRIBER_IDS",
    "_SERVER_IDS",
    "CHECKSUMS",
})


@dataclass(frozen=True)
class BindSpec:
    ip: str
    port: int


def bind_spec(sys_cfg: dict[str, Any]) -> BindSpec:
    ip = str(sys_cfg.get("IP") or "0.0.0.0")
    return BindSpec(ip=ip, port=int(sys_cfg.get("PORT", 56400)))


def enabled_systems(systems: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        name: cfg
        for name, cfg in systems.items()
        if cfg.get("ENABLED", True)
    }


def prepare_incoming_config(
    loader: YamlConfigLoader,
    config_path: str,
    log: logging.Logger,
) -> dict[str, Any]:
    """Load YAML and apply the same normalizers as startup (including GENERATOR expand)."""
    incoming = loader.load(config_path)
    apply_talker_alias_defaults(incoming)
    expand_generator(incoming, log)
    normalize_proxy_target(incoming)
    ensure_system_runtime_config(incoming)
    normalize_peer_config(incoming)
    normalize_obp_config(incoming)
    normalize_obp_proxy_targets(incoming)
    return incoming


def merge_system_config(old_cfg: dict[str, Any], new_cfg: dict[str, Any]) -> dict[str, Any]:
    """Apply new system settings while keeping live runtime state (PEERS, STATS, options/static TGs)."""
    merged = copy.deepcopy(new_cfg)
    mode = merged.get("MODE")
    if mode == "MASTER":
        merged["PEERS"] = old_cfg.get("PEERS", {})
        for key in (
            "OPTIONS",
            "TS1_STATIC",
            "TS2_STATIC",
            "DEFAULT_UA_TIMER",
            "_options_static_apply_fp",
            "_default_options",
            "_PEER_UA_SESSIONS",
            "_PEER_UA_MULTI_TGS",
        ):
            if key in old_cfg:
                merged[key] = old_cfg[key]
    elif mode == "PEER":
        stats = old_cfg.get("STATS")
        if isinstance(stats, dict):
            merged["STATS"] = stats
    return merged


def merge_top_level_config(config: dict[str, Any], incoming: dict[str, Any]) -> None:
    """Update GLOBAL / REPORTS / ALIASES / LOGGER / … / PLUGINS in the live config dict."""
    kill_flag = config.get("GLOBAL", {}).get("_KILL_SERVER")
    for key in ("GLOBAL", "REPORTS", "ALIASES", "LOGGER", "PROXY", "DATABASE", "SELF_SERVICE"):
        if key not in incoming:
            continue
        config[key] = copy.deepcopy(incoming[key])
    # PLUGINS always follows the file, absent included: it carries master_kill and the
    # per-plugin send permissions, whose removal must take effect on this reload.
    config["PLUGINS"] = copy.deepcopy(incoming.get("PLUGINS") or {})
    if kill_flag is not None:
        config.setdefault("GLOBAL", {})["_KILL_SERVER"] = kill_flag
    for rk in _RUNTIME_TOP_KEYS:
        if rk in config:
            continue
        if rk in incoming:
            config[rk] = incoming[rk]


@dataclass
class ReloadResult:
    added: list[str]
    removed: list[str]
    updated: list[str]
    rebound: list[str]


def _generator_collapse_renames(
    old_systems: dict[str, Any],
    new_systems: dict[str, Any],
    old_enabled: set[str],
) -> dict[str, str]:
    """Map ``NAME-0`` -> ``NAME`` when YAML GENERATOR drops from N>1 to 1 (expand collapsed)."""
    renames: dict[str, str] = {}
    for new_name, new_cfg in new_systems.items():
        if not new_cfg.get("ENABLED", True) or new_cfg.get("MODE") != "MASTER":
            continue
        if int(new_cfg.get("GENERATOR", 1)) > 1:
            continue
        if new_name in old_enabled:
            continue
        old_instance = f"{new_name}-0"
        if old_instance not in old_enabled:
            continue
        old_cfg = old_systems.get(old_instance, {})
        if bind_spec(old_cfg) == bind_spec(new_cfg):
            renames[old_instance] = new_name
    return renames


def _effective_old_key(old_name: str, renames: dict[str, str]) -> str:
    return renames.get(old_name, old_name)


def _old_instance_for_new(new_name: str, renames: dict[str, str]) -> str:
    for old_name, mapped in renames.items():
        if mapped == new_name:
            return old_name
    return new_name


def _queue_stop(
    port: Any,
    stop_listener: Callable[[Any], Any],
    pending: list[defer.Deferred],
) -> None:
    if port is None:
        return
    result = stop_listener(port)
    if isinstance(result, defer.Deferred):
        pending.append(result)


def reload_server_config(
    config: dict[str, Any],
    config_path: str,
    loader: YamlConfigLoader,
    protocols: dict[str, Any],
    transports: dict[str, Any],
    *,
    create_protocol: Callable[[str], Any],
    listen_udp: Callable[[str, BindSpec, Any], Any],
    stop_listener: Callable[[Any], None],
    on_systems_changed: Callable[[], None] | None = None,
    on_system_removed: Callable[[str, Any], None] | None = None,
    should_bind_udp: Callable[[str, dict[str, Any]], bool] | None = None,
    log: logging.Logger | None = None,
) -> defer.Deferred:
    """
    Re-read adn-server.yaml, diff SYSTEMS, start/stop UDP listeners.

    Preserves PEERS / STATS and protocol STATUS for systems that stay up with the
    same bind address. Returns a Deferred that fires on the reactor thread when
    listeners are rebound (waits for ``stopListening`` before re-bind).
    """
    log = log or logger
    try:
        incoming = prepare_incoming_config(loader, config_path, log)
    except ConfigError as e:
        log.error("(CONFIG-RELOAD) failed to load config: %s", e)
        return defer.fail(e)
    except Exception as e:
        log.error("(CONFIG-RELOAD) failed to prepare config: %s", e)
        return defer.fail(e)

    merge_top_level_config(config, incoming)
    if "LOGGER" in incoming:
        level_name = reapply_log_level(config.get("LOGGER", {}))
        log.info("(CONFIG-RELOAD) LOG_LEVEL applied: %s", level_name)

    old_systems = dict(config.get("SYSTEMS", {}))
    new_systems = incoming.get("SYSTEMS", {})
    old_enabled = set(enabled_systems(old_systems))
    new_enabled = set(enabled_systems(new_systems))
    collapse_renames = _generator_collapse_renames(old_systems, new_systems, old_enabled)
    mapped_old_enabled = {_effective_old_key(name, collapse_renames) for name in old_enabled}

    added: list[str] = []
    removed: list[str] = []
    updated: list[str] = []
    rebound: list[str] = []
    pending_stops: list[defer.Deferred] = []
    deferred_starts: list[tuple[str, dict[str, Any], Any, BindSpec | None]] = []

    def _schedule_start(name: str, sys_cfg: dict[str, Any], proto: Any, bind: BindSpec | None) -> None:
        deferred_starts.append((name, sys_cfg, proto, bind))

    def _migrate_protocol_key(old_key: str, new_key: str) -> None:
        if old_key == new_key:
            return
        if old_key in protocols and new_key not in protocols:
            protocols[new_key] = protocols.pop(old_key)
        if old_key in transports and new_key not in transports:
            transports[new_key] = transports.pop(old_key)

    for old_name in sorted(old_enabled):
        if _effective_old_key(old_name, collapse_renames) in new_enabled:
            continue
        port = transports.pop(old_name, None)
        proto = protocols.pop(old_name, None)
        if proto is not None and on_system_removed:
            on_system_removed(old_name, proto)
        if proto is not None:
            try:
                proto.dereg()
            except Exception as e:
                log.warning("(CONFIG-RELOAD) dereg %s: %s", old_name, e)
        _queue_stop(port, stop_listener, pending_stops)
        config.get("SYSTEMS", {}).pop(old_name, None)
        removed.append(old_name)
        log.info("(CONFIG-RELOAD) removed system %s", old_name)

    for name in sorted(new_enabled - mapped_old_enabled):
        sys_cfg = copy.deepcopy(new_systems[name])
        config.setdefault("SYSTEMS", {})[name] = sys_cfg
        proto = create_protocol(name)
        protocols[name] = proto
        if should_bind_udp is None or should_bind_udp(name, sys_cfg):
            _schedule_start(name, sys_cfg, proto, bind_spec(sys_cfg))
        else:
            transports.pop(name, None)
            log.info("(CONFIG-RELOAD) added inject-only system %s", name)
        added.append(name)

    for name in sorted(new_enabled & mapped_old_enabled):
        old_key = _old_instance_for_new(name, collapse_renames)
        old_cfg = old_systems[old_key]
        new_cfg = new_systems[name]
        old_bind = bind_spec(old_cfg)
        new_bind = bind_spec(new_cfg)
        merged = merge_system_config(old_cfg, new_cfg)
        config["SYSTEMS"][name] = merged
        if old_key in config.get("SYSTEMS", {}) and old_key != name:
            config["SYSTEMS"].pop(old_key, None)
        _migrate_protocol_key(old_key, name)
        proto = protocols.get(name)
        inject_only = should_bind_udp is not None and not should_bind_udp(name, merged)
        was_inject_only = should_bind_udp is not None and not should_bind_udp(name, old_cfg)
        if proto is None:
            proto = create_protocol(name)
            protocols[name] = proto
            if inject_only:
                transports.pop(name, None)
                log.info("(CONFIG-RELOAD) started missing inject-only listener %s", name)
            else:
                _schedule_start(name, merged, proto, new_bind)
                log.info(
                    "(CONFIG-RELOAD) started missing listener %s on %s:%s",
                    name, new_bind.ip, new_bind.port,
                )
            added.append(name)
            continue
        if hasattr(proto, "apply_system_config"):
            proto.apply_system_config(config)
        if inject_only:
            port = transports.pop(name, None)
            _queue_stop(port, stop_listener, pending_stops)
            protocols[name] = proto
            updated.append(name)
            log.debug("(CONFIG-RELOAD) %s inject-only (bind skipped)", name)
            continue
        if was_inject_only and not inject_only:
            _schedule_start(name, merged, proto, new_bind)
            rebound.append(name)
            log.info("(CONFIG-RELOAD) started UDP bind for %s on %s:%s", name, new_bind.ip, new_bind.port)
            continue
        if old_bind != new_bind:
            port = transports.pop(name, None)
            _queue_stop(port, stop_listener, pending_stops)
            _schedule_start(name, merged, proto, new_bind)
            rebound.append(name)
            log.info(
                "(CONFIG-RELOAD) rebound %s %s:%s -> %s:%s",
                name, old_bind.ip, old_bind.port, new_bind.ip, new_bind.port,
            )
        else:
            updated.append(name)
            if collapse_renames and old_key != name:
                log.info("(CONFIG-RELOAD) migrated system %s -> %s (bind unchanged)", old_key, name)
            else:
                log.debug("(CONFIG-RELOAD) updated system %s (bind unchanged)", name)

    for name in sorted(set(old_systems) - old_enabled):
        if name not in new_systems:
            config.get("SYSTEMS", {}).pop(name, None)

    def _finish_reload(_: Any = None) -> ReloadResult:
        for name, sys_cfg, proto, bind in deferred_starts:
            if bind is None:
                continue
            transports[name] = listen_udp(name, bind, proto)
            protocols[name] = proto
            if name in added:
                log.info("(CONFIG-RELOAD) added system %s on %s:%s", name, bind.ip, bind.port)
        if on_systems_changed and (added or removed or updated or rebound):
            on_systems_changed()
        log.info(
            "(CONFIG-RELOAD) complete: +%s -%s updated=%s rebound=%s",
            len(added), len(removed), len(updated), len(rebound),
        )
        return ReloadResult(added=added, removed=removed, updated=updated, rebound=rebound)

    if pending_stops:
        return defer.DeferredList(pending_stops).addCallback(_finish_reload)
    return defer.succeed(None).addCallback(_finish_reload)
