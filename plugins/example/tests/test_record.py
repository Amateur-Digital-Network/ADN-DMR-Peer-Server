# ADN DMR Peer Server plugin - example tests
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

from plugin.domain.record import build_end_record, session_key


def test_session_key() -> None:
    assert session_key({"origin_system": "SYS1", "stream_id": 42}) == ("SYS1", 42)


def test_build_voice_end_record() -> None:
    start = {"origin_system": "SYS1", "stream_id": 1, "src_id": 100, "pkt_time": 1000.0}
    end = {"origin_system": "SYS1", "stream_id": 1, "dst_id": 200, "pkt_time": 1005.0}
    rec = build_end_record(
        kind="voice",
        start_meta=start,
        end_meta=end,
        duration_s=5.0,
        count=12,
        started_at=1000.0,
        ended_at=1005.0,
    )
    assert rec["event_kind"] == "voice"
    assert rec["frame_count"] == 12
    assert rec["duration_s"] == 5.0
    assert rec["src_id"] == 100
    assert rec["dst_id"] == 200
    assert rec["started_at"].endswith("Z")
    assert rec["ended_at"].endswith("Z")
