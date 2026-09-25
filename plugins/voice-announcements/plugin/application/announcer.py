# ADN DMR Peer Server plugin - voice-announcements announcer
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

"""Plays scheduled announcements and TTS on their talkgroups through ``send_dmrd``.

Everything runs on the reactor (``call_later``); only TTS encoding goes to a thread.
Behaviour follows the core announcements it replaces: one playback per talkgroup at a
time, retries while the talkgroup or every slot is busy, 58 ms per frame, and the
playback stops as soon as the server refuses a frame (a radio took the slot).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Any, Callable

from adn_server.domain import bytes_3
from adn_server.domain.mesh_engine import server_id_bytes

from ..domain.schedule import FILE, Item, enabled_items, hourly_due
from ..domain.tg_queue import TalkgroupQueue
from ..infrastructure.tts_engine import ensure_tts_ambe

logger = logging.getLogger(__name__)

FRAME_INTERVAL_S = 0.058
START_DELAY_S = 0.5
GAP_BETWEEN_PLAYBACKS_S = 1.5
TG_BUSY_RETRY_S = 3.0
SLOT_BUSY_RETRY_S = 5.0
MAX_RETRIES = 60


class Announcer:
    def __init__(
        self,
        ctx: Any,
        voice: Any,
        tts: Callable[[dict[str, Any], dict[str, Any], str], str | None] | None = None,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._ctx = ctx
        self._voice = voice
        self._tts = tts if tts is not None else ensure_tts_ambe
        self._now = now
        self._items: dict[tuple[str, int], Item] = {}
        self._timers: dict[tuple[str, int], Any] = {}
        self._running: set[tuple[str, int]] = set()
        self._last_hour: dict[tuple[str, int], int] = {}
        self._queue: TalkgroupQueue[tuple[Item, list]] = TalkgroupQueue()
        self._stopped = False

    # --- schedule -------------------------------------------------------------

    def reconcile(self) -> None:
        """Start, restart or stop item timers to match the current ``VOICE`` config."""
        wanted = {item.key: item for item in enabled_items(self._ctx.config)}
        for key in list(self._timers):
            if key not in wanted or wanted[key] != self._items.get(key):
                self._cancel(key)
                logger.info("(VOICE-RELOAD) %s stopped", self._items[key].label)
                del self._items[key]
        for key, item in wanted.items():
            if key in self._timers:
                continue
            self._items[key] = item
            self._timers[key] = self._ctx.call_later(item.tick_s, self._tick, key)
            logger.info(
                "(VOICE-RELOAD) %s enabled - mode: %s, file: %s, TG: %s", item.label, item.mode, item.file, item.tg
            )

    def shutdown(self) -> None:
        self._stopped = True
        for key in list(self._timers):
            self._cancel(key)

    def _cancel(self, key: tuple[str, int]) -> None:
        timer = self._timers.pop(key, None)
        try:
            if timer is not None and timer.active():
                timer.cancel()
        except Exception:  # already fired
            pass

    def _tick(self, key: tuple[str, int]) -> None:
        item = self._items.get(key)
        if item is None or self._stopped:
            return
        self._timers[key] = self._ctx.call_later(item.tick_s, self._tick, key)
        self.fire(item)

    # --- one playback ---------------------------------------------------------

    def fire(self, item: Item, retry: int = 0) -> None:
        if item.key in self._running:
            if retry == 0:
                logger.debug("(%s) Previous playback still running, skipping", item.label)
            return
        if item.mode == "hourly" and retry == 0 and not hourly_due(self._now(), self._last_hour.get(item.key)):
            return
        if self._queue.on_air(item.tg) and retry < MAX_RETRIES:
            if retry == 0:
                logger.debug("(%s) Same TG %s already broadcasting, deferring", item.label, item.tg)
            self._ctx.call_later(TG_BUSY_RETRY_S + item.index * 0.5, self.fire, item, retry + 1)
            return
        self._running.add(item.key)
        if item.mode == "hourly":
            self._last_hour[item.key] = self._now().hour
        if item.kind == FILE:
            self._load(item)
            return
        logger.info("(%s) Starting TTS conversion in background thread for %s", item.label, item.file)
        d = self._ctx.defer_to_thread(self._tts, self._ctx.config, item.raw, self._audio_path())
        d.addCallback(self._tts_ready, item)
        d.addErrback(self._tts_failed, item)

    def _tts_ready(self, ambe_path: str | None, item: Item) -> None:
        if not ambe_path:
            logger.warning("(%s) No AMBE file available for TTS announcement %s", item.label, item.file)
            self._running.discard(item.key)
            return
        self._load(item)

    def _tts_failed(self, failure: Any, item: Item) -> None:
        self._running.discard(item.key)
        message = failure.getErrorMessage() if hasattr(failure, "getErrorMessage") else str(failure)
        logger.error("(%s) TTS conversion error: %s", item.label, message)

    def _load(self, item: Item) -> None:
        name = item.file[:-5] if item.file.endswith(".ambe") else item.file
        try:
            ambe = self._voice.read_single_file(self._audio_path(), item.language, name)
        except Exception as e:
            ambe = None
            logger.warning("(%s) Cannot read AMBE file %s/ondemand/%s.ambe: %s", item.label, item.language, name, e)
        if not ambe:
            logger.warning("(%s) AMBE file empty or not found: %s/ondemand/%s.ambe", item.label, item.language, name)
            self._running.discard(item.key)
            return
        if self._queue.request(item.tg, (item, ambe)):
            self._begin(item, ambe)
        else:
            logger.info("(%s) Same TG %s still on air; deferring next playback (pending %s)", item.label, item.tg, len(self._queue))

    def _begin(self, item: Item, ambe: list, retry: int = 0) -> None:
        """Holds the TG in the queue while it waits for a free slot."""
        if self._stopped:
            return
        slot_for_tg = getattr(self._ctx, "voice_slot_for_tg", None)
        slot = slot_for_tg(item.tg) if slot_for_tg is not None else None
        if slot is None:
            if slot_for_tg is not None and retry < MAX_RETRIES:
                if retry == 0:
                    logger.info("(%s) All target slots busy (QSO active), waiting for QSO to finish...", item.label)
                self._ctx.call_later(SLOT_BUSY_RETRY_S, self._begin, item, ambe, retry + 1)
                return
            logger.warning("(%s) No slot to speak TG %s on; not played", item.label, item.tg)
            self._finish(item)
            return
        pkts = list(self._voice.pkt_gen(bytes_3(item.dmr_id), bytes_3(item.tg), self._server_id(), 1 if slot == 2 else 0, [ambe]))
        logger.info("(%s) Playing %s to TG %s on TS%s (%s packets)", item.label, item.file, item.tg, slot, len(pkts))
        self._ctx.call_later(START_DELAY_S, self._play, item, pkts, 0, None)

    def _play(self, item: Item, pkts: list[bytes], idx: int, next_time: float | None) -> None:
        if self._stopped:
            return
        if idx >= len(pkts):
            logger.info("(%s) Broadcast complete: %s packets", item.label, len(pkts))
            self._finish(item)
            return
        if not self._ctx.send_dmrd(pkts[idx]):
            logger.info("(%s) Broadcast stopped at packet %s/%s: slot taken (QSO)", item.label, idx, len(pkts))
            self._finish(item)
            return
        next_time = (time.time() if next_time is None else next_time) + FRAME_INTERVAL_S
        self._ctx.call_later(max(0.001, next_time - time.time()), self._play, item, pkts, idx + 1, next_time)

    def _finish(self, item: Item) -> None:
        self._running.discard(item.key)
        nxt = self._queue.finished(item.tg)
        if nxt is not None:
            self._ctx.call_later(GAP_BETWEEN_PLAYBACKS_S, self._begin, *nxt)

    # --- helpers ----------------------------------------------------------------

    def _audio_path(self) -> str:
        return os.path.join(self._ctx.project_root, (self._ctx.config.get("VOICE") or {}).get("AUDIO_PATH", "Audio"))

    def _server_id(self) -> bytes:
        return server_id_bytes(self._ctx.config.get("GLOBAL", {}).get("SERVER_ID"))[:4]
