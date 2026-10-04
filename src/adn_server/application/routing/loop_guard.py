# ADN DMR Peer Server - loop guard
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

"""Loop guard: group voice that comes back through a transcoding bridge.

YSF2DMR, DVSwitch, ASL/EchoLink gateways re-encode the audio and start a new
stream ID, so stream-ID loop control never sees a loop through them, and each
lap restarts the 180 s source timeout. They do keep the caller's ID, so the echo
arrives with the same ``rf_src`` on the same TG while the original over is
still active, or right after it. One person cannot transmit from two ingress
points at once: such a stream is an echo.

A stream is an echo when, at its start, the same ``rf_src`` has another stream
(different stream ID) on the **same TG** from **another ingress** that is
active or ended less than ``GLOBAL.LOOP_GUARD_HOLD`` seconds ago (default 1 s,
always under 2 s). Bridges that relay to another TG are not loops and are left
alone.

An ingress is a system and peer on HBP. All OPENBRIDGE links together are one
ingress, the mesh: a remote caller's next over may reach this server first over
another link, and that is a re-key, not an echo. A real loop is still caught
where the caller is local (HBP vs a bridge peer, or HBP vs the mesh), and so is
a local bridge peer echoing a remote caller (the mesh vs HBP).

A bridge echo starts while the original is still on air (bridge delay under
~1 s), so the hold only has to cover very short overs. It stays below the
parrot's fixed replay delay (``PLAYBACK_DELAY_S``, 2.0 s), so the parrot is
never taken for a loop whatever its system or TG is called.

Per system ``LOOP_GUARD``: ``log`` (default) writes one ``*LoopGuard*`` line per
echo and lets it through, ``true`` drops it, ``false`` neither checks nor
records streams entering there (the parrot replays each over with the caller's
ID 2 s after it ends; the hold already keeps it out, ``false`` is an extra
option). Server voice IDs and plugin frames are exempt.

Cost: one verdict per stream, cached by stream ID; frames of a stream already
bound to its slot never get here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ...domain import int_id
from ..playback_use_cases import PLAYBACK_DELAY_S
from ..server_voice import all_server_voice_ids

logger = logging.getLogger(__name__)

LOOP_GUARD_OFF = "off"
LOOP_GUARD_LOG = "log"
LOOP_GUARD_DROP = "drop"

DEFAULT_LOOP_GUARD_HOLD_S = 1.0
# Exclusive upper bound: the parrot replays an over PLAYBACK_DELAY_S after it ends;
# a hold reaching it would take the replay for a loop.
MAX_LOOP_GUARD_HOLD_S = PLAYBACK_DELAY_S
# Verdicts and legs older than this are forgotten by trim(): longer than the
# 180 s source timeout plus the hold, so a live over is never forgotten.
_FORGET_AFTER_S = 300.0


def loop_guard_mode(sys_cfg: dict[str, Any] | None) -> str:
    """``LOOP_GUARD`` of a system: ``log`` unless set to ``true`` (drop) or ``false`` (off)."""
    value = (sys_cfg or {}).get("LOOP_GUARD", LOOP_GUARD_LOG)
    if value is True:
        return LOOP_GUARD_DROP
    if value is False:
        return LOOP_GUARD_OFF
    return LOOP_GUARD_LOG


def loop_guard_hold(config: dict[str, Any]) -> float:
    value = config.get("GLOBAL", {}).get("LOOP_GUARD_HOLD", DEFAULT_LOOP_GUARD_HOLD_S)
    try:
        hold = float(value)
    except (TypeError, ValueError):
        return DEFAULT_LOOP_GUARD_HOLD_S
    return hold if 0 < hold < MAX_LOOP_GUARD_HOLD_S else DEFAULT_LOOP_GUARD_HOLD_S


# Ingress of every OPENBRIDGE link: the whole mesh counts as one.
_MESH_INGRESS: tuple[str | None, bytes | None] = (None, None)


@dataclass(slots=True)
class _Leg:
    system: str  # where the stream's state lives (STATUS), even on the mesh
    peer_id: bytes | None  # None on OPENBRIDGE
    ingress: tuple[str | None, bytes | None]
    slot: int
    stream_id: bytes
    start: float


@dataclass(frozen=True, slots=True)
class LoopEcho:
    """A stream judged an echo of ``original_*``; ``drop`` follows the ingress system's mode."""

    drop: bool
    original_system: str
    original_peer_id: bytes | None
    original_stream_id: bytes
    gap: float  # seconds since the original's last frame (0 while it is still active)


