# ADN DMR Peer Server - TTS engine (legacy tts_engine.py)
# Copyright (C) 2026  Rodrigo Pérez, CE5RPY <ce5rpy@qmd.cl>
#
# Derived from ADN DMR Server / FreeDMR  / HBlink. Original license:
###############################################################################
# Copyright (C) 2026 Joaquin Madrid Belando, EA5GVK <ea5gvk@gmail.com>
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

"""
TTS Engine: convert .txt to .ambe for DMR.
Pipeline: .txt -> gTTS -> .mp3 -> ffmpeg -> .wav (8kHz mono 16-bit) -> vocoder/AMBEServer -> .ambe
"""

from __future__ import annotations

import logging
import os
import socket
import struct
import subprocess
import threading
import time
import wave
from typing import Any

logger = logging.getLogger(__name__)

# One TTS conversion at a time (shared AMBEServer / vocoder cannot handle parallel sessions).
_tts_conversion_lock = threading.Lock()

_AMBESERVER_FRAME_TIMEOUT_S = 5.0
_AMBESERVER_ENCODE_TIMEOUT_S = 120.0
_AMBESERVER_MAX_CONSECUTIVE_ERRORS = 10
_AMBESERVER_PROGRESS_EVERY_FRAMES = 100

_LANG_MAP = {
    "es_ES": "es", "en_GB": "en", "en_US": "en", "fr_FR": "fr",
    "de_DE": "de", "it_IT": "it", "pt_PT": "pt", "pt_BR": "pt",
    "pl_PL": "pl", "nl_NL": "nl", "da_DK": "da", "sv_SE": "sv",
    "no_NO": "no", "el_GR": "el", "th_TH": "th", "cy_GB": "cy",
    "ca_ES": "ca", "gl_ES": "gl", "eu_ES": "eu",
}

DV3K_START_BYTE = 0x61
DV3K_TYPE_CONTROL = 0x00
DV3K_TYPE_AMBE = 0x01
DV3K_TYPE_AUDIO = 0x02
DV3K_AMBE_FIELD_ID = 0x01
DV3K_AUDIO_FIELD_ID = 0x00
DV3K_SAMPLES_PER_FRAME = 160
DV3K_RATET_DMR = bytes([0x61, 0x00, 0x02, 0x00, 0x09, 0x21])
DV3K_PRODID_REQ = bytes([0x61, 0x00, 0x01, 0x00, 0x30])


def _get_tts_lang(announcement_language: str) -> str:
    if announcement_language in _LANG_MAP:
        return _LANG_MAP[announcement_language]
    return announcement_language[:2] if len(announcement_language) >= 2 else announcement_language


def _generate_tts_audio(text: str, lang: str, mp3_path: str) -> bool:
    try:
        from gtts import gTTS
    except ImportError:
        logger.error("(TTS) gTTS not installed. Run: pip install gTTS")
        return False
    try:
        tts = gTTS(text=text, lang=lang, slow=False)
        tts.save(mp3_path)
        logger.info("(TTS) TTS audio generated: %s", mp3_path)
        return True
    except Exception as e:
        logger.error("(TTS) Error generating TTS audio: %s", e)
        return False


def _convert_to_wav(mp3_path: str, wav_path: str, volume_db: int = 0, speed: float = 1.0) -> bool:
    speed = max(0.5, min(2.0, speed))
    _filters: list[str] = []
    if speed != 1.0:
        _filters.append('atempo={:.2f}'.format(speed))
        logger.info('(TTS) Aplicando velocidad: x%.2f', speed)
    if volume_db != 0:
        _filters.append('volume={}dB'.format(volume_db))
        logger.info("(TTS) Applying volume adjustment: %ddB", volume_db)
    cmd = ["ffmpeg", "-y", "-i", mp3_path, "-ar", "8000", "-ac", "1", "-sample_fmt", "s16"]
    if _filters:
        cmd += ["-af", ",".join(_filters)]
    cmd += ["-f", "wav", wav_path]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=60)
        if result.returncode != 0:
            logger.error("(TTS) ffmpeg error: %s", (result.stderr or b"").decode("utf-8", errors="ignore")[:500])
            return False
        logger.info("(TTS) Audio converted to 8kHz mono WAV: %s", wav_path)
        return True
    except FileNotFoundError:
        logger.error("(TTS) ffmpeg not found. Install ffmpeg on the system")
        return False
    except subprocess.TimeoutExpired:
        logger.error("(TTS) ffmpeg conversion timeout")
        return False
    except Exception as e:
        logger.error("(TTS) Audio conversion error: %s", e)
        return False


