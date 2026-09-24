# ADN DMR Peer Server - AMBE reader and default voice provider
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

"""AMBE word loading (readAMBE) and VoiceProvider that uses it + pkt_gen."""

from __future__ import annotations

import glob
import logging
import os
from itertools import islice
from pathlib import Path
from typing import Any, Iterator

from bitarray import bitarray

from ...application.ports import VoiceProvider
from .pkt_gen import pkt_gen as _pkt_gen
from .voice_map import VOICE_MAP

logger = logging.getLogger(__name__)

_AMBE_LENGTH = 9

# Silence burst pair (legacy default when no .ambe available)
SILENCE_PAIR = [
    bitarray("101011000000101010100000010000000000001000000000000000000000010001000000010000000000100000000000100000000000"),
    bitarray("001010110000001010101000000100000000000010000000000000000000000100010000000100000000001000000000001000000000"),
]


def _make_bursts(data: bitarray):
    """Yield 108-bit bursts from bitarray (legacy _make_bursts)."""
    it = iter(data)
    n = len(data)
    for i in range(0, n, 108):
        chunk = bitarray([k for k in islice(it, 108)])
        if len(chunk) < 108:
            chunk.extend([False] * (108 - len(chunk)))
        yield chunk


class ReadAMBE:
    """Legacy readAMBE: load AMBE words by language from path (dir of .ambe or .indx+.ambe)."""

    def __init__(self, lang: str, path: str | Path) -> None:
        self.langcsv = lang
        self.langs = [s.strip() for s in lang.split(",") if s.strip()]
        self.path = Path(path) if not isinstance(path, Path) else path

    def readfiles(self) -> dict[str, dict[str, list[list[bitarray]]]]:
        """Load words per language. Returns {lang: {voice_name: [[b0,b1], ...]}}."""
        result: dict[str, dict[str, list[list[bitarray]]]] = {}
        for _lang in self.langs:
            _prefix = self.path / _lang
            _wordBADict: dict[str, list[list[bitarray]]] = {}
            if _prefix.is_dir():
                for ambe_path in glob.glob(str(_prefix / "*.ambe")):
                    basename = os.path.basename(ambe_path)
                    voice_name, _ = basename.split(".", 1)
                    try:
                        with open(ambe_path, "rb") as f:
                            _wordBitarray = bitarray(endian="big")
                            _wordBitarray.frombytes(f.read())
                    except OSError:
                        continue
                    _wordBADict[voice_name] = self._pairs_from_bitarray(_wordBitarray)
                _wordBADict.setdefault("silence", [SILENCE_PAIR])
                result[_lang] = _wordBADict
            else:
                index_path = Path(str(_prefix) + ".indx")
                ambe_path = Path(str(_prefix) + ".ambe")
                if not index_path.is_file() or not ambe_path.is_file():
                    result[_lang] = {"silence": [SILENCE_PAIR]}
                    continue
                indexDict: dict[str, list[int]] = {}
                with open(index_path, "r", encoding="utf-8") as index:
                    for line in index:
                        parts = line.split()
                        if len(parts) >= 3:
                            voice_name, start, length = parts[0], int(parts[1]), int(parts[2])
                            indexDict[voice_name] = [start * _AMBE_LENGTH, length * _AMBE_LENGTH]
                try:
                    with open(ambe_path, "rb") as ambe:
                        for voice_name, (start, length) in indexDict.items():
                            ambe.seek(start)
                            _wordBitarray = bitarray(endian="big")
                            _wordBitarray.frombytes(ambe.read(length))
                            _wordBADict[voice_name] = self._pairs_from_bitarray(_wordBitarray)
                except OSError:
                    result[_lang] = {"silence": [SILENCE_PAIR]}
                    continue
                _wordBADict.setdefault("silence", [SILENCE_PAIR])
                result[_lang] = _wordBADict
        return result

    def _pairs_from_bitarray(self, _wordBitarray: bitarray) -> list[list[bitarray]]:
        """Convert bitarray to list of [burst0, burst1] pairs (108 bits each)."""
        _wordBA: list[list[bitarray]] = []
        pairs = 1
        _lastburst: bitarray | None = None
        for _burst in _make_bursts(_wordBitarray):
            if pairs == 2 and _lastburst is not None:
                _wordBA.append([_lastburst, _burst])
                _lastburst = None
                pairs = 1
            else:
                pairs = 2
                _lastburst = _burst
        return _wordBA

    def readSingleFile(self, filename: str) -> list[list[bitarray]]:
        """Read one .ambe file; return list of [b0, b1] pairs (legacy readSingleFile)."""
        full = self.path / filename if not filename.startswith("/") else Path(filename)
        if not full.is_file():
            full = self.path / filename
        if not full.is_file():
            return []
        try:
            with open(full, "rb") as ambe:
                _wordBitarray = bitarray(endian="big")
                _wordBitarray.frombytes(ambe.read())
        except OSError:
            return []
        return self._pairs_from_bitarray(_wordBitarray)


class DefaultVoiceProvider(VoiceProvider):
    """Voice provider using ReadAMBE and pkt_gen (legacy readAMBE + mk_voice.pkt_gen)."""

    def __init__(self) -> None:
        self._words: dict[str, dict[str, Any]] = {}

    def get_ambe_words(self, languages: str, audio_path: str) -> dict[str, dict[str, Any]]:
        """Load AMBE words (readAMBE.readfiles). Apply i18n voiceMap per lang. Cached per (languages, audio_path)."""
        key = f"{languages}:{audio_path}"
        if key not in self._words:
            reader = ReadAMBE(languages, audio_path)
            result = reader.readfiles()
            for lang, words in result.items():
                _map = VOICE_MAP.get(lang, {})
                for mapword, mapped in _map.items():
                    if mapped in words:
                        words[mapword] = words[mapped]
                logger.info("(AMBE) for language %s, read %s words into voice dict", lang, len(words) - 1)
            self._words[key] = result
        return self._words[key]

    def read_single_file(self, audio_path: str, lang: str, file_number: str) -> list:
        """Read one .ambe file (e.g. {lang}/ondemand/{file_number}.ambe). Legacy readSingleFile."""
        rel = os.path.join(lang, "ondemand", f"{file_number}.ambe")
        reader = ReadAMBE(lang, audio_path)
        return reader.readSingleFile(rel)

    def pkt_gen(
        self, rf_src: bytes, dst_id: bytes, peer: bytes, slot: int, phrase: list[Any]
    ) -> Iterator[bytes]:
        """Generate HBP voice packets for phrase. Legacy mk_voice.pkt_gen."""
        return _pkt_gen(rf_src, dst_id, peer, slot, phrase)


class StubVoiceProvider(VoiceProvider):
    """Stub: get_ambe_words returns empty; pkt_gen returns empty iterator; read_single_file returns []."""

    def get_ambe_words(self, languages: str, audio_path: str) -> dict[str, dict[str, Any]]:
        return {}

    def pkt_gen(
        self, rf_src: bytes, dst_id: bytes, peer: bytes, slot: int, phrase: list[Any]
    ) -> Iterator[bytes]:
        return iter([])

    def read_single_file(self, audio_path: str, lang: str, file_number: str) -> list:
        return []
