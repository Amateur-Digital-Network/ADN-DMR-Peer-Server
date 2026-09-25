# ADN DMR Peer Server - plugin send rules
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

"""What a plugin may send, and the per-plugin permission read from ``PLUGINS.send``."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, NamedTuple

from ....domain import HBPF_DATA_SYNC, HBPF_SLT_VHEAD, HBPF_SLT_VTERM, HBPF_VOICE, HBPF_VOICE_SYNC
from ....domain.mesh_admission import call_attributes

# CSBK, data header, rate 1/2 and rate 3/4 data blocks: ARS, LRRP, SMS and the like.
PLUGIN_SENDABLE_DTYPES = frozenset({3, 6, 7, 8})
UNIT_DATA = "unit_data"
GROUP_VOICE = "group_voice"
DEFAULT_MAX_FRAMES_PER_S = 40.0
# A voice stream is one frame every 60 ms (~17/s), at most one per talkgroup at a time.
VOICE_FRAMES_PER_S_PER_TG = 18.0


class DmrdHeader(NamedTuple):
    seq: int
    rf_src: bytes
    dst_id: bytes
    peer_id: bytes
    slot: int
    call_type: str
    frame_type: int
    dtype_vseq: int
    stream_id: bytes


def parse_dmrd_header(pkt: bytes) -> DmrdHeader | None:
    """The HBP DMRD header fields of any call type (group, vcsbk or unit)."""
    if len(pkt) < 53 or pkt[:4] != b"DMRD":
        return None
    attrs = call_attributes(pkt[15])
    return DmrdHeader(
        seq=pkt[4],
        rf_src=pkt[5:8],
        dst_id=pkt[8:11],
        peer_id=pkt[11:15],
        slot=attrs.slot,
        call_type=attrs.call_type,
        frame_type=attrs.frame_type,
        dtype_vseq=attrs.dtype_vseq,
        stream_id=pkt[16:20],
    )


def plugin_frame_kind(call_type: str, frame_type: int, dtype_vseq: int) -> str | None:
    """UNIT_DATA, GROUP_VOICE, or None for anything a plugin may not send (e.g. private voice)."""
    if call_type == "unit":
        return UNIT_DATA if frame_type == HBPF_DATA_SYNC and dtype_vseq in PLUGIN_SENDABLE_DTYPES else None
    if call_type == "group":
        if frame_type in (HBPF_VOICE, HBPF_VOICE_SYNC):
            return GROUP_VOICE
        if frame_type == HBPF_DATA_SYNC and dtype_vseq in (HBPF_SLT_VHEAD, HBPF_SLT_VTERM):
            return GROUP_VOICE
    return None


def is_plugin_sendable(call_type: str, frame_type: int, dtype_vseq: int) -> bool:
    return plugin_frame_kind(call_type, frame_type, dtype_vseq) is not None


@dataclass(frozen=True)
class SendPermission:
    allowed_src_ids: frozenset[int]
    max_frames_per_s: float
    # Talkgroups the plugin may speak on (group voice); empty: unit data only.
    group_voice_tgs: frozenset[int] = frozenset()


ANNOUNCEMENTS_PLUGIN = "voice-announcements"


def announcements_grant(server_config: dict[str, Any]) -> dict[str, Any]:
    """What the official announcements plugin may send, read from ``VOICE``.

    The talkgroups and DMR IDs its items use (enabled or not, so enabling one in
    adn-voice.yaml needs no restart), plus the server voice ID: announcements kept
    working unchanged when they moved out of the core.
    """
    from ...server_voice import announcement_item_dmr_id, server_voice_dmr_id

    voice = server_config.get("VOICE") or {}
    ids = {server_voice_dmr_id(server_config)}
    tgs: set[int] = set()
    for section in ("ANNOUNCEMENTS", "TTS_ANNOUNCEMENTS"):
        for item in voice.get(section) or []:
            if not isinstance(item, dict):
                continue
            ids.add(announcement_item_dmr_id(item, server_config))
            try:
                if int(item.get("TG", 0)):
                    tgs.add(int(item["TG"]))
            except (TypeError, ValueError):
                continue
    return {"allowed_src_ids": sorted(ids), "group_voice_tgs": sorted(tgs)}


def send_permission(server_config: dict[str, Any], plugin: str) -> SendPermission | None:
    """The plugin's entry in ``PLUGINS.send``, or None when it may not send.

    An entry without source IDs grants nothing. The allowlist and the rate limit
    guard against a *buggy* plugin (sending as a radio, flooding the mesh); they are
    no sandbox: a plugin runs in-process with the live config and could rewrite its
    own entry, so only install plugins you trust. The official announcements plugin
    without an entry gets what ``VOICE`` configures (see ``announcements_grant``).
    """
    plugins_cfg = server_config.get("PLUGINS") or {}
    if plugins_cfg.get("master_kill"):
        return None
    entry = (plugins_cfg.get("send") or {}).get(plugin)
    if entry is None and plugin == ANNOUNCEMENTS_PLUGIN:
        entry = announcements_grant(server_config)
    if not isinstance(entry, dict):
        return None
    try:
        ids = frozenset(int(i) for i in entry.get("allowed_src_ids") or ())
        tgs = frozenset(int(t) for t in entry.get("group_voice_tgs") or ())
        default_rate = DEFAULT_MAX_FRAMES_PER_S + VOICE_FRAMES_PER_S_PER_TG * len(tgs)
        rate = float(entry.get("max_frames_per_s", default_rate))
    except (TypeError, ValueError):
        return None
    if not ids or not math.isfinite(rate) or rate <= 0:
        return None
    return SendPermission(ids, rate, tgs)