def _encode_ambe_vocoder(wav_path: str, ambe_path: str, vocoder_cmd: str) -> bool:
    if not vocoder_cmd:
        return False
    cmd = vocoder_cmd.replace("{wav}", wav_path).replace("{ambe}", ambe_path)
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, timeout=120)
        if result.returncode != 0:
            logger.error("(TTS) Vocoder error: %s", (result.stderr or b"").decode("utf-8", errors="ignore")[:500])
            return False
        if not os.path.isfile(ambe_path):
            logger.error("(TTS) Vocoder did not produce AMBE file: %s", ambe_path)
            return False
        logger.info("(TTS) Audio encoded to AMBE via external vocoder: %s", ambe_path)
        return True
    except subprocess.TimeoutExpired:
        logger.error("(TTS) AMBE encoding timeout")
        return False
    except Exception as e:
        logger.error("(TTS) Error running vocoder: %s", e)
        return False


def _build_audio_packet(pcm_samples: list[int]) -> bytes:
    payload = struct.pack("BB", DV3K_AUDIO_FIELD_ID, len(pcm_samples))
    for sample in pcm_samples:
        payload += struct.pack(">h", sample)
    header = bytes([DV3K_START_BYTE]) + struct.pack(">HB", len(payload), DV3K_TYPE_AUDIO)
    return header + payload


def _parse_ambe_response(data: bytes) -> bytes | None:
    if len(data) < 4 or data[0] != DV3K_START_BYTE:
        return None
    _payload_len = struct.unpack(">H", data[1:3])[0]
    _pkt_type = data[3]
    if _pkt_type == DV3K_TYPE_AMBE and len(data) > 5:
        _field_id = data[4]
        if _field_id == DV3K_AMBE_FIELD_ID:
            _num_bits = data[5]
            _num_bytes = (_num_bits + 7) // 8
            return data[6 : 6 + _num_bytes]
    return None


