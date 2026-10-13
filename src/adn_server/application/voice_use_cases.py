# ADN DMR Peer Server - voice use cases
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

"""Server voice prompts (ident, on-demand files, disconnected). Orchestrates VoiceProvider."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from ..domain import HBPF_SLT_VHEAD, HBPF_SLT_VTERM, bytes_3, bytes_4, int_id
from .ports import VoiceProvider
from .routing.helpers import slot_voice_held_by_other_stream
from .server_voice import server_voice_rf_src_bytes

logger = logging.getLogger(__name__)

_FRAME_INTERVAL = 0.058
# The TG/ID 4000 reply starts this long after the over ends, so the radio is back in receive.
_TG4000_REPLY_DELAY = 1.0
# At most this many statics are named in "linked to ..." (each TG is a few digits of speech).
_MAX_LINKED_SPOKEN = 4


@dataclass
class _PromptRun:
    """State of one prompt, shared by its worker thread and the reactor.

    Only the reactor writes it; the thread reads ``stopped`` between frames.
    """

    stopped: bool = False
    sent: int = 0
    stream_id: bytes | None = None


class VoiceUseCases:
    """Server voice prompts: voice ident, on-demand files (TG 9991-9999), disconnected prompt.

    Scheduled announcements and TTS live in the voice-announcements plugin.
    """

    def __init__(
        self,
        voice_provider: VoiceProvider,
        config: dict[str, Any],
        get_protocols: Callable[[], dict[str, Any]] | None = None,
        call_from_reactor: Callable[..., None] | None = None,
        audio_path: str | None = None,
        call_later: Callable[..., Any] | None = None,
    ) -> None:
        self._voice = voice_provider
        self._config = config
        self._get_protocols = get_protocols
        self._call_from_reactor = call_from_reactor
        self._audio_path = audio_path or ""
        self._call_later = call_later  # reactor.callLater: paces replies on the reactor, no thread held

    def _server_source_id(self) -> bytes:
        return server_voice_rf_src_bytes(self._config)

    def get_ambe_words(self, languages: str, audio_path: str) -> dict[str, dict[str, Any]]:
        """Load AMBE words for given languages (legacy readAMBE.readfiles)."""
        return self._voice.get_ambe_words(languages, audio_path)

    def pkt_gen(
        self, rf_src: bytes, dst_id: bytes, peer: bytes, slot: int, phrase: list[Any], *, private: bool = False
    ) -> Any:
        """Generate HBP voice packets for phrase (legacy mk_voice.pkt_gen); ``private``: unit call."""
        if private:
            return self._voice.pkt_gen(rf_src, dst_id, peer, slot, phrase, private=True)
        return self._voice.pkt_gen(rf_src, dst_id, peer, slot, phrase)

    def read_single_file(self, audio_path: str, lang: str, file_number: str) -> list:
        """Read one AMBE file (e.g. ondemand/{file_number}.ambe). Legacy readSingleFile."""
        return self._voice.read_single_file(audio_path, lang, file_number)

    def play_on_slot(
        self, protocol: Any, system: str, speech: Any, source_id: bytes, dst_id: bytes
    ) -> int:
        """Play a prompt on TS2 of an HBP system from a worker thread; frames sent.

        The thread only paces the frames: each one is sent on the reactor, which
        holds the slot while the prompt plays the way scheduled broadcasts do
        (TX_TYPE=VHEAD), so routed group voice finds it busy instead of going out
        as a second stream on the same slot. A radio keying up, or a call already
        on the slot, stops the prompt; the slot is released when it ends.
        """
        run = _PromptRun()
        _next_time = time.time()
        for pkt in speech:
            if run.stopped:
                break
            _next_time += _FRAME_INTERVAL
            delay = _next_time - time.time()
            if delay > 0.001:
                time.sleep(delay)
            self._call_from_reactor(self._prompt_frame, protocol, system, pkt, source_id, dst_id, run)
        self._call_from_reactor(self._prompt_end, protocol, run)
        return run.sent

    def _prompt_frame(
        self, protocol: Any, system: str, pkt: bytes, source_id: bytes, dst_id: bytes, run: _PromptRun
    ) -> None:
        """Reactor side of ``play_on_slot``: one frame, unless the slot is taken."""
        if run.stopped:
            return
        slot = protocol.STATUS.get(2) if getattr(protocol, "STATUS", None) else None
        if not slot:
            run.stopped = True
            return
        stream_id = pkt[16:20]
        now = time.time()
        if slot_voice_held_by_other_stream(slot, stream_id, now):
            run.stopped = True
            logger.info("(%s) Voice on TS2, stopping server prompt after %s frames", system, run.sent)
            return
        slot["TX_TYPE"] = HBPF_SLT_VHEAD
        slot["TX_STREAM_ID"] = stream_id
        slot["TX_RFS"] = source_id
        slot["TX_FROM_MESH"] = False  # this server's own prompt holds the slot
        slot["TX_TIME"] = now
        run.stream_id = stream_id
        protocol.send_voice_packet(pkt, source_id, dst_id, slot)
        run.sent += 1

    def _prompt_end(self, protocol: Any, run: _PromptRun) -> None:
        """Reactor side of ``play_on_slot``: free the slot if the prompt still holds it."""
        slot = protocol.STATUS.get(2) if getattr(protocol, "STATUS", None) else None
        if slot and run.stream_id is not None and slot.get("TX_STREAM_ID") == run.stream_id:
            slot["TX_TYPE"] = HBPF_SLT_VTERM

    def play_file_on_request(self, file_number: str, system: str) -> None:
        """Play AMBE file on request (legacy playFileOnRequest). TG 9991-9999 triggers this."""
        if not self._get_protocols or not self._call_from_reactor or not self._audio_path:
            return
        protocol = self._get_protocols().get(system)
        if not protocol or not getattr(protocol, "STATUS", None):
            return
        sys_cfg = self._config.get("SYSTEMS", {}).get(system, {})
        lang = sys_cfg.get("ANNOUNCEMENT_LANGUAGE", "en_GB")
        pairs = self.read_single_file(self._audio_path, lang, file_number)
        if not pairs:
            logger.warning("(%s) AMBE file not found or empty: %s/ondemand/%s.ambe", system, lang, file_number)
            return
        logger.info("(%s) Playing on-demand AMBE file: %s (ID: %s)", system, file_number, file_number)
        time.sleep(1)
        _say = [pairs]
        _source_id = self._server_source_id()
        speech = self.pkt_gen(_source_id, bytes_3(9), bytes_4(9), 1, _say)
        time.sleep(1)
        if not protocol.STATUS.get(2):
            return
        _pkt_count = self.play_on_slot(protocol, system, speech, _source_id, bytes_3(9))
        logger.info("(%s) On-demand playback complete: %s (%d packets)", system, file_number, _pkt_count)

    def _link_status_words(self, sys_cfg: dict[str, Any], linked: tuple[int, ...] = ()) -> list[Any] | None:
        """The link status in the system's language; None without audio.

        "linked to" and the TGs in ``linked`` (statics still on the slot, at most
        ``_MAX_LINKED_SPOKEN``), else "linked to <DEFAULT_REFLECTOR>", else "not linked".
        """
        _lang = sys_cfg.get("ANNOUNCEMENT_LANGUAGE", "en_GB")
        words_by_lang = self.get_ambe_words(_lang, self._audio_path)
        if _lang not in words_by_lang:
            return None
        words = words_by_lang[_lang]
        silence = words.get("silence")
        if not silence:
            return None
        _say = [silence, silence]
        default_refl = int(sys_cfg.get("DEFAULT_REFLECTOR", 0))
        if linked:
            _say.append(words.get("linkedto") or silence)
            for tg in linked[:_MAX_LINKED_SPOKEN]:
                _say.append(silence)
                for digit in str(tg):
                    _say.append(words.get(digit) or silence)
        elif default_refl > 0:
            _say.append(silence)
            _say.append(words.get("linkedto") or silence)
            _say.append(silence)
            _say.append(words.get("to") or silence)
            _say.append(silence)
            _say.append(silence)
            for digit in str(default_refl):
                _say.append(words.get(digit) or silence)
                _say.append(silence)
        else:
            _say.append(words.get("notlinked") or silence)
        _say.append(silence)
        return _say

    def disconnected_voice(self, system: str) -> None:
        """Send 'disconnected' / 'linked to reflector' voice (legacy disconnectedVoice). Run from thread."""
        if not self._get_protocols or not self._call_from_reactor or not self._audio_path:
            return
        protocol = self._get_protocols().get(system)
        if not protocol or not getattr(protocol, "STATUS", None):
            return
        _say = self._link_status_words(self._config.get("SYSTEMS", {}).get(system, {}))
        if _say is None:
            return
        _source_id = self._server_source_id()
        speech = self.pkt_gen(_source_id, bytes_3(9), bytes_4(9), 1, _say)
        time.sleep(1)
        if not protocol.STATUS.get(2):
            return
        logger.debug("(%s) Sending disconnected voice", system)
        self.play_on_slot(protocol, system, speech, _source_id, bytes_3(9))
        logger.debug("(%s) disconnected voice thread end", system)

    def tg4000_voice(
        self,
        system: str,
        peer_id: bytes,
        slot: int,
        *,
        rf_src: bytes = b"",
        private: bool = False,
        linked: tuple[int, ...] = (),
    ) -> int:
        """Answer a hotspot's TG/ID 4000 with the link status, to that hotspot only.

        Runs from a worker thread only to load the words and build the frames; playback is
        paced on the reactor with ``call_later`` (``_TG4000_REPLY_DELAY`` after the over, then
        one frame per ``_FRAME_INTERVAL``) through ``send_prompt_to_peer``, so it stops if the
        hotspot gets busy on that slot or leaves. A group 4000 is answered on TG 4000 from the
        server voice ID (the radio is still on that contact); a private 4000 (``private``) with
        a private call from ID 4000 to ``rf_src``. Returns the frames queued.
        """
        if not self._get_protocols or not self._call_from_reactor or not self._audio_path or not self._call_later:
            return 0
        protocol = self._get_protocols().get(system)
        if protocol is None or not hasattr(protocol, "send_prompt_to_peer"):
            return 0
        _say = self._link_status_words(self._config.get("SYSTEMS", {}).get(system, {}), tuple(linked))
        if _say is None:
            return 0
        ts = 1 if int(slot) == 2 else 0
        if private and rf_src:
            frames = list(self.pkt_gen(bytes_3(4000), rf_src, bytes_4(9), ts, _say, private=True))
        else:
            frames = list(self.pkt_gen(self._server_source_id(), bytes_3(4000), bytes_4(9), ts, _say))
        if frames:
            self._call_from_reactor(self._start_peer_prompt, protocol, system, peer_id, int(slot), frames)
        return len(frames)

    def _start_peer_prompt(self, protocol: Any, system: str, peer_id: bytes, slot: int, frames: list[bytes]) -> None:
        """Reactor: schedule the reply's first frame ``_TG4000_REPLY_DELAY`` from now."""
        run = _PromptRun()
        start = time.time() + _TG4000_REPLY_DELAY
        self._call_later(_TG4000_REPLY_DELAY, self._peer_prompt_step, protocol, system, peer_id, slot, frames, 0, start, run)

    def _peer_prompt_step(
        self, protocol: Any, system: str, peer_id: bytes, slot: int,
        frames: list[bytes], index: int, start: float, run: _PromptRun,
    ) -> None:
        """Reactor: one frame of the reply, then the next one on time; stops at the first refusal."""
        if index < len(frames) and protocol.send_prompt_to_peer(peer_id, frames[index]):
            run.sent += 1
            due = start + (index + 1) * _FRAME_INTERVAL
            self._call_later(
                max(0.0, due - time.time()), self._peer_prompt_step,
                protocol, system, peer_id, slot, frames, index + 1, start, run,
            )
            return
        logger.info("(%s) TG 4000 voice reply to peer %s on TS %s: %d frames", system, int_id(peer_id), slot, run.sent)
