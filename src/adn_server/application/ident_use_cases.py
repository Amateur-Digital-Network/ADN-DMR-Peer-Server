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

"""Voice ident: periodic ident on MASTER systems (VOICE_IDENT, slot 2 idle 30s).

A MASTER with one hotspot is identified with its slot (TS2). On a MASTER several hotspots
share (inject-only proxy target, MAX_PEERS > 1), VOICE_IDENT allows it and each hotspot whose
OPTIONS ask for it (VOICE=1 or IDENT=1) gets its own ident, with its callsign, LANG and
VOICETG/IDENTTG, sent to that hotspot only.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable

from ..domain import HBPF_SLT_VTERM, bytes_3, bytes_4, int_id
from ..domain.config_coerce import coerce_bool
from ..domain.hbp_protocol import normalize_fixed_width_ascii
from .proxy.deployment import master_shared_by_hotspots
from .report.payloads import parse_options_kv
from .routing.downlink import PEER_STREAM_END
from .server_voice import server_voice_rf_src_bytes

logger = logging.getLogger(__name__)

_IDLE_S = 30
_ALL_CALL = 16777215
_IDENT_SLOT = 2


def _alias_tg(dst_id: bytes, config: dict[str, Any]) -> str:
    """Resolve TGID to alias for logging; fallback to numeric."""
    tg_ids = config.get("_TG_IDS", {})
    idx = int_id(dst_id)
    return tg_ids.get(idx, str(idx))


def _ident_tg(value: Any) -> int | None:
    """A TG to send the ident to, or None (absent, empty or out of range: all-call)."""
    try:
        tg = int(value)
    except (TypeError, ValueError):
        return None
    return tg if 0 < tg < _ALL_CALL else None


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
        protocols = self._get_protocols()
        for system, sys_cfg in list(self._config.get("SYSTEMS", {}).items()):
            if sys_cfg.get("MODE") != "MASTER" or not sys_cfg.get("VOICE_IDENT"):
                continue
            protocol = protocols.get(system)
            if not protocol:
                continue
            if master_shared_by_hotspots(self._config, system):
                self._ident_hotspots(system, sys_cfg, protocol)
            else:
                self._ident_system(system, sys_cfg, protocol)

    def _words(self, lang: str) -> dict[str, Any] | None:
        """AMBE words for ``lang``; VOICE.ANNOUNCEMENT_LANGUAGES, when set, limits the languages."""
        allowed = (self._config.get("VOICE", {}).get("ANNOUNCEMENT_LANGUAGES") or "").strip()
        if allowed and lang not in [x.strip() for x in allowed.split(",")]:
            return None
        return self._voice.get_ambe_words(lang, self._audio_path).get(lang)

    def _speech(self, words: dict[str, Any], callsign: str, dst_id: bytes) -> list[bytes]:
        """Voice frames for "this is <callsign> ... ADN"; empty without the silence word."""
        silence = words.get("silence")
        if not silence:
            return []
        _say = [silence, silence, silence, words.get("this-is") or silence]
        for _ in range(6):
            _say.append(silence)
        for character in re.sub(r"\W+", "", callsign).upper():
            _say.append(words.get(character) or silence)
            _say.append(silence)
        for _ in range(5):
            _say.append(silence)
        _say.append(words.get("adn") or silence)
        server_id = self._config.get("GLOBAL", {}).get("SERVER_ID", b"\x00\x00\x00\x00")
        if not isinstance(server_id, bytes):
            server_id = bytes_3(int(server_id))
        return list(self._voice.pkt_gen(server_voice_rf_src_bytes(self._config), dst_id, server_id, 1, _say))

    def _ident_hotspots(self, system: str, sys_cfg: dict[str, Any], protocol: Any) -> None:
        """One ident per hotspot that asked for it and has been quiet, to that hotspot only."""
        now = time.time()
        peers = getattr(protocol, "_peers", {})
        for peer_id, peer in list(peers.items()):
            if not isinstance(peer, dict) or peer.get("CONNECTION") != "YES":
                continue
            options = parse_options_kv(peer.get("OPTIONS"))
            if not coerce_bool(options.get("VOICE", options.get("IDENT"))):
                continue
            if not self._peer_quiet(protocol, peer_id, peer, now):
                continue
            callsign = normalize_fixed_width_ascii(peer.get("CALLSIGN") or b"")
            words = self._words(options.get("LANG") or sys_cfg.get("ANNOUNCEMENT_LANGUAGE", "en_GB"))
            if not callsign or not words:
                continue
            tg = (
                _ident_tg(options.get("VOICETG", options.get("IDENTTG")))
                or _ident_tg(sys_cfg.get("OVERRIDE_IDENT_TG"))
                or _ALL_CALL
            )
            frames = self._speech(words, callsign, bytes_3(tg))
            if frames and self._voice.play_to_peer(system, peer_id, _IDENT_SLOT, frames, "voice ident"):
                logger.info(
                    "(%s) %s idle. Sending voice ident to peer %s, TG %s",
                    system, callsign, int_id(peer_id), _alias_tg(bytes_3(tg), self._config),
                )

    @staticmethod
    def _peer_quiet(protocol: Any, peer_id: bytes, peer: dict[str, Any], now: float) -> bool:
        """No stream on this hotspot in the last ``_IDLE_S``: none ended, none still going."""
        if now - float(peer.get(PEER_STREAM_END, 0)) <= _IDLE_S:
            return False
        rows = getattr(protocol, "_peer_voice_slots", {}).get(bytes_4(int_id(peer_id)), {})
        return all(now - float(row.get("time", 0)) > _IDLE_S for row in list(rows.values()) if isinstance(row, dict))

    def _ident_system(self, system: str, sys_cfg: dict[str, Any], protocol: Any) -> None:
        """A MASTER with one hotspot: the ident holds the system's TS2 while it plays."""
        words = self._words(sys_cfg.get("ANNOUNCEMENT_LANGUAGE", "en_GB"))
        if not words:
            return
        _callsign = None
        peers = sys_cfg.get("PEERS", {})
        for _peerid in peers:
            peer_cfg = peers.get(_peerid, {})
            if isinstance(peer_cfg, dict) and peer_cfg.get("CALLSIGN"):
                _callsign = normalize_fixed_width_ascii(peer_cfg["CALLSIGN"])
                break
        if not _callsign:
            logger.debug("(IDENT) %s System has no peers or no recorded callsign, skipping", system)
            return

        if not getattr(protocol, "STATUS", None):
            return
        _slot = protocol.STATUS.get(_IDENT_SLOT)
        if not _slot:
            return
        rx_type = _slot.get("RX_TYPE")
        tx_type = _slot.get("TX_TYPE")
        tx_time = _slot.get("TX_TIME", 0)
        rx_time = _slot.get("RX_TIME", 0)
        now = time.time()
        if (rx_type != HBPF_SLT_VTERM or tx_type != HBPF_SLT_VTERM or
                now - tx_time <= _IDLE_S or now - rx_time <= _IDLE_S):
            return

        _source_id = server_voice_rf_src_bytes(self._config)
        _dst_id = bytes_3(_ident_tg(sys_cfg.get("OVERRIDE_IDENT_TG")) or _ALL_CALL)

        logger.info(
            "(%s) %s System idle. Sending voice ident to TG %s",
            system, _callsign, _alias_tg(_dst_id, self._config),
        )

        speech = self._speech(words, _callsign, _dst_id)
        if not speech:
            return
        time.sleep(1)
        if not protocol.STATUS.get(_IDENT_SLOT):
            return
        self._voice.play_on_slot(protocol, system, speech, _source_id, _dst_id)