def _encode_ambe_ambeserver(wav_path: str, ambe_path: str, host: str, port: int) -> bool:
    host = host.strip().strip('"').strip("'")
    logger.info("(TTS-AMBESERVER) Connecting to AMBEServer %s:%d", host, port)
    try:
        resolved = socket.gethostbyname(host)
        if resolved != host:
            logger.info("(TTS-AMBESERVER) Host %s resolved to %s", host, resolved)
        host = resolved
    except socket.gaierror as e:
        logger.error('(TTS-AMBESERVER) Cannot resolve host "%s": %s', host, e)
        return False
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(_AMBESERVER_FRAME_TIMEOUT_S)
    except Exception as e:
        logger.error("(TTS-AMBESERVER) Error creating UDP socket: %s", e)
        return False
    try:
        sock.sendto(DV3K_PRODID_REQ, (host, port))
        data, _ = sock.recvfrom(1024)
        if data[0] != DV3K_START_BYTE:
            logger.error("(TTS-AMBESERVER) Invalid response from AMBEServer")
            sock.close()
            return False
        logger.info("(TTS-AMBESERVER) AMBEServer connected")
    except socket.timeout:
        logger.error("(TTS-AMBESERVER) Timeout connecting to AMBEServer %s:%d", host, port)
        sock.close()
        return False
    except Exception as e:
        logger.error("(TTS-AMBESERVER) Connection error: %s", e)
        sock.close()
        return False
    try:
        sock.sendto(DV3K_RATET_DMR, (host, port))
        data, _ = sock.recvfrom(1024)
        if data[0] != DV3K_START_BYTE:
            logger.error("(TTS-AMBESERVER) Error setting RATET DMR")
            sock.close()
            return False
        logger.info("(TTS-AMBESERVER) RATET DMR configured")
    except (socket.timeout, Exception):
        sock.close()
        return False
    try:
        wf = wave.open(wav_path, "rb")
    except Exception as e:
        logger.error("(TTS-AMBESERVER) Error opening WAV: %s", e)
        sock.close()
        return False
    if wf.getsampwidth() != 2 or wf.getnchannels() != 1:
        logger.error("(TTS-AMBESERVER) WAV must be mono 16-bit PCM")
        wf.close()
        sock.close()
        return False
    _total_frames = wf.getnframes()
    _sample_rate = wf.getframerate()
    _duration_s = _total_frames / _sample_rate if _sample_rate else 0.0
    _pcm_frames = (_total_frames + DV3K_SAMPLES_PER_FRAME - 1) // DV3K_SAMPLES_PER_FRAME
    logger.info(
        "(TTS-AMBESERVER) WAV: %d samples, %d Hz, duration: %.1fs (~%d AMBE frames)",
        _total_frames,
        _sample_rate,
        _duration_s,
        _pcm_frames,
    )
    _raw_frames = wf.readframes(_total_frames)
    wf.close()
    _samples = list(struct.unpack("<" + "h" * _total_frames, _raw_frames))
    _ambe_frames: list[bytes] = []
    _frames_sent = 0
    _frames_error = 0
    _consecutive_errors = 0
    _encode_deadline = time.monotonic() + _AMBESERVER_ENCODE_TIMEOUT_S
    for i in range(0, len(_samples), DV3K_SAMPLES_PER_FRAME):
        _frame_idx = i // DV3K_SAMPLES_PER_FRAME
        if time.monotonic() >= _encode_deadline:
            logger.error(
                "(TTS-AMBESERVER) Encoding timeout after %ss (%d/%d frames)",
                int(_AMBESERVER_ENCODE_TIMEOUT_S),
                _frames_sent,
                _pcm_frames,
            )
            break
        _chunk = _samples[i : i + DV3K_SAMPLES_PER_FRAME]
        if len(_chunk) < DV3K_SAMPLES_PER_FRAME:
            _chunk = _chunk + [0] * (DV3K_SAMPLES_PER_FRAME - len(_chunk))
        _audio_pkt = _build_audio_packet(_chunk)
        try:
            sock.sendto(_audio_pkt, (host, port))
            data, _ = sock.recvfrom(1024)
            _ambe_data = _parse_ambe_response(data)
            if _ambe_data is not None:
                _ambe_frames.append(_ambe_data)
                _frames_sent += 1
                _consecutive_errors = 0
            else:
                _frames_error += 1
                _consecutive_errors += 1
                logger.debug(
                    "(TTS-AMBESERVER) Frame %d: non-AMBE response (type: 0x%02X)",
                    _frame_idx,
                    data[3] if len(data) > 3 else 0,
                )
        except socket.timeout:
            _frames_error += 1
            _consecutive_errors += 1
            logger.warning("(TTS-AMBESERVER) Timeout on frame %d", _frame_idx)
        except Exception as e:
            _frames_error += 1
            _consecutive_errors += 1
            logger.error("(TTS-AMBESERVER) Error on frame %d: %s", _frame_idx, e)
        if _consecutive_errors >= _AMBESERVER_MAX_CONSECUTIVE_ERRORS:
            logger.error(
                "(TTS-AMBESERVER) Aborting after %d consecutive frame errors (%d/%d sent)",
                _consecutive_errors,
                _frames_sent,
                _pcm_frames,
            )
            break
        if _frame_idx > 0 and _frame_idx % _AMBESERVER_PROGRESS_EVERY_FRAMES == 0:
            logger.info(
                "(TTS-AMBESERVER) Progress: %d/%d frames encoded",
                _frames_sent,
                _pcm_frames,
            )
    sock.close()
    if not _ambe_frames:
        logger.error("(TTS-AMBESERVER) No AMBE frames received")
        return False
    try:
        with open(ambe_path, "wb") as f:
            for frame in _ambe_frames:
                f.write(frame)
    except Exception as e:
        logger.error("(TTS-AMBESERVER) Error writing AMBE: %s", e)
        return False
    logger.info(
        "(TTS-AMBESERVER) Encoding completed: %d frames (%d errors): %s",
        _frames_sent,
        _frames_error,
        ambe_path,
    )
    return True


def _cleanup(files: list[str]) -> None:
    for f in files:
        try:
            if os.path.isfile(f):
                os.remove(f)
        except Exception as e:
            logger.warning("(TTS) cleanup remove %s failed: %s", f, e)


def _cleanup(files: list[str]) -> None:
    for f in files:
        try:
            if os.path.isfile(f):
                os.remove(f)
        except Exception as e:
            logger.warning("(TTS) cleanup remove %s failed: %s", f, e)


def _ambe_cache_valid(txt_path: str, ambe_path: str) -> bool:
    if not os.path.isfile(ambe_path):
        return False
    if not os.path.isfile(txt_path):
        return True
    return os.path.getmtime(ambe_path) > os.path.getmtime(txt_path)


