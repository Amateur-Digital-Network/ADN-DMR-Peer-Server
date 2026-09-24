# ADN DMR Peer Server plugin - voice-announcements adapter
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

"""ServerPlugin adapter: scheduled announcements, TTS and voice beacons."""

from __future__ import annotations

import logging
from typing import Any

from adn_server.infrastructure.voice import DefaultVoiceProvider

from .announcer import Announcer

logger = logging.getLogger(__name__)


class VoiceAnnouncementsPlugin:
    name = "voice-announcements"
    events = ()  # listens to nothing: it only speaks

    def __init__(self) -> None:
        self._announcer: Announcer | None = None
        self._ctx: Any = None
        self._reconcile_s = 15.0
        self._timer: Any = None

    def on_load(self, bus: Any, config: dict[str, Any], server_ctx: Any) -> None:
        del bus
        self._ctx = server_ctx
        self._reconcile_s = float(config.get("reconcile_s", 15))
        if server_ctx.send_dmrd is None or getattr(server_ctx, "voice_slot_for_tg", None) is None:
            logger.warning(
                "(VOICE) %s is not allowed to send group voice: add it to PLUGINS.send with "
                "allowed_src_ids and group_voice_tgs; announcements will not play",
                self.name,
            )
            return
        self._announcer = Announcer(server_ctx, DefaultVoiceProvider())
        self._reconcile()

    def _reconcile(self) -> None:
        if self._announcer is None:
            return
        self._announcer.reconcile()
        # The core re-reads adn-voice.yaml every 15 s into config["VOICE"]; follow it.
        self._timer = self._ctx.call_later(self._reconcile_s, self._reconcile)

    def on_reload(self, config: dict[str, Any]) -> None:
        self._reconcile_s = float(config.get("reconcile_s", self._reconcile_s))

    def on_shutdown(self) -> None:
        if self._announcer is not None:
            self._announcer.shutdown()
            self._announcer = None
        try:
            if self._timer is not None and self._timer.active():
                self._timer.cancel()
        except Exception:
            pass

    def on_event(self, event: object) -> None:
        pass
