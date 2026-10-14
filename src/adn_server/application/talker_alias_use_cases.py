# ADN DMR Peer Server - Talker Alias use cases
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

"""Talker Alias policy: template formatting, inject vs passthrough."""

from __future__ import annotations

import logging
from typing import Any

from ..domain import int_id
from ..domain.talker_alias import (
    DMRA_BLOCK_COUNT,
    DMRA_PAYLOAD_LEN,
    buffer_from_blocks,
    buffer_from_wire_blocks,
    build_dmra_packet,
    build_dmra_packets,
    decode_ta_from_blocks,
    is_ta_header_byte,
    parse_ta_text_formats,
    required_ta_block_count,
    talker_alias_decode_complete,
    truncate_talker_alias,
)
from .ports import TalkerAliasEmblcEncoder

logger = logging.getLogger(__name__)

VALID_MODES = frozenset({"inject", "passthrough", "both"})


def talker_alias_settings(config: dict[str, Any], system_name: str | None = None) -> dict[str, Any]:
    """Effective Talker Alias settings (GLOBAL with optional per-system override)."""
    global_cfg = config.get("GLOBAL", {})
    sys_cfg = config.get("SYSTEMS", {}).get(system_name or "", {}) if system_name else {}
    enabled = sys_cfg.get("TALKER_ALIAS")
    if enabled is None:
        enabled = global_cfg.get("TALKER_ALIAS", False)
    mode = sys_cfg.get("TALKER_ALIAS_MODE")
    if mode is None:
        mode = global_cfg.get("TALKER_ALIAS_MODE", "both")
    if mode not in VALID_MODES:
        mode = "both"
    fmt = sys_cfg.get("TALKER_ALIAS_FORMAT")
    if fmt is None:
        fmt = global_cfg.get("TALKER_ALIAS_FORMAT", "{callsign} {fname}")
    tfmt = sys_cfg.get("TALKER_ALIAS_TEXT_FORMAT")
    if tfmt is None:
        tfmt = global_cfg.get("TALKER_ALIAS_TEXT_FORMAT", "utf8")
    send_dmra = sys_cfg.get("TALKER_ALIAS_SEND_DMRA")
    if send_dmra is None:
        send_dmra = global_cfg.get("TALKER_ALIAS_SEND_DMRA", False)
    return {
        "enabled": bool(enabled),
        "mode": mode,
        "format": str(fmt),
        "text_formats": parse_ta_text_formats(tfmt),
        # Standalone HBP DMRA UDP (legacy wire often omits this; radios use embedded LC).
        "send_dmra": bool(send_dmra),
    }


def format_talker_alias_text(config: dict[str, Any], rf_src: bytes) -> str:
    """Build display string from subscriber profile + template."""
    settings = talker_alias_settings(config)
    template = settings["format"]
    rid = int_id(rf_src)
    callsign, fname, surname, talker_alias = config.get("_SUB_PROFILES", {}).get(rid) or (None, "", "", "")
    callsign = callsign or config.get("_SUB_IDS", {}).get(rid) or ""
    if talker_alias:
        text = talker_alias
    else:
        try:
            text = template.format(
                callsign=callsign,
                fname=fname,
                surname=surname,
                id=rid,
            )
        except (KeyError, ValueError):
            text = callsign or f"DMR ID:{rid}"
    text = " ".join(text.split())
    if not text.strip():
        text = callsign or f"DMR ID:{rid}"
    return truncate_talker_alias(text)


def _passthrough_buf_ready(blocks: dict[int, bytes], buf: bytes) -> bool:
    need = required_ta_block_count(buf)
    if need < 1 or need > DMRA_BLOCK_COUNT:
        return False
    if not all(i in blocks and len(blocks[i]) >= DMRA_PAYLOAD_LEN for i in range(need)):
        return False
    return talker_alias_decode_complete(buf)


def passthrough_complete(blocks: dict[int, bytes]) -> bool:
    """True when required TA blocks (1–4) are present and fully decode."""
    if not blocks:
        return False
    buf = buffer_from_wire_blocks(blocks)
    if _passthrough_buf_ready(blocks, buf):
        return True
    if blocks.get(0) and is_ta_header_byte(blocks[0][0]):
        return _passthrough_buf_ready(blocks, buffer_from_blocks(blocks))
    return False


def passthrough_packets_from_blocks(rf_src: bytes, blocks: dict[int, bytes]) -> list[bytes]:
    """Rebuild DMRA packets from buffered wire payloads (only blocks needed for TA)."""
    if passthrough_complete(blocks):
        buf = buffer_from_wire_blocks(blocks)
        if not talker_alias_decode_complete(buf) and blocks.get(0) and is_ta_header_byte(blocks[0][0]):
            buf = buffer_from_blocks(blocks)
        count = required_ta_block_count(buf)
        return [
            build_dmra_packet(rf_src, block_id, blocks[block_id])
            for block_id in range(count)
            if block_id in blocks
        ]
    return []