class LoopGuard:
    """Server-wide table of who is talking where, one instance per server (see ``loop_guard``)."""

    def __init__(self) -> None:
        self._legs: dict[tuple[bytes, bytes], _Leg] = {}
        self._verdicts: dict[bytes, tuple[LoopEcho | None, float]] = {}

    def check(
        self,
        config: dict[str, Any],
        protocols: dict[str, Any],
        *,
        system_name: str,
        peer_id: bytes | None,
        slot: int,
        rf_src: bytes,
        dst_id: bytes,
        stream_id: bytes,
        pkt_time: float,
    ) -> LoopEcho | None:
        """Verdict for a group voice stream, computed on its first frame and cached."""
        cached = self._verdicts.get(stream_id)
        if cached is not None:
            return cached[0]
        echo = self._evaluate(
            config, protocols, system_name, peer_id, slot, rf_src, dst_id, stream_id, pkt_time,
        )
        self._verdicts[stream_id] = (echo, pkt_time)
        return echo

    def trim(self, now: float) -> None:
        """Forget verdicts and legs nobody can still be talking on (stream trimmer)."""
        cutoff = now - _FORGET_AFTER_S
        for sid in [sid for sid, (_e, t) in self._verdicts.items() if t < cutoff]:
            del self._verdicts[sid]
        for key in [key for key, leg in self._legs.items() if leg.start < cutoff]:
            del self._legs[key]

    def _evaluate(
        self,
        config: dict[str, Any],
        protocols: dict[str, Any],
        system_name: str,
        peer_id: bytes | None,
        slot: int,
        rf_src: bytes,
        dst_id: bytes,
        stream_id: bytes,
        pkt_time: float,
    ) -> LoopEcho | None:
        sys_cfg = config.get("SYSTEMS", {}).get(system_name)
        mode = loop_guard_mode(sys_cfg)
        if mode == LOOP_GUARD_OFF:
            return None
        if (sys_cfg or {}).get("MODE") == "OPENBRIDGE":
            peer_id = None
            ingress = _MESH_INGRESS
        else:
            ingress = (system_name, peer_id)
        key = (bytes(rf_src), bytes(dst_id))
        leg = self._legs.get(key)
        if (
            leg is not None
            and leg.stream_id != stream_id
            and leg.ingress != ingress
            and int_id(rf_src) not in all_server_voice_ids(config)
        ):
            last = _last_activity(leg, protocols, config)
            if last is not None:
                gap = max(0.0, pkt_time - last)
                if gap <= loop_guard_hold(config):
                    echo = LoopEcho(mode == LOOP_GUARD_DROP, leg.system, leg.peer_id, leg.stream_id, gap)
                    _log_echo(system_name, peer_id, rf_src, dst_id, stream_id, echo)
                    return echo
        self._legs[key] = _Leg(system_name, peer_id, ingress, slot, stream_id, pkt_time)
        return None


def _last_activity(leg: _Leg, protocols: dict[str, Any], config: dict[str, Any]) -> float | None:
    """When the original stream last carried a frame; None if its state is gone (long over)."""
    status = getattr(protocols.get(leg.system), "STATUS", None)
    if not isinstance(status, dict):
        return None
    if config.get("SYSTEMS", {}).get(leg.system, {}).get("MODE") == "OPENBRIDGE":
        st = status.get(leg.stream_id)
        if not isinstance(st, dict):
            return None
        return float(st.get("LAST", st.get("START", leg.start)) or leg.start)
    slot_st = status.get(leg.slot)
    if not isinstance(slot_st, dict) or slot_st.get("RX_STREAM_ID") != leg.stream_id:
        return None  # the slot moved on to another stream: the original ended a while ago
    return float(slot_st.get("RX_TIME", leg.start) or leg.start)


def _log_echo(
    system_name: str,
    peer_id: bytes | None,
    rf_src: bytes,
    dst_id: bytes,
    stream_id: bytes,
    echo: LoopEcho,
) -> None:
    logger.warning(
        "(%s) *LoopGuard* SUB %s TG %s STREAM %s from peer %s echoes STREAM %s on %s peer %s "
        "(%.1fs after it) -- %s",
        system_name,
        int_id(rf_src),
        int_id(dst_id),
        int_id(stream_id),
        int_id(peer_id) if peer_id is not None else "-",
        int_id(echo.original_stream_id),
        echo.original_system,
        int_id(echo.original_peer_id) if echo.original_peer_id is not None else "-",
        echo.gap,
        "dropped" if echo.drop else "logged only (LOOP_GUARD: log)",
    )


def loop_guard(config: dict[str, Any]) -> LoopGuard:
    """The guard for this server, kept beside the other runtime tables (like ``_MESH_SESSIONS``)."""
    guard = config.get("_LOOP_GUARD")
    if not isinstance(guard, LoopGuard):
        guard = LoopGuard()
        config["_LOOP_GUARD"] = guard
    return guard


def loop_guard_drops(
    config: dict[str, Any],
    protocols: dict[str, Any],
    **stream: Any,
) -> bool:
    """True when the guard says drop this group voice stream (see ``LoopGuard.check``)."""
    echo = loop_guard(config).check(config, protocols, **stream)
    return echo is not None and echo.drop
