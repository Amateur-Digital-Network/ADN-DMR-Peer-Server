# ADN DMR Peer Server - voice ident use cases
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

"""Voice ident: periodic ident on MASTER systems (VOICE_IDENT, slot 2 idle 30s)."""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable

from ..domain import HBPF_SLT_VTERM, bytes_3, int_id
from ..domain.hbp_protocol import normalize_fixed_width_ascii
from .server_voice import server_voice_rf_src_bytes

logger = logging.getLogger(__name__)


def _alias_tg(dst_id: bytes, config: dict[str, Any]) -> str:
    """Resolve TGID to alias for logging; fallback to numeric."""
    tg_ids = config.get("_TG_IDS", {})
    idx = int_id(dst_id)
    return tg_ids.get(idx, str(idx))


class IdentUseCases:
    """Run voice ident for MASTER systems with VOICE_IDENT (legacy threadIdent/ident)."""

    def __init__(
        self,
        config: dict[str, Any],
        voice_use_cases: Any,
        audio_path: str,
        get_protocols: Callable[[], dict[str, Any]],
        call_from_reactor: Callable[..., None],
    ) -> None:
        self._config = config
        self._voice = voice_use_cases
        self._audio_path = audio_path
        self._get_protocols = get_protocols
        self._call_from_reactor = call_from_reactor

    def run_ident(self) -> None:
        """Run ident once (legacy ident()). Call from thread; uses call_from_reactor to send packets."""
        systems_cfg = self._config.get("SYSTEMS", {})
        protocols = self._get_protocols()
        ann_lang = (self._config.get("VOICE", {}).get("ANNOUNCEMENT_LANGUAGES") or "").strip()
        if not ann_lang:
            # Fallback: derive from MASTER systems with VOICE_IDENT (each has ANNOUNCEMENT_LANGUAGE)
            langs = set()
            for sys_cfg in systems_cfg.values():
                if sys_cfg.get("MODE") == "MASTER" and sys_cfg.get("VOICE_IDENT"):
                    langs.add(sys_cfg.get("ANNOUNCEMENT_LANGUAGE", "en_GB"))
            ann_lang = ",".join(sorted(langs)) if langs else ""
        if not ann_lang:
            return
        words_by_lang = self._voice.get_ambe_words(ann_lang, self._audio_path)
        if not words_by_lang:
            return

        for system in list(systems_cfg.keys()):
            sys_cfg = systems_cfg.get(system, {})
            if sys_cfg.get("MODE") != "MASTER":
                continue
            if not sys_cfg.get("VOICE_IDENT"):
                continue
            _lang = sys_cfg.get("ANNOUNCEMENT_LANGUAGE", "en_GB")
            if _lang not in words_by_lang:
                continue
            words = words_by_lang[_lang]
            max_peers = int(sys_cfg.get("MAX_PEERS", 1))
            if max_peers > 1:
                logger.debug("(IDENT) %s System has MAX_PEERS > 1, skipping", system)
                continue
            _callsign = None
            peers = sys_cfg.get("PEERS", {})
            for _peerid in peers:
                peer_cfg = peers.get(_peerid, {})
                if isinstance(peer_cfg, dict) and peer_cfg.get("CALLSIGN"):
                    _callsign = normalize_fixed_width_ascii(peer_cfg["CALLSIGN"])
                    break
            if not _callsign:
                logger.debug("(IDENT) %s System has no peers or no recorded callsign, skipping", system)
                continue

            protocol = protocols.get(system)
            if not protocol or not getattr(protocol, "STATUS", None):
                continue
            _slot = protocol.STATUS.get(2)
            if not _slot:
                continue
            rx_type = _slot.get("RX_TYPE")
            tx_type = _slot.get("TX_TYPE")
            tx_time = _slot.get("TX_TIME", 0)
            rx_time = _slot.get("RX_TIME", 0)
            now = time.time()
            if (rx_type != HBPF_SLT_VTERM or tx_type != HBPF_SLT_VTERM or
                    now - tx_time <= 30 or now - rx_time <= 30):
                continue

            _all_call = bytes_3(16777215)
            _source_id = server_voice_rf_src_bytes(self._config)
            _dst_id = b""
            override_tg = sys_cfg.get("OVERRIDE_IDENT_TG")
            if override_tg is not None and int(override_tg) > 0 and int(override_tg) < 16777215:
                _dst_id = bytes_3(int(override_tg))
            else:
                _dst_id = _all_call

            logger.info(
                "(%s) %s System idle. Sending voice ident to TG %s",
                system, _callsign, _alias_tg(_dst_id, self._config),
            )

            silence = words.get("silence")
            if not silence:
                continue
            _say = [silence, silence, silence, words.get("this-is") or silence]
            for _ in range(6):
                _say.append(silence)
            _systemcs = re.sub(r"\W+", "", _callsign).upper()
            for character in _systemcs:
                _say.append(words.get(character) or silence)
                _say.append(silence)
            for _ in range(5):
                _say.append(silence)
            _say.append(words.get("adn") or silence)

            server_id = self._config.get("GLOBAL", {}).get("SERVER_ID", b"\x00\x00\x00\x00")
            if not isinstance(server_id, bytes):
                server_id = bytes_3(int(server_id))
            speech = self._voice.pkt_gen(_source_id, _dst_id, server_id, 1, _say)

            time.sleep(1)
            if not protocol.STATUS.get(2):
                continue
            self._voice.play_on_slot(protocol, system, speech, _source_id, _dst_id)
