# ADN DMR Peer Server - application trunking config
#
# Copyright (C) 2026  Rodrigo Pérez, CE5RPY <ce5rpy@qmd.cl>
# Copyright (C) 2026  Joaquin Madrid Belando, EA5GVK <ea5gvk@gmail.com>
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

"""Which systems serve Tier III sites: ``TRUNKING: true`` on a MASTER, and nothing else."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def system_is_trunking(sys_cfg: Mapping[str, Any] | None) -> bool:
    return sys_cfg is not None and sys_cfg.get("MODE") == "MASTER" and sys_cfg.get("TRUNKING") is True


def trunk_systems(systems: Mapping[str, Any]) -> frozenset[str]:
    return frozenset(name for name, cfg in systems.items() if cfg.get("ENABLED", True) and system_is_trunking(cfg))
