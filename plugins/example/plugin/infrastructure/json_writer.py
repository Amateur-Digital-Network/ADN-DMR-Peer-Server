# ADN DMR Peer Server plugin - example JSON writer
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

"""Write session JSON files off the reactor thread."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


class JsonWriter:
    def __init__(
        self,
        *,
        project_root: str,
        output_dir: str,
        defer_to_thread: Callable[..., Any],
    ) -> None:
        self._root = Path(project_root)
        self._output_dir = output_dir
        self._defer = defer_to_thread

    def set_output_dir(self, output_dir: str) -> None:
        self._output_dir = output_dir

    def write_record(self, stream_id: int, record: dict[str, Any]) -> None:
        self._defer(self._write_sync, stream_id, record)

    def _write_sync(self, stream_id: int, record: dict[str, Any]) -> None:
        out_dir = self._root / self._output_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{stream_id}.json"
        path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        logger.debug("(EXAMPLE) wrote %s", path)
