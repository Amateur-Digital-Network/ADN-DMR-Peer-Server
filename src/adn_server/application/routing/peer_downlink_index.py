# ADN DMR Peer Server - application routing peer downlink index
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

"""Inject-only MASTER downlink: narrow peer fan-out by (slot, TGID).

Legacy ``send_peers`` scans every registered peer per packet. On inject-only
proxies with hundreds of hotspots, that is O(peers × pkt/s). This index
builds a candidate set from static OPTIONS and UA session state; each
candidate is still checked with :func:`peer_should_receive_group_voice`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ...domain import bytes_4, int_id
from .helpers import peer_single_exclusive_tgid


def invalidate_peer_options_cache(peer: dict[str, Any]) -> None:
    """Drop cached OPTIONS parses after RPTO."""
    peer.pop("_CACHED_OPTIONS_STATIC", None)
    peer.pop("_CACHED_OPTIONS_FIELDS", None)


def cached_peer_static_tgs(peer: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Memoize ``parse_peer_options_static`` per peer OPTIONS blob."""
    opts = peer.get("OPTIONS")
    key = opts if isinstance(opts, bytes) else b""
    cached = peer.get("_CACHED_OPTIONS_STATIC")
    if cached and cached[0] == key:
        return cached[1], cached[2]
    from adn_server.application.report.payloads import parse_peer_options_static

    ts1, ts2 = parse_peer_options_static(opts)
    t1, t2 = tuple(ts1), tuple(ts2)
    peer["_CACHED_OPTIONS_STATIC"] = (key, t1, t2)
    return t1, t2


def count_connected_peers(peers: dict[bytes, dict[str, Any]]) -> int:
    return sum(1 for p in peers.values() if p.get("CONNECTION") == "YES")


@dataclass
class PeerDownlinkIndex:
    """Precomputed (slot, TGID) → peer candidates for connected hotspots."""

    static_by_slot_tgid: dict[tuple[int, int], frozenset[bytes]] = field(default_factory=dict)
    ua_by_slot_tgid: dict[tuple[int, int], frozenset[bytes]] = field(default_factory=dict)
    connected: frozenset[bytes] = frozenset()

    def candidates(self, slot: int, tgid: int, *, connected_count: int) -> frozenset[bytes]:
        if connected_count == 1:
            return self.connected
        voice_slot = int(slot)
        other_slot = 3 - voice_slot
        tg = int(tgid)
        out: set[bytes] = set()
        for ts in (voice_slot, other_slot):
            key = (ts, tg)
            out.update(self.static_by_slot_tgid.get(key, ()))
            out.update(self.ua_by_slot_tgid.get(key, ()))
        return frozenset(out)


def _add_index_entry(
    index: dict[tuple[int, int], set[bytes]],
    slot: int,
    tgid: int,
    peer_id: bytes,
) -> None:
    try:
        tgid_i = int(tgid)
    except (TypeError, ValueError):
        return
    if tgid_i <= 0:
        return
    index.setdefault((int(slot), tgid_i), set()).add(peer_id)


def build_peer_downlink_index(
    peers: dict[bytes, dict[str, Any]],
    sys_cfg: dict[str, Any],
    *,
    now: float | None = None,
) -> PeerDownlinkIndex:
    """Rebuild candidate map from all connected peers (call when index is dirty)."""
    pkt_time = time.time() if now is None else now
    static_map: dict[tuple[int, int], set[bytes]] = {}
    ua_map: dict[tuple[int, int], set[bytes]] = {}
    connected: set[bytes] = set()

    for peer_id, peer in peers.items():
        if peer.get("CONNECTION") != "YES":
            continue
        connected.add(peer_id)
        ts1, ts2 = cached_peer_static_tgs(peer)
        for tg in ts1:
            _add_index_entry(static_map, 1, tg, peer_id)
        for tg in ts2:
            _add_index_entry(static_map, 2, tg, peer_id)

        for slot in (1, 2):
            locked = peer_single_exclusive_tgid(
                peer, slot, sys_cfg, peer_id=peer_id, now=pkt_time,
            )
            if locked is not None:
                _add_index_entry(ua_map, slot, locked, peer_id)

        sessions = peer.get("_UA_SESSION")
        if isinstance(sessions, dict):
            for slot, entry in sessions.items():
                if not isinstance(entry, dict):
                    continue
                if pkt_time >= float(entry.get("expires", 0)):
                    continue
                locked = entry.get("tgid")
                if locked is not None:
                    _add_index_entry(ua_map, int(slot), locked, peer_id)

    multi_store = sys_cfg.get("_PEER_UA_MULTI_TGS")
    if isinstance(multi_store, dict):
        for pk, per_slot in multi_store.items():
            if not isinstance(per_slot, dict):
                continue
            peer_id = pk if isinstance(pk, bytes) else bytes_4(int_id(pk))
            if peer_id not in connected:
                continue
            for slot, tg_set in per_slot.items():
                if not isinstance(tg_set, set):
                    continue
                for tgid in tg_set:
                    _add_index_entry(ua_map, int(slot), tgid, peer_id)

    ua_sessions = sys_cfg.get("_PEER_UA_SESSIONS")
    if isinstance(ua_sessions, dict):
        for pk, per_slot in ua_sessions.items():
            if not isinstance(per_slot, dict):
                continue
            peer_id = pk if isinstance(pk, bytes) else bytes_4(int_id(pk))
            if peer_id not in connected:
                continue
            for slot, entry in per_slot.items():
                if not isinstance(entry, dict):
                    continue
                if pkt_time >= float(entry.get("expires", 0)):
                    continue
                locked = entry.get("tgid")
                if locked is not None:
                    _add_index_entry(ua_map, int(slot), locked, peer_id)

    return PeerDownlinkIndex(
        static_by_slot_tgid={k: frozenset(v) for k, v in static_map.items()},
        ua_by_slot_tgid={k: frozenset(v) for k, v in ua_map.items()},
        connected=frozenset(connected),
    )