def _text_to_ambe_uncached(
    txt_path: str,
    ambe_path: str,
    language: str,
    vocoder_cmd: str,
    ambeserver_host: str,
    ambeserver_port: int,
    volume_db: int,
    speed: float,
) -> bool:
    with open(txt_path, "r", encoding="utf-8") as f:
        text = f.read().strip()
    if not text:
        logger.warning("(TTS) Text file is empty: %s", txt_path)
        return False
    logger.info("(TTS) Converting text to AMBE: %s (%d chars, language: %s)", txt_path, len(text), language)
    _dir = os.path.dirname(ambe_path)
    if _dir:
        os.makedirs(_dir, exist_ok=True)
    _base = os.path.splitext(ambe_path)[0]
    _mp3_path = _base + ".mp3"
    _wav_path = _base + ".wav"
    _tts_lang = _get_tts_lang(language)
    if not _generate_tts_audio(text, _tts_lang, _mp3_path):
        return False
    if not _convert_to_wav(_mp3_path, _wav_path, volume_db, speed):
        _cleanup([_mp3_path])
        return False
    _encoded = False
    if ambeserver_host:
        logger.info("(TTS) Using AMBEServer %s:%d", ambeserver_host, ambeserver_port)
        _encoded = _encode_ambe_ambeserver(_wav_path, ambe_path, ambeserver_host, ambeserver_port)
        if not _encoded:
            logger.warning("(TTS) AMBEServer failed, trying external vocoder...")
    if not _encoded and vocoder_cmd:
        logger.info("(TTS) Using external vocoder")
        _encoded = _encode_ambe_vocoder(_wav_path, ambe_path, vocoder_cmd)
    if not _encoded:
        logger.warning("(TTS) Could not encode to AMBE. Configure TTS_AMBESERVER_HOST or TTS_VOCODER_CMD.")
        _cleanup([_mp3_path, _wav_path])
        return False
    _cleanup([_mp3_path, _wav_path])
    logger.info("(TTS) Conversion completed: %s -> %s", txt_path, ambe_path)
    return True


def text_to_ambe(
    txt_path: str,
    ambe_path: str,
    language: str,
    vocoder_cmd: str,
    ambeserver_host: str = "",
    ambeserver_port: int = 2460,
    volume_db: int = 0,
    speed: float = 1.0,
) -> bool:
    """Convert .txt to .ambe (gTTS -> mp3 -> ffmpeg -> wav -> vocoder/AMBEServer)."""
    if not os.path.isfile(txt_path):
        logger.warning("(TTS) Text file not found: %s", txt_path)
        return False
    if _ambe_cache_valid(txt_path, ambe_path):
        logger.info("(TTS) Using cached AMBE (newer than .txt): %s", ambe_path)
        return True
    with _tts_conversion_lock:
        if _ambe_cache_valid(txt_path, ambe_path):
            logger.info("(TTS) Using cached AMBE (newer than .txt): %s", ambe_path)
            return True
        return _text_to_ambe_uncached(
            txt_path,
            ambe_path,
            language,
            vocoder_cmd,
            ambeserver_host,
            ambeserver_port,
            volume_db,
            speed,
        )


def ensure_tts_ambe(config: dict[str, Any], item: dict[str, Any], audio_path: str) -> str | None:
    """Ensure .ambe exists for TTS item; create from .txt if needed. Returns path or None."""
    if not item.get("ENABLED", False):
        return None
    _file = str(item.get("FILE") or "").strip()
    _lang = item.get("LANGUAGE", "en_GB")
    if not _file:
        return None
    g = config.get("VOICE", {})
    _txt_path = os.path.join(audio_path, _lang, "ondemand", _file + ".txt")
    _ambe_path = os.path.join(audio_path, _lang, "ondemand", _file + ".ambe")
    if os.path.isfile(_ambe_path):
        if not os.path.isfile(_txt_path):
            logger.info("(TTS) Using existing AMBE file (no .txt): %s", _ambe_path)
            return _ambe_path
        if os.path.getmtime(_ambe_path) > os.path.getmtime(_txt_path):
            logger.debug("(TTS) Using cached AMBE: %s", _ambe_path)
            return _ambe_path
    if not os.path.isfile(_txt_path):
        logger.warning("(TTS) Text file not found: %s", _txt_path)
        return None
    _vocoder_cmd = g.get("TTS_VOCODER_CMD", "")
    _ambeserver_host = (g.get("TTS_AMBESERVER_HOST") or "").strip()
    _ambeserver_port = int(g.get("TTS_AMBESERVER_PORT", 2460))
    _volume_db = int(g.get("TTS_VOLUME", -3))
    _speed = float(g.get("TTS_SPEED", 1.0))
    if text_to_ambe(_txt_path, _ambe_path, _lang, _vocoder_cmd, _ambeserver_host, _ambeserver_port, _volume_db, _speed):
        return _ambe_path
    if os.path.isfile(_ambe_path):
        logger.warning("(TTS) Using previous AMBE (conversion failed): %s", _ambe_path)
        return _ambe_path
    return None