class TalkerAliasUseCases:
    """Orchestrate inject / passthrough for bridge forwarding."""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        ta_emblc_encoder: TalkerAliasEmblcEncoder,
    ) -> None:
        self._config = config
        self._ta_emblc = ta_emblc_encoder
        self._sent_streams: set[tuple[str, bytes]] = set()
        self._sent_kind: dict[tuple[str, bytes], str] = {}
        self._embed_logged: set[tuple[str, str, bytes]] = set()
        self._policy_logged: set[tuple[str, str, bytes, str]] = set()

    def clear_dmra_sent(self, system_name: str, stream_id: bytes) -> None:
        key = (system_name, stream_id)
        self._sent_streams.discard(key)
        self._sent_kind.pop(key, None)

    def clear_stream(self, system_name: str, stream_id: bytes) -> None:
        self.clear_dmra_sent(system_name, stream_id)
        self._embed_logged = {k for k in self._embed_logged if k[2] != stream_id}
        self._policy_logged = {k for k in self._policy_logged if k[2] != stream_id}

    def should_send_on_vhead(self, target_system: str, stream_id: bytes) -> bool:
        return (target_system, stream_id) not in self._sent_streams

    def should_resend_passthrough_dmra(self, target_system: str, stream_id: bytes) -> bool:
        """True when passthrough DMRA should replace a prior inject on this leg."""
        key = (target_system, stream_id)
        if key not in self._sent_streams:
            return True
        return self._sent_kind.get(key) == "inject"

    def mark_dmra_sent(self, target_system: str, stream_id: bytes, *, kind: str) -> None:
        key = (target_system, stream_id)
        self._sent_streams.add(key)
        self._sent_kind[key] = kind

    def _log_policy_once(
        self,
        source_system: str,
        target_system: str,
        stream_id: bytes,
        kind: str,
        text: str,
        *,
        suffix: str = "",
        via: str,
    ) -> None:
        log_key = (source_system, target_system, stream_id, kind)
        if log_key in self._policy_logged:
            return
        self._policy_logged.add(log_key)
        if kind == "passthrough":
            logger.debug(
                "(%s) *TALKER ALIAS* passthrough '%s' via %s -> %s stream %s",
                source_system, text, via, target_system, int_id(stream_id),
            )
        else:
            logger.debug(
                "(%s) *TALKER ALIAS* inject '%s'%s via %s -> %s stream %s",
                source_system, text, suffix, via, target_system, int_id(stream_id),
            )

    def packets_for_stream(
        self,
        source_system: str,
        rf_src: bytes,
        stream_id: bytes,
        get_passthrough_blocks: Any,
        *,
        target_system: str | None = None,
        fallback_inject: bool = False,
    ) -> list[bytes] | None:
        """Return DMRA packets to send on VHEAD, or None if TA disabled / nothing to send."""
        settings = talker_alias_settings(self._config, source_system)
        if not settings["enabled"]:
            return None
        target = target_system or source_system
        via = "repeat" if source_system == target else "bridge"
        mode = settings["mode"]
        blocks = get_passthrough_blocks(source_system, stream_id) if get_passthrough_blocks else None
        have_passthrough = bool(blocks and passthrough_complete(blocks))
        if mode == "passthrough":
            if not have_passthrough:
                return None
            text = decode_ta_from_blocks(blocks)
            self._log_policy_once(
                source_system, target, stream_id, "passthrough", text, via=via,
            )
            return passthrough_packets_from_blocks(rf_src, blocks)
        if mode == "inject":
            text = format_talker_alias_text(self._config, rf_src)
            self._log_policy_once(
                source_system, target, stream_id, "inject", text, via=via,
            )
            return build_dmra_packets(rf_src, text, settings["text_formats"][0])
        # both: prefer the source's own TA. If a valid MMDVM DMRA buffer arrived,
        # relay it. Otherwise the source's embedded LC (e.g. MMDVM voice) is passed
        # through unchanged in the DMRD voice, so do NOT inject a template here.
        if have_passthrough:
            text = decode_ta_from_blocks(blocks)
            self._log_policy_once(
                source_system, target, stream_id, "passthrough", text, via=via,
            )
            return passthrough_packets_from_blocks(rf_src, blocks)
        # Legacy resolve_ta (both): inject template when no DMRA buffer yet (VHEAD).
        text = format_talker_alias_text(self._config, rf_src)
        suffix = " (no source TA yet)" if fallback_inject else ""
        self._log_policy_once(
            source_system, target, stream_id, "inject", text, suffix=suffix, via=via,
        )
        return build_dmra_packets(rf_src, text, settings["text_formats"][0])

    def embedded_emblc_for_stream(
        self,
        source_system: str,
        rf_src: bytes,
        stream_id: bytes,
        get_passthrough_blocks: Any,
        *,
        target_system: str | None = None,
        fallback_inject: bool = False,
    ) -> tuple[list[dict[int, Any]], int] | None:
        """Return (encode_emblc dicts, block count 1–4) for the embedded TA to overlay, or None.

        The group LC is rewritten separately for the destination TG; this only supplies the
        Talker Alias overlaid on alternate superframes:

        - **passthrough / both with source TA:** re-encode the source's decoded TA blocks
          (round-trips losslessly via the fixed ``encode_emblc``).
        - **inject / both fallback:** the configured template.
        - otherwise (no TA available yet): ``None`` — only the destination group LC is sent.
        """
        settings = talker_alias_settings(self._config, source_system)
        if not settings["enabled"]:
            return None
        mode = settings["mode"]
        target = target_system or source_system
        via = "repeat" if source_system == target else "bridge"
        log_key = (source_system, target, stream_id)

        def _log(text: str, suffix: str) -> None:
            if log_key in self._embed_logged:
                return
            self._embed_logged.add(log_key)
            logger.debug(
                "(%s) *TALKER ALIAS* embed inject '%s'%s via %s -> %s stream %s",
                source_system, text, suffix, via, target, int_id(stream_id),
            )

        blocks = get_passthrough_blocks(source_system, stream_id) if get_passthrough_blocks else None
        if mode in ("passthrough", "both") and blocks and passthrough_complete(blocks):
            _log(decode_ta_from_blocks(blocks), " (source TA)")
            return self._ta_emblc.encode_blocks(blocks)
        if mode in ("inject", "both") or fallback_inject:
            suffix = "" if mode == "inject" else " (no source TA yet)"
            text = format_talker_alias_text(self._config, rf_src)
            _log(text, suffix)
            return self._ta_emblc.encode_text(text, text_formats=settings["text_formats"])
        return None
