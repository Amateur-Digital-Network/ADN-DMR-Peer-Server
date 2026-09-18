# ADN DMR Peer Server plugin - example record builder
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

"""Pure helpers to build JSON records at session end."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def session_key(meta: dict[str, Any]) -> tuple[str, int]:
    return (str(meta.get("origin_system") or ""), int(meta.get("stream_id") or 0))


def _iso_utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def build_end_record(
    *,
    kind: str,
    start_meta: dict[str, Any],
    end_meta: dict[str, Any],
    duration_s: float,
    count: int,
    started_at: float,
    ended_at: float,
) -> dict[str, Any]:
    """Flat JSON document written when a voice or unit-data session ends."""
    out = {**start_meta, **end_meta}
    out["event_kind"] = kind
    out["duration_s"] = duration_s
    out["started_at"] = _iso_utc(started_at)
    out["ended_at"] = _iso_utc(ended_at)
    out["pkt_time"] = float(end_meta.get("pkt_time") or ended_at)
    if kind == "voice":
        out["frame_count"] = count
    else:
        out["packet_count"] = count
    return out
