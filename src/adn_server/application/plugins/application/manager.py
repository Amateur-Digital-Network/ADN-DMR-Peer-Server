# ADN DMR Peer Server - plugin manager
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

"""Plugin lifecycle: discover, load, rescan, shutdown."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from ..application.bus import PluginBus
from ..application.context import ServerContext
from ..domain.protocol import ServerPlugin
from ..domain.send import send_permission
from ..infrastructure.loader import (
    load_plugin_from_entry,
    plugin_config_for_load,
    scan_plugin_dirs,
    topo_sort,
)

logger = logging.getLogger(__name__)


class PluginManager:
    def __init__(
        self,
        bus: PluginBus,
        ctx: ServerContext,
        project_root: str | Path,
        sender_factory: Callable[[str], Callable[[bytes], bool]] | None = None,
    ) -> None:
        self._bus = bus
        self._ctx = ctx
        self._sender_factory = sender_factory
        self._project_root = Path(project_root)
        self._loaded: dict[str, ServerPlugin] = {}
        self._configs: dict[str, dict[str, Any]] = {}

    @property
    def bus(self) -> PluginBus:
        return self._bus

    def plugins_directory(self, server_config: dict[str, Any]) -> Path:
        plugins_cfg = server_config.get("PLUGINS") or {}
        if plugins_cfg.get("master_kill"):
            return self._project_root / "__disabled__"
        custom = plugins_cfg.get("directory")
        if custom:
            p = Path(custom)
            return p if p.is_absolute() else self._project_root / p
        return self._project_root / "plugins"

    def discover_and_load(self, server_config: dict[str, Any]) -> list[ServerPlugin]:
        directory = self.plugins_directory(server_config)
        overrides = (server_config.get("PLUGINS") or {}).get("overrides") or {}
        entries = topo_sort(scan_plugin_dirs(directory))
        loaded: list[ServerPlugin] = []
        for entry in entries:
            if entry.name in self._loaded:
                continue
            try:
                plugin = load_plugin_from_entry(entry, directory, overrides=overrides)
                cfg = plugin_config_for_load(entry.config)
                if overrides.get(entry.name):
                    cfg = {**cfg, **overrides[entry.name]}
                plugin.on_load(self._bus, cfg, self._ctx_for(entry.name, server_config))
                self._loaded[entry.name] = plugin
                self._configs[entry.name] = dict(entry.config)
                loaded.append(plugin)
                self._bus.register_plugin(plugin)
                logger.debug("(PLUGIN-MANAGER) loaded %s", entry.name)
            except Exception:
                logger.exception("(PLUGIN-MANAGER) failed to load %s", entry.name)
        return loaded

    def rescan(self, server_config: dict[str, Any]) -> None:
        directory = self.plugins_directory(server_config)
        if (server_config.get("PLUGINS") or {}).get("master_kill"):
            self.shutdown_all()
            return
        active = {e.name: e for e in topo_sort(scan_plugin_dirs(directory))}
        for name in list(self._loaded):
            if name not in active:
                self._unload_one(name)
        overrides = (server_config.get("PLUGINS") or {}).get("overrides") or {}
        for entry in topo_sort(scan_plugin_dirs(directory)):
            if entry.name not in self._loaded:
                try:
                    plugin = load_plugin_from_entry(entry, directory, overrides=overrides)
                    cfg = plugin_config_for_load(entry.config)
                    if overrides.get(entry.name):
                        cfg = {**cfg, **overrides[entry.name]}
                    plugin.on_load(self._bus, cfg, self._ctx_for(entry.name, server_config))
                    self._loaded[entry.name] = plugin
                    self._configs[entry.name] = dict(entry.config)
                    self._bus.register_plugin(plugin)
                    logger.debug("(PLUGIN-MANAGER) loaded %s", entry.name)
                except Exception:
                    logger.exception("(PLUGIN-MANAGER) failed to load %s", entry.name)
                continue
            new_cfg = entry.config
            if new_cfg != self._configs.get(entry.name):
                plugin = self._loaded[entry.name]
                cfg = plugin_config_for_load(new_cfg)
                if overrides.get(entry.name):
                    cfg = {**cfg, **overrides[entry.name]}
                try:
                    plugin.on_reload(cfg)
                except Exception:
                    logger.exception("(PLUGIN-MANAGER) reload failed for %s", entry.name)
                self._configs[entry.name] = dict(new_cfg)

    def _ctx_for(self, name: str, server_config: dict[str, Any]) -> ServerContext:
        """The shared context, plus ``send_dmrd`` for a plugin granted it in PLUGINS.send.

        The sender re-reads the permission on every frame, so revoking it on SIGHUP
        is immediate; granting it to an already loaded plugin needs that plugin reloaded.
        """
        permission = send_permission(server_config, name) if self._sender_factory is not None else None
        if permission is None:
            return self._ctx
        sender = self._sender_factory(name)
        if permission.group_voice_tgs:
            logger.info(
                "(PLUGIN-MANAGER) %s may send unit data and group voice on TG %s (PLUGINS.send)",
                name, sorted(permission.group_voice_tgs),
            )
            return replace(self._ctx, send_dmrd=sender, voice_slot_for_tg=getattr(sender, "voice_slot_for_tg", None))
        logger.info("(PLUGIN-MANAGER) %s may send unit data (PLUGINS.send)", name)
        return replace(self._ctx, send_dmrd=sender)

    def shutdown_all(self) -> None:
        for name in list(self._loaded):
            self._unload_one(name)

    def _unload_one(self, name: str) -> None:
        plugin = self._loaded.pop(name, None)
        self._configs.pop(name, None)
        if plugin is None:
            return
        self._bus.unregister_plugin(plugin)
        try:
            plugin.on_shutdown()
        except Exception:
            logger.exception("(PLUGIN-MANAGER) shutdown failed for %s", name)
        logger.info("(PLUGIN-MANAGER) unloaded %s", name)
