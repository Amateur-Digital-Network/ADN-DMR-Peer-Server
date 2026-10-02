# ADN DMR Peer Server - SUB_MAP learning
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

"""Where a subscriber was last heard: ``system_learns_sub_map`` is the one place that decides.

Every DMRD on a MASTER/PEER system moves its ``rf_src`` to that system in
``SUB_MAP``, so private calls and unit data addressed to that subscriber are
delivered there. A service system (beacon, ASL/EchoLink/DVSwitch bridge, the
parrot) transmits with someone else's ID, or a shared service ID, and would
"steal" that subscriber's location until they key up again. ``SUB_MAP_LEARN:
false`` on such a system stops it from ever becoming where a subscriber is.

OpenBridge always learns: whoever is behind an OBP link must stay reachable.
"""

from __future__ import annotations

from typing import Any


def system_learns_sub_map(sys_cfg: dict[str, Any] | None) -> bool:
    """True unless the system sets ``SUB_MAP_LEARN: false`` (ignored on OPENBRIDGE)."""
    if not sys_cfg or sys_cfg.get("SUB_MAP_LEARN", True) is not False:
        return True
    return sys_cfg.get("MODE") == "OPENBRIDGE"


def learn_sub_map(
    config: dict[str, Any],
    system_name: str,
    rf_src: bytes,
    slot: int,
    pkt_time: float,
    peer_id: bytes | None,
) -> bool:
    """Record that ``rf_src`` was last heard on ``system_name`` / ``peer_id``.

    Used by the private-call path. The HBP ingress paths (``udp_hbp.py``) cache
    ``system_learns_sub_map`` on the protocol instead, since they run on every
    frame. Returns False when the system does not learn (or there is no SUB_MAP).
    """
    sub_map = config.get("_SUB_MAP")
    if sub_map is None:
        return False
    if not system_learns_sub_map(config.get("SYSTEMS", {}).get(system_name)):
        return False
    sub_map[rf_src] = (system_name, slot, pkt_time, peer_id)
    return True


def purge_non_learning_systems(config: dict[str, Any]) -> int:
    """Drop SUB_MAP entries that point at a system with ``SUB_MAP_LEARN: false``.

    SUB_MAP is persisted (``SUB_MAP_FILE``), so entries learned before a system
    was switched to ``false`` would survive a reload or a restart. Run after
    the config is loaded and after every reload. Returns how many were dropped.
    """
    sub_map = config.get("_SUB_MAP")
    if not sub_map:
        return 0
    systems = config.get("SYSTEMS", {})
    quiet = {name for name, sys_cfg in systems.items() if not system_learns_sub_map(sys_cfg)}
    if not quiet:
        return 0
    stale = [rf_src for rf_src, entry in sub_map.items() if entry and entry[0] in quiet]
    for rf_src in stale:
        del sub_map[rf_src]
    return len(stale)
