# ADN DMR Peer Server plugin - voice-announcements schedule
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

"""Scheduled announcements and TTS items, read from the server's ``VOICE`` section.

The same keys the core used (adn-voice.yaml), so existing installs keep working.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from adn_server.application.server_voice import announcement_item_dmr_id

FILE = "file"
TTS = "tts"


@dataclass(frozen=True)
class Item:
    kind: str  # FILE (pre-recorded .ambe) or TTS (.txt spoken, cached as .ambe)
    index: int
    file: str
    tg: int
    language: str
    mode: str  # "interval" or "hourly"
    interval_s: float
    dmr_id: int
    raw: dict[str, Any]  # the item as configured: TTS settings live here too

    @property
    def label(self) -> str:
        return f"{'ANNOUNCEMENT' if self.kind == FILE else 'TTS'}-{self.index + 1}"

    @property
    def key(self) -> tuple[str, int]:
        return (self.kind, self.index)

    @property
    def tick_s(self) -> float:
        """How often the item's timer fires: hourly items check the clock every 30 s."""
        return 30.0 if self.mode == "hourly" else self.interval_s


def enabled_items(config: dict[str, Any]) -> list[Item]:
    """Enabled, playable items of ``VOICE.ANNOUNCEMENTS`` and ``VOICE.TTS_ANNOUNCEMENTS``."""
    voice = config.get("VOICE") or {}
    items: list[Item] = []
    for kind, section in ((FILE, "ANNOUNCEMENTS"), (TTS, "TTS_ANNOUNCEMENTS")):
        entries = voice.get(section) or []
        if not isinstance(entries, list):
            continue
        for index, raw in enumerate(entries):
            if not isinstance(raw, dict) or not raw.get("ENABLED"):
                continue
            try:
                tg = int(raw.get("TG", 0))
                interval = float(raw.get("INTERVAL", 60))
            except (TypeError, ValueError):
                continue
            file = str(raw.get("FILE") or "").strip()
            if not file or not tg or interval <= 0:
                continue
            items.append(
                Item(
                    kind=kind,
                    index=index,
                    file=file,
                    tg=tg,
                    language=str(raw.get("LANGUAGE", "en_GB")),
                    mode=str(raw.get("MODE", "interval")),
                    interval_s=interval,
                    dmr_id=announcement_item_dmr_id(raw, config),
                    raw=dict(raw),
                )
            )
    return items


def hourly_due(now: datetime, last_hour: int | None) -> bool:
    """Hourly items play once, in the first minute of each hour."""
    return now.minute == 0 and last_hour != now.hour
