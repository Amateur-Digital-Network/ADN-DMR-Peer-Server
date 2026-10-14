# ADN DMR Peer Server - runtime context holder
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

"""Runtime config container and atomic pointer swap on SIGHUP reload."""

from __future__ import annotations

import copy
from collections.abc import Iterator, MutableMapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RuntimeContext:
    """Live server configuration and related metadata."""

    config: dict[str, Any]
    config_path: str = ""
    subscription_store: Any = None


class RuntimeContextHolder:
    """Thread-local holder for the active RuntimeContext (swap on SIGHUP reload)."""

    def __init__(self, initial: RuntimeContext) -> None:
        self._ctx = initial

    def get(self) -> RuntimeContext:
        return self._ctx

    def swap(self, ctx: RuntimeContext) -> RuntimeContext:
        """Replace the active context; returns the previous context."""
        previous = self._ctx
        self._ctx = ctx
        return previous


class ConfigProxy(MutableMapping[str, Any]):
    """Dict-like view that always reads/writes the holder's current config dict."""

    def __init__(self, holder: RuntimeContextHolder) -> None:
        self._holder = holder

    def _cfg(self) -> dict[str, Any]:
        return self._holder.get().config

    def __getitem__(self, key: str) -> Any:
        return self._cfg()[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._cfg()[key] = value

    def __delitem__(self, key: str) -> None:
        del self._cfg()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._cfg())

    def __len__(self) -> int:
        return len(self._cfg())

    def __contains__(self, key: object) -> bool:
        return key in self._cfg()

    def get(self, key: str, default: Any = None) -> Any:
        return self._cfg().get(key, default)

    def setdefault(self, key: str, default: Any = None) -> Any:
        return self._cfg().setdefault(key, default)

    def pop(self, key: str, *default: Any) -> Any:
        return self._cfg().pop(key, *default)

    def keys(self) -> Any:
        return self._cfg().keys()

    def values(self) -> Any:
        return self._cfg().values()

    def items(self) -> Any:
        return self._cfg().items()

    def update(self, *args: Any, **kwargs: Any) -> None:
        self._cfg().update(*args, **kwargs)


def prepare_reload_config(holder: RuntimeContextHolder) -> dict[str, Any]:
    """
    Build a working copy for SIGHUP reload.

    The live ``_SUB_MAP`` and ``_MESH_SESSIONS`` objects are shared, not copied:
    subscriber state and OpenBridge sessions survive the reload, and the readers
    that hold a reference to the store keep looking at the live one.
    On failure the holder is unchanged; on success call ``swap`` with the merged dict.
    """
    live = holder.get().config
    shared = {key: live.get(key) for key in ("_SUB_MAP", "_MESH_SESSIONS")}
    new_config = copy.deepcopy(live)
    for key, value in shared.items():
        if value is not None:
            new_config[key] = value
    return new_config


def swap_runtime_config(
    holder: RuntimeContextHolder,
    new_config: dict[str, Any],
    *,
    config_path: str | None = None,
) -> RuntimeContext:
    """Atomically install a reloaded config dict."""
    previous = holder.get()
    path = config_path if config_path is not None else previous.config_path
    return holder.swap(
        RuntimeContext(
            config=new_config,
            config_path=path,
            subscription_store=previous.subscription_store,
        )
    )
