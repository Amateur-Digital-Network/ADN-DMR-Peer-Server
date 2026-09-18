# ADN DMR Peer Server - plugin loader
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

"""Discover and load drop-in plugins from plugins/<name>/."""

from __future__ import annotations

import importlib.util
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..domain.protocol import ServerPlugin

logger = logging.getLogger(__name__)

_RESERVED_KEYS = frozenset({"enabled", "depends_on", "hot_reload_seconds"})


@dataclass
class PluginEntry:
    name: str
    config_path: Path
    config: dict[str, Any]


def scan_plugin_dirs(directory: Path) -> list[PluginEntry]:
    if not directory.is_dir():
        return []
    entries: list[PluginEntry] = []
    for child in sorted(directory.iterdir()):
        if not child.is_dir():
            continue
        cfg_path = child / "config.yaml"
        if not cfg_path.is_file():
            continue
        try:
            raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("(PLUGIN-LOADER) skip %s: %s", child.name, exc)
            continue
        if not isinstance(raw, dict):
            logger.warning("(PLUGIN-LOADER) skip %s: config not a mapping", child.name)
            continue
        if raw.get("enabled") is not True:
            continue
        entries.append(PluginEntry(name=child.name, config_path=cfg_path, config=raw))
    return entries


def topo_sort(entries: list[PluginEntry]) -> list[PluginEntry]:
    by_name = {e.name: e for e in entries}
    ordered: list[PluginEntry] = []
    seen: set[str] = set()
    visiting: set[str] = set()

    def visit(name: str) -> None:
        if name in seen:
            return
        if name in visiting:
            logger.error("(PLUGIN-LOADER) depends_on cycle at %s", name)
            return
        entry = by_name.get(name)
        if entry is None:
            return
        visiting.add(name)
        deps = entry.config.get("depends_on") or []
        if isinstance(deps, list):
            for dep in deps:
                if isinstance(dep, str):
                    visit(dep)
        visiting.discard(name)
        seen.add(name)
        ordered.append(entry)

    for entry in entries:
        visit(entry.name)
    return ordered


def plugin_config_for_load(raw: dict[str, Any]) -> dict[str, Any]:
    """Strip loader-reserved keys before passing to plugin."""
    return {k: v for k, v in raw.items() if k not in _RESERVED_KEYS}


def import_plugin_module(plugin_dir: Path) -> Any:
    """Load plugins/<name>/plugin/ as a Python package (supports relative imports)."""
    init_py = plugin_dir / "__init__.py"
    if not init_py.is_file():
        raise FileNotFoundError(f"missing {init_py}")
    pkg_name = f"adn_plugin_{plugin_dir.parent.name.replace('-', '_')}"
    cached = sys.modules.get(pkg_name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(
        pkg_name,
        init_py,
        submodule_search_locations=[str(plugin_dir)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load spec for {init_py}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[pkg_name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_plugin_from_entry(
    entry: PluginEntry,
    plugins_root: Path,
    *,
    overrides: dict[str, Any] | None = None,
) -> ServerPlugin:
    plugin_dir = plugins_root / entry.name / "plugin"
    mod = import_plugin_module(plugin_dir)
    create = getattr(mod, "create_plugin", None)
    if not callable(create):
        raise AttributeError(f"{entry.name}: plugin/__init__.py must export create_plugin()")
    plugin = create()
    if not hasattr(plugin, "on_load") or not hasattr(plugin, "on_event"):
        raise TypeError(f"{entry.name}: create_plugin() must return ServerPlugin")
    return plugin
