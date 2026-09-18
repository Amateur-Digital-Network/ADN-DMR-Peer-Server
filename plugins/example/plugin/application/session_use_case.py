# ADN DMR Peer Server plugin - example session use case
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

"""Track voice/data sessions and emit JSON at end."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..domain.record import build_end_record, session_key
from ..infrastructure.json_writer import JsonWriter


@dataclass
class _Session:
    kind: str
    meta: dict[str, Any]
    started_at: float
    count: int = 0


class SessionUseCase:
    def __init__(self, writer: JsonWriter) -> None:
        self._writer = writer
        self._active: dict[tuple[str, int], _Session] = {}

    def on_start(self, kind: str, meta: dict[str, Any]) -> None:
        key = session_key(meta)
        self._active[key] = _Session(
            kind=kind,
            meta=dict(meta),
            started_at=float(meta.get("pkt_time") or time.time()),
        )

    def on_frame(self, meta: dict[str, Any]) -> None:
        key = session_key(meta)
        session = self._active.get(key)
        if session is not None:
            session.count += 1

    def on_end(
        self,
        kind: str,
        end_meta: dict[str, Any],
        *,
        duration_s: float,
        count: int | None = None,
    ) -> None:
        key = session_key(end_meta)
        session = self._active.pop(key, None)
        if session is None:
            started_at = float(end_meta.get("pkt_time") or time.time()) - duration_s
            start_meta = dict(end_meta)
            frame_count = count if count is not None else 0
        else:
            started_at = session.started_at
            start_meta = session.meta
            frame_count = count if count is not None else session.count

        ended_at = float(end_meta.get("pkt_time") or time.time())
        record = build_end_record(
            kind=kind,
            start_meta=start_meta,
            end_meta=end_meta,
            duration_s=duration_s,
            count=frame_count,
            started_at=started_at,
            ended_at=ended_at,
        )
        stream_id = int(end_meta.get("stream_id") or 0)
        self._writer.write_record(stream_id, record)

    def shutdown(self) -> None:
        self._active.clear()
