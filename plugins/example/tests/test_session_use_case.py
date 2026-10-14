# ADN DMR Peer Server plugin - example session tests
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

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from plugin.application.session_use_case import SessionUseCase
from plugin.infrastructure.json_writer import JsonWriter


class _SyncWriter:
    def __init__(self) -> None:
        self.records: list[tuple[int, dict]] = []

    def write_record(self, stream_id: int, record: dict) -> None:
        self.records.append((stream_id, record))


def test_voice_session_end_writes_record() -> None:
    sink = _SyncWriter()
    uc = SessionUseCase(sink)  # type: ignore[arg-type]
    meta = {"origin_system": "T1", "stream_id": 99, "src_id": 1, "pkt_time": 10.0}
    uc.on_start("voice", meta)
    uc.on_frame(meta)
    uc.on_frame(meta)
    uc.on_end("voice", {**meta, "dst_id": 4000}, duration_s=2.5, count=2)
    assert len(sink.records) == 1
    sid, rec = sink.records[0]
    assert sid == 99
    assert rec["event_kind"] == "voice"
    assert rec["frame_count"] == 2
    assert rec["duration_s"] == 2.5


def test_json_writer_delegates_to_thread(tmp_path: Path) -> None:
    written: list[Path] = []

    def defer(fn, *args):  # noqa: ANN001
        fn(*args)
        written.append(tmp_path / "example-events" / f"{args[0]}.json")

    writer = JsonWriter(
        project_root=str(tmp_path),
        output_dir="example-events",
        defer_to_thread=defer,
    )
    writer.write_record(7, {"stream_id": 7, "event_kind": "voice"})
    assert len(written) == 1
    assert written[0].is_file()
