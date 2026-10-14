# ADN DMR Peer Server - tests voice TTS engine serialization
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

"""TTS engine serial conversion lock (shared AMBEServer)."""

from __future__ import annotations

import socket
import threading
import time
import wave

from plugin.infrastructure import tts_engine
from plugin.infrastructure.tts_engine import DV3K_PRODID_REQ, DV3K_SAMPLES_PER_FRAME


def test_text_to_ambe_serializes_parallel_conversions(tmp_path, monkeypatch) -> None:
    """Only one full TTS conversion may run at a time (shared AMBEServer)."""
    overlap = threading.Event()
    active = 0
    peak_active = 0
    state_lock = threading.Lock()

    def fake_encode(*_args, **_kwargs) -> bool:
        nonlocal active, peak_active
        with state_lock:
            active += 1
            peak_active = max(peak_active, active)
        time.sleep(0.05)
        with state_lock:
            active -= 1
        overlap.set()
        return True

    monkeypatch.setattr(tts_engine, "_generate_tts_audio", lambda *a, **k: True)
    monkeypatch.setattr(tts_engine, "_convert_to_wav", lambda *a, **k: True)
    monkeypatch.setattr(tts_engine, "_encode_ambe_ambeserver", fake_encode)
    monkeypatch.setattr(tts_engine, "_cleanup", lambda *a, **k: None)

    paths: list[tuple[str, str]] = []
    for name in ("a", "b"):
        txt = tmp_path / f"{name}.txt"
        ambe = tmp_path / f"{name}.ambe"
        txt.write_text(f"hello {name}", encoding="utf-8")
        paths.append((str(txt), str(ambe)))

    errors: list[BaseException] = []

    def run_one(txt_path: str, ambe_path: str) -> None:
        try:
            assert tts_engine.text_to_ambe(
                txt_path,
                ambe_path,
                "es_ES",
                "",
                "127.0.0.1",
                2473,
            )
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run_one, args=p) for p in paths]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    assert errors == []
    assert overlap.is_set()
    assert peak_active == 1


def test_encode_ambe_ambeserver_aborts_on_consecutive_timeouts(monkeypatch, tmp_path) -> None:
    wav_path = tmp_path / "test.wav"
    ambe_path = tmp_path / "test.ambe"

    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(b"\x00\x00" * (DV3K_SAMPLES_PER_FRAME * 25))

    class _TimeoutSock:
        def __init__(self) -> None:
            self._step = 0

        def sendto(self, *_args, **_kwargs) -> None:
            return None

        def recvfrom(self, _size: int) -> tuple[bytes, tuple[str, int]]:
            self._step += 1
            if self._step <= 2:
                return (DV3K_PRODID_REQ, ("127.0.0.1", 2473))
            raise socket.timeout()

        def close(self) -> None:
            return None

    monkeypatch.setattr(tts_engine.socket, "gethostbyname", lambda host: host)
    monkeypatch.setattr(tts_engine.socket, "socket", lambda *a, **k: _TimeoutSock())
    monkeypatch.setattr(tts_engine, "_AMBESERVER_MAX_CONSECUTIVE_ERRORS", 3)
    monkeypatch.setattr(tts_engine, "_AMBESERVER_ENCODE_TIMEOUT_S", 30.0)

    assert tts_engine._encode_ambe_ambeserver(str(wav_path), str(ambe_path), "127.0.0.1", 2473) is False
    assert not ambe_path.exists()
