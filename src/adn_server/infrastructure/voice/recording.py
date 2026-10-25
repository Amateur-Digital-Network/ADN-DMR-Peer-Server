# ADN DMR Peer Server - voice recording (legacy _handleRecording, _saveRecording)
# Copyright (C) 2026  Rodrigo Pérez, CE5RPY <ce5rpy@qmd.cl>
#
# Derived from ADN DMR Server / FreeDMR  / HBlink. Original license:
###############################################################################
# Copyright (C) 2020 Simon Adlem, G7RZU <g7rzu@gb7fr.org.uk>
# Copyright (C) 2016-2019 Cortney T. Buffington, N0MJS <n0mjs@me.com>
#
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

"""Record DMR voice to AMBE file when RECORDING_ENABLED and packet matches RECORDING_TG / RECORDING_TIMESLOT."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from bitarray import bitarray

from ...domain.dmr.frames import voice_bits

logger = logging.getLogger(__name__)

RECORDING_MAX_FRAMES = 2750


class RecordingHandler:
    """Legacy _handleRecording + _saveRecording: accumulate voice bursts, save to Audio/{lang}/ondemand/{file}.ambe."""

    def __init__(self, config: dict[str, Any], project_root: str) -> None:
        self._config = config
        self._project_root = project_root
        self._active = False
        self._stream_id: bytes | None = None
        self._bursts = bitarray(endian="big")
        self._start_time = 0.0
        self._frames = 0
        self._rf_src: bytes | None = None

    def handle_recording(
        self,
        dmrpkt: bytes,
        frame_type: int,
        dtype_vseq: int,
        stream_id: bytes,
        pkt_time: float,
        rf_src: bytes,
        int_dst_id: int,
        slot: int,
    ) -> None:
        """Legacy _handleRecording: accumulate voice or start/end stream."""
        g = self._config.get("VOICE", {})
        if not g.get("RECORDING_ENABLED"):
            return
        if int_dst_id != g.get("RECORDING_TG", 0) or slot != g.get("RECORDING_TIMESLOT", 2):
            return
        if frame_type == 2 and dtype_vseq == 1:  # HBPF_DATA_SYNC, HBPF_SLT_VHEAD
            if self._active and self._stream_id != stream_id:
                logger.info("(RECORDING) New transmission detected, saving previous recording (%d frames)", self._frames)
                self._save_recording()
            self._active = True
            self._stream_id = stream_id
            self._bursts = bitarray(endian="big")
            self._start_time = pkt_time
            self._frames = 0
            self._rf_src = rf_src
            logger.info("(RECORDING) Recording started - SUB: %s, TG: %s, TS: %s", int.from_bytes(rf_src, "big") if rf_src else 0, int_dst_id, slot)
            return
        if not self._active or self._stream_id != stream_id:
            return
        if frame_type in (0, 1):  # HBPF_VOICE, HBPF_VOICE_SYNC
            if len(dmrpkt) >= 33:
                self._bursts.extend(voice_bits(dmrpkt))
            self._frames += 1
            if self._frames >= RECORDING_MAX_FRAMES:
                logger.info("(RECORDING) Max duration reached (%d frames), saving", self._frames)
                self._save_recording()
            return
        if frame_type == 2 and dtype_vseq == 2:  # HBPF_SLT_VTERM
            self._save_recording()
            return

    def _save_recording(self) -> None:
        """Legacy _saveRecording: write bursts to Audio/{lang}/ondemand/{file}.ambe."""
        if not self._active or self._frames == 0:
            self._active = False
            self._stream_id = None
            logger.warning("(RECORDING) No frames recorded, discarding")
            return
        g = self._config.get("VOICE", {})
        lang = g.get("RECORDING_LANGUAGE", "en_GB")
        file_name = g.get("RECORDING_FILE", "recording")
        audio_path = os.path.join(self._project_root, g.get("AUDIO_PATH", "Audio"))
        out_dir = os.path.join(audio_path, lang, "ondemand")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, file_name + ".ambe")
        try:
            with open(path, "wb") as f:
                f.write(self._bursts.tobytes())
        except OSError as e:
            logger.warning("(RECORDING) Could not save: %s", e)
            self._active = False
            self._stream_id = None
            self._bursts = bitarray(endian="big")
            self._frames = 0
            self._rf_src = None
            return
        duration = time.time() - self._start_time
        _id = int.from_bytes(self._rf_src, "big") if self._rf_src else 0
        logger.info("(RECORDING) Recording saved: %s (%d frames, %.1f seconds, SUB: %s)", path, self._frames, duration, _id)
        self._active = False
        self._stream_id = None
        self._bursts = bitarray(endian="big")
        self._frames = 0
        self._rf_src = None
