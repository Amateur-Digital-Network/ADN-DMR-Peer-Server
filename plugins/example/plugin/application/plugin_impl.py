# ADN DMR Peer Server plugin - example adapter
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

"""ServerPlugin adapter — debug log on every event, JSON file on session end."""

from __future__ import annotations

import logging
from typing import Any

from adn_server.application.plugins.domain.events import (
    UnitDataEnd,
    UnitDataFrame,
    UnitDataStart,
    VoiceCallEnd,
    VoiceCallFrame,
    VoiceCallStart,
)

from ..infrastructure.json_writer import JsonWriter
from .session_use_case import SessionUseCase

logger = logging.getLogger(__name__)


class ExamplePlugin:
    name = "example"

    def __init__(self) -> None:
        self._use_case: SessionUseCase | None = None
        self._writer: JsonWriter | None = None

    def on_load(self, bus: Any, config: dict[str, Any], server_ctx: Any) -> None:
        self._writer = JsonWriter(
            project_root=server_ctx.project_root,
            output_dir=str(config.get("output_dir", "example-events")),
            defer_to_thread=server_ctx.defer_to_thread,
        )
        self._use_case = SessionUseCase(self._writer)
        logger.debug("(EXAMPLE) loaded output_dir=%s", config.get("output_dir"))

    def on_reload(self, config: dict[str, Any]) -> None:
        if self._writer is not None:
            self._writer.set_output_dir(str(config.get("output_dir", "example-events")))
        if self._use_case is not None:
            self._use_case.shutdown()
        logger.debug("(EXAMPLE) reloaded output_dir=%s", config.get("output_dir"))

    def on_shutdown(self) -> None:
        if self._use_case is not None:
            self._use_case.shutdown()
        logger.debug("(EXAMPLE) shutdown")

    def on_event(self, event: object) -> None:
        if self._use_case is None:
            return
        if isinstance(event, VoiceCallStart):
            meta = event.context.to_metadata_dict()
            logger.debug("(EXAMPLE) VoiceCallStart stream_id=%s", meta.get("stream_id"))
            self._use_case.on_start("voice", meta)
        elif isinstance(event, VoiceCallFrame):
            meta = event.context.to_metadata_dict()
            logger.debug(
                "(EXAMPLE) VoiceCallFrame stream_id=%s frame_type=%s",
                meta.get("stream_id"),
                event.frame_type,
            )
            self._use_case.on_frame(meta)
        elif isinstance(event, VoiceCallEnd):
            meta = event.context.to_metadata_dict()
            logger.debug(
                "(EXAMPLE) VoiceCallEnd stream_id=%s duration_s=%.3f frames=%s",
                meta.get("stream_id"),
                event.duration_s,
                event.frame_count,
            )
            self._use_case.on_end(
                "voice",
                meta,
                duration_s=event.duration_s,
                count=event.frame_count,
            )
        elif isinstance(event, UnitDataStart):
            meta = event.context.to_metadata_dict()
            logger.debug(
                "(EXAMPLE) UnitDataStart stream_id=%s label=%s",
                meta.get("stream_id"),
                event.data_label,
            )
            self._use_case.on_start("unit_data", meta)
        elif isinstance(event, UnitDataFrame):
            meta = event.context.to_metadata_dict()
            logger.debug(
                "(EXAMPLE) UnitDataFrame stream_id=%s seq=%s",
                meta.get("stream_id"),
                event.seq,
            )
            self._use_case.on_frame(meta)
        elif isinstance(event, UnitDataEnd):
            meta = event.context.to_metadata_dict()
            logger.debug(
                "(EXAMPLE) UnitDataEnd stream_id=%s duration_s=%.3f packets=%s",
                meta.get("stream_id"),
                event.duration_s,
                event.packet_count,
            )
            self._use_case.on_end(
                "unit_data",
                meta,
                duration_s=event.duration_s,
                count=event.packet_count,
            )
