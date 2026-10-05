# ADN DMR Peer Server - bridge helpers
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
#
# Derived from ADN DMR Server / FreeDMR / HBlink. Original license:
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

"""Shared bridge routing helpers (no Twisted)."""

from __future__ import annotations

import time
from typing import Any

from ...domain import HBPF_DATA_SYNC, HBPF_SLT_VHEAD, bytes_3, bytes_4, int_id
from ...domain.hbp_protocol import HBPF_SLT_VTERM, STREAM_TO
from ...domain.mesh_session import obp_session
from ..server_voice import DEFAULT_SERVER_VOICE_ID

PeerVoiceSlotRow = dict[str, Any]
PeerVoiceSlotMap = dict[int, PeerVoiceSlotRow]

# A per-peer downlink voice session with no frames for this long is considered
# dead (VTERM lost or stream abandoned). Matches the legacy bridge_master idle
# loop threshold (bridge_master.py ~607: RX_TIME < now - 5).
_STALE_PEER_SESSION_TIMEOUT = 5.0

RF_MODE_SIMPLEX = "simplex"
RF_MODE_DUPLEX = "duplex"
# MMDVMHost DMO: downlink DMRD with TS1 bit set is dropped; only TS2 passes (DMRNetwork.cpp).
SIMPLEX_VOICE_SLOT = 2


def _peer_freq_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip("\x00").strip()
    return str(value or "").strip()


def parse_peer_slots_code(slots: Any) -> int | None:
    """MMDVM RPTC ``SLOTS`` byte: 4=simplex, 1–3=duplex (per MMDVMHost / Wireshark dissector)."""
    if isinstance(slots, bytes):
        raw = slots[:1]
    elif slots is None:
        return None
    else:
        text = str(slots).strip()
        raw = text[:1].encode("ascii", errors="ignore") if text else b""
    if not raw:
        return None
    try:
        return int(raw.decode("ascii", errors="ignore"))
    except ValueError:
        return None


def derive_peer_rf_mode(peer: dict[str, Any]) -> str:
    """Classify hotspot RF from RPTC ``SLOTS`` and matching RX/TX frequencies."""
    slots_i = parse_peer_slots_code(peer.get("SLOTS"))
    if slots_i == 4:
        return RF_MODE_SIMPLEX
    rx = _peer_freq_text(peer.get("RX_FREQ"))
    tx = _peer_freq_text(peer.get("TX_FREQ"))
    if rx and tx and rx == tx:
        return RF_MODE_SIMPLEX
    return RF_MODE_DUPLEX


def peer_rf_mode(peer: dict[str, Any]) -> str:
    """Cached or derived simplex/duplex mode for downlink and monitor."""
    cached = peer.get("RF_MODE")
    if cached in (RF_MODE_SIMPLEX, RF_MODE_DUPLEX):
        return str(cached)
    return derive_peer_rf_mode(peer)


def peer_is_simplex(peer: dict[str, Any]) -> bool:
    return peer_rf_mode(peer) == RF_MODE_SIMPLEX


def _slot_leg_active(slot_st: dict[str, Any], leg: str, pkt_time: float) -> bool:
    """True when the slot's ``RX`` or ``TX`` leg carries voice (within STREAM_TO)."""
    leg_type = slot_st.get(f"{leg}_TYPE")
    if leg_type is None or leg_type == HBPF_SLT_VTERM:
        return False
    return (pkt_time - float(slot_st.get(f"{leg}_TIME", 0) or 0)) < STREAM_TO


def slot_has_active_voice(slot_st: dict[str, Any], pkt_time: float) -> bool:
    """True when the slot has an active group-voice RX or TX leg (within STREAM_TO)."""
    return _slot_leg_active(slot_st, "RX", pkt_time) or _slot_leg_active(slot_st, "TX", pkt_time)


def slot_voice_held_by_other_stream(slot_st: dict[str, Any], stream_id: bytes, pkt_time: float) -> bool:
    """Like ``slot_has_active_voice``, but ``stream_id``'s own TX leg does not count."""
    if _slot_leg_active(slot_st, "RX", pkt_time):
        return True
    return slot_st.get("TX_STREAM_ID") != stream_id and _slot_leg_active(slot_st, "TX", pkt_time)


def _slot_last_voice_activity(slot_st: dict[str, Any]) -> tuple[bytes, float]:
    """Most recent RX/TX TG and timestamp on this slot."""
    rx_tg = slot_st.get("RX_TGID", b"\x00\x00\x00")
    rx_t = float(slot_st.get("RX_TIME", 0))
    tx_tg = slot_st.get("TX_TGID", b"\x00\x00\x00")
    tx_t = float(slot_st.get("TX_TIME", 0))
    if tx_t >= rx_t:
        return tx_tg, tx_t
    return rx_tg, rx_t


def slot_in_group_hangtime(
    slot_st: dict[str, Any],
    incoming_tgid_b: bytes,
    pkt_time: float,
    group_hangtime: float,
) -> bool:
    """True when the slot is idle but other TGs are blocked for GROUP_HANGTIME seconds."""
    hang = float(group_hangtime or 0)
    if hang <= 0:
        return False
    if slot_has_active_voice(slot_st, pkt_time):
        return False
    last_tg, last_t = _slot_last_voice_activity(slot_st)
    if last_t <= 0:
        return False
    if bytes_4(int_id(incoming_tgid_b)) == bytes_4(int_id(last_tg)):
        return False
    return (pkt_time - last_t) < hang


def hbp_slot_blocks_group_voice(
    slot_st: dict[str, Any],
    incoming_tgid_b: bytes,
    stream_id: bytes,
    pkt_time: float,
    group_hangtime: float,
    *,
    allow_same_stream: bool = True,
    is_vterm: bool = False,
) -> bool:
    """True when group voice must not be routed or repeated to this slot.

    Active QSO: any other stream is blocked (independent of GROUP_HANGTIME).
    Post-VTERM: other TGs are blocked for ``group_hangtime`` seconds from config.
    """
    if allow_same_stream and stream_id:
        if stream_id == slot_st.get("RX_STREAM_ID"):
            return False
        if stream_id == slot_st.get("TX_STREAM_ID"):
            rx_stream = slot_st.get("RX_STREAM_ID")
            rx_stream_active = bool(rx_stream) and int_id(rx_stream) != 0
            # RX leg is only "genuinely active" if it received voice within
            # STREAM_TO — a stale RX_STREAM_ID from an earlier, ended stream
            # must not block same-TX-stream packets (legacy contention compares
            # TGID + time, never stream IDs).
            rx_type = slot_st.get("RX_TYPE")
            rx_genuinely_active = (
                rx_stream_active
                and stream_id != rx_stream
                and rx_type is not None
                and rx_type != HBPF_SLT_VTERM
                and (pkt_time - float(slot_st.get("RX_TIME", 0) or 0)) < STREAM_TO
            )
            if rx_genuinely_active:
                pass
            else:
                return False
    if slot_has_active_voice(slot_st, pkt_time):
        return True
    return slot_in_group_hangtime(slot_st, incoming_tgid_b, pkt_time, group_hangtime)


def slot_status_peer_owner(slot_st: dict[str, Any]) -> bytes | None:
    """Hotspot radio id that last owned this slot STATUS row (RX preferred, then TX)."""
    rx = slot_st.get("RX_PEER")
    if rx is not None and int_id(rx) != 0:
        return bytes_4(int_id(rx))
    tx = slot_st.get("TX_PEER")
    if tx is not None and int_id(tx) != 0:
        return bytes_4(int_id(tx))
    return None


def peer_key_in_peers(peer_id: bytes, peers: dict[Any, Any] | None) -> bool:
    """True when ``peer_id`` is a connected hotspot key in ``PEERS``."""
    if not peers:
        return False
    pk = bytes_4(int_id(peer_id))
    if pk in peers:
        return True
    for key in peers:
        try:
            if bytes_4(int_id(key)) == pk:
                return True
        except (TypeError, ValueError):
            continue
    return False


def slot_status_hotspot_owner(
    slot_st: dict[str, Any],
    peers: dict[Any, Any] | None = None,
) -> bytes | None:
    """Connected hotspot owning this slot row; ignores bridge ``TX_PEER`` (e.g. OBP 73010)."""
    for field in ("RX_PEER", "TX_PEER"):
        raw = slot_st.get(field)
        if raw is None or int_id(raw) == 0:
            continue
        pk = bytes_4(int_id(raw))
        if peers is not None and not peer_key_in_peers(pk, peers):
            continue
        return pk
    return None


def _peer_transmit_hangtime_blocks(
    hang_row: tuple[int, float] | None,
    incoming_tgid_b: bytes,
    pkt_time: float,
    group_hangtime: float,
) -> bool:
    """True when hotspot RF transmit hangtime blocks a different TG."""
    hang = float(group_hangtime or 0)
    if hang <= 0 or hang_row is None:
        return False
    last_tg, last_t = hang_row
    return int_id(incoming_tgid_b) != int(last_tg) and (pkt_time - float(last_t)) < hang


def _peer_status_rx_hangtime_blocks(
    peer_id: bytes,
    slot_st: dict[str, Any],
    incoming_tgid_b: bytes,
    pkt_time: float,
    group_hangtime: float,
) -> bool:
    """Ingress RX hangtime for this hotspot (ignores bridge TX_TGID on shared STATUS)."""
    pk = bytes_4(int_id(peer_id))
    if bytes_4(int_id(slot_st.get("RX_PEER", b""))) != pk:
        return False
    if slot_has_active_voice(slot_st, pkt_time):
        return False
    rx_tg = slot_st.get("RX_TGID", b"\x00\x00\x00")
    rx_t = float(slot_st.get("RX_TIME", 0))
    hang = float(group_hangtime or 0)
    if hang <= 0 or rx_t <= 0:
        return False
    if int_id(incoming_tgid_b) == int_id(rx_tg):
        return False
    return (pkt_time - rx_t) < hang


def _peer_single_locked_tgid(
    peer: dict[str, Any],
    sys_cfg: dict[str, Any],
    *,
    peer_id: bytes | None = None,
    now: float | None = None,
    prefer_slot: int | None = None,
) -> int | None:
    """First active SINGLE=1 session TG (``prefer_slot`` checked before the other TS)."""
    slots: list[int] = []
    if prefer_slot is not None:
        slots.append(int(prefer_slot))
    for voice_slot in (1, 2):
        if prefer_slot is not None and voice_slot == int(prefer_slot):
            continue
        slots.append(voice_slot)
    for voice_slot in slots:
        locked = peer_single_exclusive_tgid(
            peer, voice_slot, sys_cfg, peer_id=peer_id, now=now,
        )
        if locked is not None:
            return locked
    return None


def peer_single_same_tg_foreign_tx_blocks(
    peer: dict[str, Any],
    peer_id: bytes,
    incoming_tgid_b: bytes,
    stream_id: bytes,
    slot_st: dict[str, Any],
    sys_cfg: dict[str, Any] | None,
    *,
    pkt_time: float,
) -> bool:
    """SINGLE=1 UA on TG T: block another peer's stream on the same TG (slot busy)."""
    if not sys_cfg or not peer_single_mode(peer, sys_cfg):
        return False
    incoming = int_id(incoming_tgid_b)
    locked = _peer_single_locked_tgid(
        peer, sys_cfg, peer_id=peer_id, now=pkt_time,
    )
    if locked is None or int(locked) != incoming:
        return False
    if not slot_has_active_voice(slot_st, pkt_time):
        return False
    slot_tg = int_id(slot_st.get("RX_TGID", b"\x00\x00\x00"))
    if slot_tg != incoming:
        return False
    owner = slot_st.get("RX_PEER") or slot_st.get("TX_PEER")
    pk = bytes_4(int_id(peer_id))
    if owner is None or int_id(owner) == 0 or bytes_4(int_id(owner)) == pk:
        return False
    leg_stream = slot_st.get("RX_STREAM_ID") or slot_st.get("TX_STREAM_ID")
    if stream_id and leg_stream == stream_id:
        return False
    return True


def peer_hotspot_voice_slot_busy_reason(
    peer_id: bytes,
    voice_slot: int,
    stream_id: bytes,
    incoming_tgid_b: bytes,
    slot_st: dict[str, Any],
    peer_slots: PeerVoiceSlotMap | None,
    hang_row: tuple[int, float] | None,
    pkt_time: float,
    group_hangtime: float,
    *,
    peers: dict[Any, Any] | None = None,
    peer: dict[str, Any] | None = None,
    sys_cfg: dict[str, Any] | None = None,
) -> str | None:
    """Reason this hotspot must not receive another group stream on ``voice_slot``, or None.

    Same logic as ``peer_hotspot_voice_slot_busy`` (which just checks ``is not
    None``) -- kept as one function so the diagnostic reason can never drift
    from the actual accept/reject decision.

    Hard rules (SINGLE=0 and SINGLE=1):

    - **Transmitting** (``ingress``): drop every downlink byte until VTERM clears the slot.
    - **Listening** on TG *T*: drop every byte for TG *U* ≠ *T* on this RF slot (no hangtime
      exception for another OPTIONS/UA TG).
    - **Bridge hold**: while an ACTIVE bridge leg keeps *T* on the slot, foreign legs on
      *U* ≠ *T* stay dropped (OBP or HBP).
    - Same ``stream_id`` on the same TG continues one call leg.
    """
    pk = bytes_4(int_id(peer_id))
    if peer is None and peers is not None:
        peer = peers.get(peer_id) or peers.get(pk)
    if _peer_transmit_hangtime_blocks(hang_row, incoming_tgid_b, pkt_time, group_hangtime):
        return f"GROUP_HANGTIME: recent local transmit hanging on slot {voice_slot}"
    incoming_tgid = int_id(incoming_tgid_b)
    active = (peer_slots or {}).get(int(voice_slot))
    if isinstance(active, dict):
        active_tgid = int(active.get("tgid", 0) or 0)
        active_stream = active.get("stream_id")
        active_time = float(active.get("time", 0) or 0)
        age = pkt_time - active_time
        if isinstance(active, dict) and active_time > 0 and age >= _STALE_PEER_SESSION_TIMEOUT:
            peer_slots.pop(int(voice_slot), None)
            active = None
        if isinstance(active, dict) and active.get("ingress"):
            return f"peer is transmitting (ingress) on slot {voice_slot}"
        if isinstance(active, dict) and active.get("bridge_hold") and active_tgid and incoming_tgid != active_tgid:
            if age <= group_hangtime:
                return f"bridge hold: TG {active_tgid} active on slot {voice_slot}"
            peer_slots.pop(int(voice_slot), None)
            active = None
        if isinstance(active, dict) and stream_id and active_stream:
            if active_stream == stream_id:
                pass
            elif active_tgid and active_tgid == incoming_tgid:
                if age >= STREAM_TO:
                    peer_slots.pop(int(voice_slot), None)
                else:
                    return f"TG {incoming_tgid} already active on slot {voice_slot} with a different stream"
            elif active_tgid and incoming_tgid != active_tgid:
                return f"slot {voice_slot} busy with different TG {active_tgid}"
            else:
                return f"slot {voice_slot} busy with an unidentified active stream"
        elif isinstance(active, dict):
            if active_tgid and active_tgid == incoming_tgid:
                pass
            else:
                return (
                    f"slot {voice_slot} already occupied by TG {active_tgid}"
                    if active_tgid
                    else f"slot {voice_slot} already occupied by another stream"
                )
    if isinstance(peer, dict) and peer_single_blocks_foreign_same_tg_downlink(
        peer, pk, voice_slot, incoming_tgid_b, peer_slots, sys_cfg, now=pkt_time,
    ):
        return f"SINGLE mode: local UA lock on TG {incoming_tgid} blocks foreign downlink on slot {voice_slot}"
    if isinstance(peer, dict) and peer_single_same_tg_foreign_tx_blocks(
        peer, pk, incoming_tgid_b, stream_id, slot_st, sys_cfg, pkt_time=pkt_time,
    ):
        return f"SINGLE mode: peer is transmitting a different call on TG {incoming_tgid}"
    if bytes_4(int_id(slot_st.get("RX_PEER", b""))) == pk:
        rx_active = (
            slot_st.get("RX_TYPE") is not None
            and slot_st.get("RX_TYPE") != HBPF_SLT_VTERM
            and (pkt_time - float(slot_st.get("RX_TIME", 0))) < STREAM_TO
        )
        if rx_active and stream_id != slot_st.get("RX_STREAM_ID"):
            return f"slot {voice_slot} STATUS RX owner with a different active stream"
        if _peer_status_rx_hangtime_blocks(
            peer_id, slot_st, incoming_tgid_b, pkt_time, group_hangtime,
        ):
            return f"GROUP_HANGTIME: recent STATUS RX hangtime on slot {voice_slot}"
    return None


def peer_hotspot_voice_slot_busy(
    peer_id: bytes,
    voice_slot: int,
    stream_id: bytes,
    incoming_tgid_b: bytes,
    slot_st: dict[str, Any],
    peer_slots: PeerVoiceSlotMap | None,
    hang_row: tuple[int, float] | None,
    pkt_time: float,
    group_hangtime: float,
    *,
    peers: dict[Any, Any] | None = None,
    peer: dict[str, Any] | None = None,
    sys_cfg: dict[str, Any] | None = None,
) -> bool:
    """True when this hotspot must not receive another group stream on ``voice_slot``."""
    return (
        peer_hotspot_voice_slot_busy_reason(
            peer_id,
            voice_slot,
            stream_id,
            incoming_tgid_b,
            slot_st,
            peer_slots,
            hang_row,
            pkt_time,
            group_hangtime,
            peers=peers,
            peer=peer,
            sys_cfg=sys_cfg,
        )
        is not None
    )


def master_per_peer_slot_contention(
    config: dict[str, Any],
    system_name: str,
    system_cfg: dict[str, Any],
    *,
    connected_count: int = 0,
) -> bool:
    """True when slot busy/hangtime applies per hotspot, not globally on the MASTER row."""
    del config, system_name, connected_count
    return system_cfg.get("MODE") == "MASTER"


def inject_only_defer_obp_hbp_slot_contention(
    config: dict[str, Any],
    target_system: str,
    target_system_cfg: dict[str, Any],
    *,
    source_is_obp: bool,
    source_is_hbp: bool = False,
    connected_count: int = 0,
) -> bool:
    """Whether ``to_target`` should skip global MASTER slot STATUS contention.

    Defer to ``send_peer`` (same as REPEAT): per-peer ``hbp_slot_blocks_group_voice_for_peer``
    + OPTIONS/UA slot remap. Global STATUS on the bridge wire TS would block another
    hotspot's TG while a different peer is active on that TS.
    """
    if target_system_cfg.get("MODE") != "MASTER":
        return False
    if not master_per_peer_slot_contention(
        config, target_system, target_system_cfg, connected_count=connected_count,
    ):
        return False
    if source_is_obp:
        return True
    return source_is_hbp and connected_count > 1


_SERVER_VOICE_RF_SRC_LEGACY = 5000


def master_slot_holds_server_broadcast(
    slot_st: dict[str, Any],
    pkt_time: float,
    *,
    server_voice_rf_src: int | None = None,
    server_voice_rf_srcs: frozenset[int] | None = None,
) -> bool:
    """True when server scheduled/TTS voice holds the flat MASTER slot TX row.

    Announcements stamp TX_TYPE=VHEAD, TX_RFS=server voice ID, and refresh TX_TIME each frame.
    Re-apply global slot contention for OBP→MASTER when held so inject-only defer
    does not interleave mesh voice (legacy ``bridge_master`` TX_TGID/TX_TIME rules).
    """
    tx_rfs = int_id(slot_st.get("TX_RFS", b"\x00\x00\x00"))
    if server_voice_rf_srcs is not None:
        if tx_rfs not in server_voice_rf_srcs:
            return False
    elif server_voice_rf_src is not None:
        if tx_rfs != server_voice_rf_src:
            return False
    elif tx_rfs != DEFAULT_SERVER_VOICE_ID and tx_rfs != _SERVER_VOICE_RF_SRC_LEGACY:
        return False
    tx_type = slot_st.get("TX_TYPE")
    if tx_type is None or tx_type == HBPF_SLT_VTERM:
        return False
    tx_time = float(slot_st.get("TX_TIME", 0) or 0)
    if tx_time <= 0:
        return True
    return (pkt_time - tx_time) < STREAM_TO


_OBP_FLAT_TX_KEYS = (
    "TX_START",
    "TX_TGID",
    "TX_STREAM_ID",
    "TX_RFS",
    "TX_PEER",
    "TX_H_LC",
    "TX_T_LC",
    "TX_EMB_LC",
    "TX_TIME",
    "TX_TYPE",
)


def obp_deferred_bridge_tx_leg(slot_st: dict[str, Any], stream_id: bytes) -> dict[str, Any]:
    """Per-stream bridge TX stamp for inject-only OBP→MASTER legs on a shared slot row."""
    legs = slot_st.setdefault("TX_STREAMS", {})
    if not isinstance(legs, dict):
        legs = {}
        slot_st["TX_STREAMS"] = legs
    return legs.setdefault(stream_id, {})


def obp_flat_bridge_tx_idle(slot_st: dict[str, Any], pkt_time: float) -> bool:
    """True when flat ``TX_*`` on the slot is not carrying an active bridge leg."""
    tx_type = slot_st.get("TX_TYPE")
    if tx_type is None or tx_type == HBPF_SLT_VTERM:
        return True
    tx_time = float(slot_st.get("TX_TIME", 0) or 0)
    return pkt_time >= tx_time + STREAM_TO


def obp_bridge_tx_leg_active(leg: dict[str, Any], pkt_time: float) -> bool:
    tx_type = leg.get("TX_TYPE")
    if tx_type is None or tx_type == HBPF_SLT_VTERM:
        return False
    tx_time = float(leg.get("TX_TIME", 0) or 0)
    return pkt_time < tx_time + STREAM_TO


def obp_publish_flat_bridge_tx(slot_st: dict[str, Any], leg: dict[str, Any]) -> None:
    """Mirror one per-stream bridge leg onto the legacy flat ``TX_*`` slot row."""
    for key in _OBP_FLAT_TX_KEYS:
        if key in leg:
            slot_st[key] = leg[key]


def obp_sync_flat_bridge_tx_times(
    slot_st: dict[str, Any],
    leg: dict[str, Any],
    stream_id: bytes,
    pkt_time: float,
    dtype_vseq: int,
) -> None:
    """Refresh per-stream leg activity; mirror times to flat row only for the owner stream."""
    leg["TX_TIME"] = pkt_time
    leg["TX_TYPE"] = dtype_vseq
    if slot_st.get("TX_STREAM_ID") == stream_id:
        slot_st["TX_TIME"] = pkt_time
        slot_st["TX_TYPE"] = dtype_vseq


def obp_pick_active_bridge_tx_leg(
    slot_st: dict[str, Any],
    pkt_time: float,
) -> tuple[bytes | None, dict[str, Any] | None]:
    legs = slot_st.get("TX_STREAMS")
    if not isinstance(legs, dict):
        return None, None
    best_sid: bytes | None = None
    best_leg: dict[str, Any] | None = None
    best_time = -1.0
    for sid, leg in legs.items():
        if not isinstance(leg, dict) or not obp_bridge_tx_leg_active(leg, pkt_time):
            continue
        t = float(leg.get("TX_TIME", 0) or 0)
        if t > best_time:
            best_time = t
            best_sid = sid
            best_leg = leg
    return best_sid, best_leg


def obp_clear_flat_bridge_tx(slot_st: dict[str, Any]) -> None:
    slot_st["TX_TYPE"] = HBPF_SLT_VTERM
    slot_st["TX_STREAM_ID"] = b"\x00"
    slot_st["TX_TIME"] = 0.0


def obp_clear_deferred_bridge_tx_leg(
    slot_st: dict[str, Any],
    stream_id: bytes,
    pkt_time: float,
) -> None:
    """End one per-stream OBP bridge leg; republish flat ``TX_*`` for another active leg if any."""
    legs = slot_st.get("TX_STREAMS")
    if isinstance(legs, dict):
        legs.pop(stream_id, None)
    if slot_st.get("TX_STREAM_ID") == stream_id:
        repl_sid, repl_leg = obp_pick_active_bridge_tx_leg(slot_st, pkt_time)
        if repl_leg is not None and repl_sid is not None:
            obp_publish_flat_bridge_tx(slot_st, repl_leg)
        else:
            obp_clear_flat_bridge_tx(slot_st)


def hbp_slot_blocks_group_voice_for_peer_reason(
    slot_st: dict[str, Any],
    peer_id: bytes,
    incoming_tgid_b: bytes,
    stream_id: bytes,
    pkt_time: float,
    group_hangtime: float,
    *,
    per_peer: bool,
    peers: dict[Any, Any] | None = None,
    peer_slots: PeerVoiceSlotMap | None = None,
    peer_hang_row: tuple[int, float] | None = None,
    voice_slot: int | None = None,
    sys_cfg: dict[str, Any] | None = None,
) -> str | None:
    """Reason for ``hbp_slot_blocks_group_voice_for_peer``'s block, or None.

    Same logic as ``hbp_slot_blocks_group_voice_for_peer`` (which just checks
    ``is not None``) -- kept as one function so the diagnostic reason can
    never drift from the actual accept/reject decision.
    """
    if per_peer:
        if voice_slot is None:
            return None
        peer = None
        if peers is not None:
            pk = bytes_4(int_id(peer_id))
            peer = peers.get(peer_id) or peers.get(pk)
        return peer_hotspot_voice_slot_busy_reason(
            peer_id,
            int(voice_slot),
            stream_id,
            incoming_tgid_b,
            slot_st,
            peer_slots,
            peer_hang_row,
            pkt_time,
            group_hangtime,
            peers=peers,
            peer=peer if isinstance(peer, dict) else None,
            sys_cfg=sys_cfg,
        )
    if hbp_slot_blocks_group_voice(
        slot_st, incoming_tgid_b, stream_id, pkt_time, group_hangtime,
    ):
        return "global STATUS slot contention"
    return None


def hbp_slot_blocks_group_voice_for_peer(
    slot_st: dict[str, Any],
    peer_id: bytes,
    incoming_tgid_b: bytes,
    stream_id: bytes,
    pkt_time: float,
    group_hangtime: float,
    *,
    per_peer: bool,
    peers: dict[Any, Any] | None = None,
    peer_slots: PeerVoiceSlotMap | None = None,
    peer_hang_row: tuple[int, float] | None = None,
    voice_slot: int | None = None,
    sys_cfg: dict[str, Any] | None = None,
) -> bool:
    """Slot contention scoped to one hotspot when ``per_peer`` (inject-only multi-HS).

    ``STATUS[slot]`` is shared at the MASTER, but each connected hotspot has an
    independent RF timeslot. Another peer's active QSO must not block this peer.

    Inject-only OBP→HBP defers global contention and stamps bridge ``TX_*`` on the
    shared slot row before ``send_peer``. Same-stream exemption must not treat that
    bridge TX stamp as the hotspot's own leg while the peer is still on the air (RX).
    """
    return (
        hbp_slot_blocks_group_voice_for_peer_reason(
            slot_st,
            peer_id,
            incoming_tgid_b,
            stream_id,
            pkt_time,
            group_hangtime,
            per_peer=per_peer,
            peers=peers,
            peer_slots=peer_slots,
            peer_hang_row=peer_hang_row,
            voice_slot=voice_slot,
            sys_cfg=sys_cfg,
        )
        is not None
    )


def hbp_ingress_new_stream_collision(
    slot_st: dict[str, Any],
    peer_id: bytes,
    rf_src: bytes,
    stream_id: bytes,
    pkt_time: float,
    *,
    per_peer: bool,
) -> bool:
    """True when a new group-voice stream must drop on ingress (legacy routerHBP).

    When ``per_peer`` is False the MASTER ``STATUS[slot]`` is treated as a shared
    RF slot: only one live stream per wire timeslot (legacy bridge_master).

    When ``per_peer`` is True the MASTER fronts multiple hotspots, each on its
    own frequency, so concurrent streams to different TGs on the same slot are
    legitimate. Per-hotspot contention (one listen TG per peer/slot) is enforced
    at downlink time by ``peer_hotspot_voice_slot_busy``; it must not be applied
    here on ingress, otherwise a second hotspot is silenced by the first.
    Same-subscriber rekey with a new stream id is always allowed.
    """
    from ...domain.hbp_protocol import HBPF_SLT_VTERM, STREAM_TO

    if stream_id and stream_id == slot_st.get("RX_STREAM_ID"):
        return False
    if stream_id and stream_id == slot_st.get("TX_STREAM_ID"):
        return False
    if per_peer:
        return False
    del peer_id
    for leg in ("RX", "TX"):
        type_key = f"{leg}_TYPE"
        time_key = f"{leg}_TIME"
        rfs_key = f"{leg}_RFS"
        stream_key = f"{leg}_STREAM_ID"
        dtype = slot_st.get(type_key)
        if dtype is None or dtype == HBPF_SLT_VTERM:
            continue
        leg_time = float(slot_st.get(time_key, 0) or 0)
        if pkt_time >= leg_time + STREAM_TO:
            continue
        if stream_id and stream_id == slot_st.get(stream_key):
            continue
        prev_rfs = slot_st.get(rfs_key, b"\x00\x00\x00")
        if int_id(rf_src) != 0 and bytes_4(int_id(rf_src)) == bytes_4(int_id(prev_rfs)):
            continue
        return True
    return False


def _same_rf_source(a: bytes, b: bytes) -> bool:
    return int_id(a) != 0 and bytes_4(int_id(a)) == bytes_4(int_id(b))


def _hbp_slot_active_tgid(slot_st: dict[str, Any], pkt_time: float) -> bytes | None:
    if not slot_has_active_voice(slot_st, pkt_time):
        return None
    rx_type = slot_st.get("RX_TYPE")
    if rx_type is not None and rx_type != HBPF_SLT_VTERM:
        return slot_st.get("RX_TGID")
    tx_type = slot_st.get("TX_TYPE")
    if tx_type is not None and tx_type != HBPF_SLT_VTERM:
        return slot_st.get("TX_TGID") or slot_st.get("RX_TGID")
    return slot_st.get("RX_TGID")


def _obp_stream_active(st: dict[str, Any], pkt_time: float) -> bool:
    if st.get("_fin"):
        return False
    start = float(st.get("START", 0) or 0)
    if start <= 0 or start + 180 < pkt_time:
        return False
    last = float(st.get("LAST", start) or start)
    if (pkt_time - last) >= STREAM_TO:
        return False
    return True


def obp_ingress_stream_on_system(
    obp_status: dict[Any, Any],
    stream_id: bytes,
    dst_id: bytes,
    rf_src: bytes,
) -> int:
    best_sid: bytes | None = None
    best_first: float | None = None
    for sid, st in obp_status.items():
        if not isinstance(sid, (bytes, bytearray)) or not isinstance(st, dict):
            continue
        if "H_LC" in st:
            continue
        if st.get("TGID") != dst_id:
            continue
        if not _same_rf_source(st.get("RFS", b"\x00\x00\x00"), rf_src):
            continue
        if st.get("_fin"):
            continue
        first = st.get("1ST")
        if first is None:
            continue
        if best_first is None or float(first) < best_first:
            best_first = float(first)
            best_sid = sid
    return int_id(best_sid if best_sid is not None else stream_id)


def obp_cross_system_winner(
    protocols: dict[str, Any],
    systems_cfg: dict[str, Any],
    system_name: str,
    stream_id: bytes,
    dst_id: bytes,
) -> str:
    hr_times: dict[str, float] = {}
    for other_name, proto in protocols.items():
        if systems_cfg.get(other_name, {}).get("MODE") != "OPENBRIDGE":
            continue
        obp_status = getattr(proto, "STATUS", None)
        if not isinstance(obp_status, dict):
            continue
        ent = obp_status.get(stream_id)
        if not isinstance(ent, dict) or "1ST" not in ent or ent.get("TGID") != dst_id:
            continue
        hr_times[other_name] = float(ent["1ST"])
    if not hr_times:
        return system_name
    return min(hr_times, key=hr_times.get)


def obp_is_canonical_ingress(
    protocols: dict[str, Any],
    systems_cfg: dict[str, Any],
    system_name: str,
    stream_id: bytes,
    dst_id: bytes,
    rf_src: bytes,
) -> bool:
    src_proto = protocols.get(system_name)
    obp_status = getattr(src_proto, "STATUS", None) if src_proto else None
    if not isinstance(obp_status, dict):
        return False
    sid = int_id(stream_id)
    if sid != obp_ingress_stream_on_system(obp_status, stream_id, dst_id, rf_src):
        return False
    return system_name == obp_cross_system_winner(
        protocols, systems_cfg, system_name, stream_id, dst_id
    )


def obp_status_plugin_voice(
    protocols: dict[str, Any],
    system_name: str,
    stream_id: bytes,
) -> bool:
    proto = protocols.get(system_name)
    status = getattr(proto, "STATUS", None) if proto else None
    if not isinstance(status, dict):
        return False
    ent = status.get(stream_id)
    return isinstance(ent, dict) and bool(ent.get("_plugin_voice"))


def tg_has_active_conversation(
    protocols: dict[str, Any],
    systems_cfg: dict[str, Any],
    tgid_b: bytes,
    stream_id: bytes,
    rf_src: bytes,
    pkt_time: float,
) -> bool:
    """True when TG *tgid_b* has an active (in-progress) voice conversation.

    Distinct from ``group_voice_tg_ingress_collision``: that helper also matches a TG
    that is merely in ``GROUP_HANGTIME`` (idle but recent). This one only matches a TG
    with a live stream (within ``STREAM_TO`` of the last frame), so the ingress gate
    can activate the TG silently (no uplink, deliver downlink of the active QSO)
    instead of rejecting the stream.
    """
    tgid = int_id(tgid_b)
    if tgid < 5 or tgid in (9, 4000, 5000):
        return False
    for sys_name, proto in protocols.items():
        mode = systems_cfg.get(sys_name, {}).get("MODE")
        status = getattr(proto, "STATUS", None)
        if not isinstance(status, dict):
            continue
        if mode in ("MASTER", "PEER"):
            for slot_key in (1, 2):
                slot_st = status.get(slot_key)
                if not isinstance(slot_st, dict):
                    continue
                if not slot_has_active_voice(slot_st, pkt_time):
                    continue
                active_tg = _hbp_slot_active_tgid(slot_st, pkt_time)
                if active_tg is None or int_id(active_tg) != tgid:
                    continue
                leg_stream = slot_st.get("RX_STREAM_ID") or slot_st.get("TX_STREAM_ID")
                leg_rfs = slot_st.get("RX_RFS") or slot_st.get("TX_RFS") or b"\x00\x00\x00"
                if stream_id and leg_stream == stream_id:
                    continue
                if _same_rf_source(rf_src, leg_rfs):
                    continue
                return True
        elif mode == "OPENBRIDGE":
            for key, st in status.items():
                if isinstance(key, int) or not isinstance(st, dict):
                    continue
                if "TGID" not in st or int_id(st.get("TGID", b"")) != tgid:
                    continue
                if not _obp_stream_active(st, pkt_time):
                    continue
                leg_stream = key if isinstance(key, (bytes, bytearray)) else b""
                leg_rfs = st.get("RFS", b"\x00\x00\x00")
                if stream_id and leg_stream == stream_id:
                    continue
                if _same_rf_source(rf_src, leg_rfs):
                    continue
                return True
    return False


def group_voice_tg_ingress_collision(
    protocols: dict[str, Any],
    systems_cfg: dict[str, Any],
    tgid_b: bytes,
    stream_id: bytes,
    rf_src: bytes,
    pkt_time: float,
) -> bool:
    """True when another active group-voice leg already owns this TG (HBP or OBP)."""
    tgid = int_id(tgid_b)
    if tgid < 5 or tgid in (9, 4000, 5000):
        return False
    for sys_name, proto in protocols.items():
        mode = systems_cfg.get(sys_name, {}).get("MODE")
        status = getattr(proto, "STATUS", None)
        if not isinstance(status, dict):
            continue
        if mode in ("MASTER", "PEER"):
            for slot_key in (1, 2):
                slot_st = status.get(slot_key)
                if not isinstance(slot_st, dict):
                    continue
                active_tg = _hbp_slot_active_tgid(slot_st, pkt_time)
                if active_tg is None or int_id(active_tg) != tgid:
                    continue
                leg_stream = slot_st.get("RX_STREAM_ID") or slot_st.get("TX_STREAM_ID")
                leg_rfs = slot_st.get("RX_RFS") or slot_st.get("TX_RFS") or b"\x00\x00\x00"
                if stream_id and leg_stream == stream_id:
                    continue
                if _same_rf_source(rf_src, leg_rfs):
                    continue
                return True
        elif mode == "OPENBRIDGE":
            for key, st in status.items():
                if isinstance(key, int) or not isinstance(st, dict):
                    continue
                if "TGID" not in st or int_id(st.get("TGID", b"")) != tgid:
                    continue
                if not _obp_stream_active(st, pkt_time):
                    continue
                leg_stream = key if isinstance(key, (bytes, bytearray)) else b""
                leg_rfs = st.get("RFS", b"\x00\x00\x00")
                if stream_id and leg_stream == stream_id:
                    continue
                if _same_rf_source(rf_src, leg_rfs):
                    continue
                return True
    return False


def hbp_ingress_downlink_session_blocks_tx(
    voice_slot: int,
    incoming_tgid_b: bytes,
    peer_slots: PeerVoiceSlotMap | None,
) -> bool:
    """True when a hotspot mid downlink QSO on this TG/slot must not ingress TX."""
    active = (peer_slots or {}).get(int(voice_slot))
    if not isinstance(active, dict) or active.get("ingress"):
        return False
    active_tg = int(active.get("tgid", 0) or 0)
    incoming_tg = int_id(incoming_tgid_b)
    return bool(active_tg and incoming_tg and active_tg == incoming_tg)


def hbp_master_ingress_repeat_allowed(
    slot_st: dict[str, Any],
    peer_id: bytes,
    rf_src: bytes,
    dst_id: bytes,
    stream_id: bytes,
    pkt_time: float,
    *,
    protocols: dict[str, Any] | None = None,
    systems_cfg: dict[str, Any] | None = None,
    is_vterm: bool = False,
    system_cfg: dict[str, Any] | None = None,
) -> bool:
    """True when MASTER REPEAT may fan this ingress packet to other peers.

    In a multi-hotspot MASTER, concurrent streams from different peers to
    different TGs on the same wire timeslot are legitimate: each hotspot is
    on its own RF frequency. Contention is enforced per-peer at downlink
    time (``peer_slot_blocks_downlink``). Applying a global shared-slot gate
    here would drop voice packets of an active stream whenever a second
    stream updates ``RX_STREAM_ID``, causing audible gaps on the first call.
    """
    if stream_id and stream_id == slot_st.get("RX_STREAM_ID"):
        owner = slot_st.get("RX_PEER")
        return (
            owner is not None
            and int_id(owner) != 0
            and bytes_4(int_id(owner)) == bytes_4(int_id(peer_id))
        )
    # A VTERM closes an existing stream; it must reach peers that were hearing
    # that stream even when another stream is now active on the shared slot
    # (multi-hotspot MASTER). Blocking it leaves per-peer sessions open forever.
    if is_vterm:
        return True
    per_peer = bool(system_cfg and master_per_peer_slot_contention(
        systems_cfg or {}, "", system_cfg, connected_count=0,
    ))
    if hbp_ingress_new_stream_collision(
        slot_st, peer_id, rf_src, stream_id, pkt_time, per_peer=per_peer,
    ):
        return False
    if protocols and systems_cfg and group_voice_tg_ingress_collision(
        protocols, systems_cfg, dst_id, stream_id, rf_src, pkt_time,
    ):
        return False
    return True


def is_private_subscriber_dst(dst_id: bytes) -> bool:
    """True for 7-digit private/unit destinations (legacy routerHBP pvt_call branch)."""
    return len(str(int_id(dst_id))) == 7


def unit_data_hbp_target_idle(
    dst_slot: dict,
    pkt_time: float,
    hangtime: float,
) -> bool:
    """Legacy sendDataToHBP gate: both RX/TX idle and past group hangtime."""
    from ...domain.hbp_protocol import HBPF_SLT_VTERM

    return (
        dst_slot.get("RX_TYPE") == HBPF_SLT_VTERM
        and dst_slot.get("TX_TYPE") == HBPF_SLT_VTERM
        and (pkt_time - dst_slot.get("TX_TIME", 0) > hangtime)
    )


def unit_data_reportable(dtype_vseq: int) -> bool:
    """True when a unit-data frame should emit monitor/report events.

    CSBK prelude (dtype 3) is routed but omitted from BRDG_EVENT — same policy as
    data-log ``log_dtypes`` — to avoid SMS/GPS setup storms in logs and monitors.
    """
    return dtype_vseq in (6, 7, 8)


def is_unit_data_ingress(
    call_type: str,
    dtype_vseq: int,
    stream_id: bytes,
    slot_rx_stream_id: bytes | None,
) -> bool:
    """True when legacy routerHBP sets ``_data_call`` (bridge_master.py ~3130).

    Unit data is routed but must not update per-slot RX STATUS (busy check for
    downlink SUB_MAP / hotspot match stays open on the source MASTER).
    """
    if call_type != "unit":
        return False
    if dtype_vseq in (6, 7, 8):
        return True
    if dtype_vseq == 3:
        return stream_id != (slot_rx_stream_id or b"\x00")
    return False


# Embedded LC codeword sits at bits 116:148 inside the 48-bit EMB field (108:156).
# Legacy bridge_master.py replaces dmrbits[116:148] on bursts B–E (dtype_vseq 1–4).
EMB_LC_SLICE = slice(116, 148)


def tg4000_reset_on_vhead(int_dst_id: int, frame_type: int, dtype_vseq: int) -> bool:
    """True when TG/ID 4000 voice header should trigger a one-shot dynamic reset."""
    return (
        int_dst_id == 4000
        and frame_type == HBPF_DATA_SYNC
        and dtype_vseq == HBPF_SLT_VHEAD
    )


def is_ua_session_tgid(tgid: int) -> bool:
    """True when a keyed TG may be stored as a user-activated dynamic session.

    Excludes TG 4000 (reset command) and service/echo 9990–9999 (no SINGLE lock).
    """
    t = int(tgid)
    if t <= 0 or t == 4000:
        return False
    return not is_special_tg(str(t))


def obp_target_bcsq_quenches_stream(
    config: dict[str, Any], target_name: str, dst_id_b: bytes, stream_id: bytes
) -> bool:
    """True when the target OBP has quenched this stream for this talkgroup."""
    return obp_session(config, target_name).quenches(dst_id_b, stream_id)


def _peer_key_from_int(peer_key: Any) -> bytes:
    if isinstance(peer_key, bytes):
        return peer_key
    if isinstance(peer_key, int):
        return bytes_4(peer_key)
    if isinstance(peer_key, str) and peer_key.isdigit():
        return bytes_4(int(peer_key))
    return bytes_4(int_id(peer_key))


def _fuzzy_peer_matches(
    val: int,
    peers: dict[Any, Any],
) -> list[bytes]:
    val_str = str(val)
    matches: list[bytes] = []
    for pk in peers:
        try:
            pk_b = _peer_key_from_int(pk)
        except (TypeError, ValueError):
            continue
        pk_int = int_id(pk_b)
        pk_str = str(pk_int)
        if pk_int == val or pk_int // 100 == val:
            matches.append(pk_b)
            continue
        if len(val_str) >= 5 and len(pk_str) >= 7 and pk_str.startswith(val_str):
            matches.append(pk_b)
    return matches


def resolve_voice_peer_id(
    peer_id: bytes,
    rf_src: bytes,
    system_name: str,
    systems_cfg: dict[str, Any],
) -> bytes:
    """Resolve BRDG_EVENT field 5 for RX legs from a MASTER (hotspot transmitting).

    Legacy bridge uses ``_peer_id`` from DMRD for TX legs unchanged. Only RX source
    events need the full hotspot radio id so monitor ``rts_update`` marks that peer RX.
    """
    peers = systems_cfg.get(system_name, {}).get("PEERS", {})
    if not isinstance(peers, dict) or not peers:
        return peer_id
    peer_b = peer_id if isinstance(peer_id, bytes) else bytes_4(int_id(peer_id))
    if peer_b in peers:
        return peer_b
    rf_b = rf_src if isinstance(rf_src, bytes) else bytes_3(int_id(rf_src))
    if rf_b in peers:
        return rf_b
    peer_matches = _fuzzy_peer_matches(int_id(peer_id), peers)
    if len(peer_matches) == 1:
        return peer_matches[0]
    rf_matches = _fuzzy_peer_matches(int_id(rf_src), peers)
    if len(rf_matches) == 1:
        return rf_matches[0]
    return peer_id


# Back-compat alias for tests and imports.
report_peer_id_for_hbp_target = resolve_voice_peer_id


def is_special_tg(relay_table_key: str) -> bool:
    """True if bridge is special TGID 9990-9999 (excluded from infinite timer)."""
    if relay_table_key and relay_table_key[0:1] == "#":
        return False
    try:
        return 9990 <= int(relay_table_key) <= 9999
    except ValueError:
        return False


def is_on_demand_service_dst(dst_id: int) -> bool:
    """True for private-call destinations 9991-9999 (on-demand audio trigger)."""
    return 9991 <= int(dst_id) <= 9999


def is_server_originated_voice(
    packet: bytes,
    *,
    server_voice_rf_src: int | None = None,
    server_voice_rf_srcs: frozenset[int] | None = None,
) -> bool:
    """True for server group playback (server voice ID -> TG 9 TS2), e.g. on-demand / disconnected."""
    if len(packet) < 11:
        return False
    burst = parse_dmrd_burst_fields(packet)
    if burst is None:
        return False
    slot, _, _, _, dst_id, call_type = burst
    if call_type not in ("group", "vcsbk"):
        return False
    src = int_id(packet[5:8])
    if server_voice_rf_srcs is not None:
        if src not in server_voice_rf_srcs:
            return False
    elif server_voice_rf_src is not None:
        if src != server_voice_rf_src and src != _SERVER_VOICE_RF_SRC_LEGACY:
            return False
    elif src != DEFAULT_SERVER_VOICE_ID and src != _SERVER_VOICE_RF_SRC_LEGACY:
        return False
    return int_id(dst_id) == 9 and slot == 2


def parse_dmrd_route_fields(packet: bytes) -> tuple[int, int, str] | None:
    """Parse HBP DMRD slot, destination TG, and call type for downlink OPTIONS filter."""
    burst = parse_dmrd_burst_fields(packet)
    if burst is None:
        return None
    slot, _, _, _, dst_id, call_type = burst
    return slot, int_id(dst_id), call_type


def parse_dmrd_burst_fields(
    packet: bytes,
) -> tuple[int, int, int, bytes, bytes, str] | None:
    """Parse wire slot, frame type, dtype, stream id, dst, call type from group DMRD."""
    if len(packet) < 20 or packet[:4] != b"DMRD":
        return None
    bits = packet[15]
    slot = 2 if (bits & 0x80) else 1
    if bits & 0x40:
        return None
    if (bits & 0x23) == 0x23:
        call_type = "vcsbk"
    else:
        call_type = "group"
    frame_type = (bits & 0x30) >> 4
    dtype_vseq = bits & 0xF
    return slot, frame_type, dtype_vseq, packet[16:20], packet[8:11], call_type


def _system_has_active_bridge_leg(
    bridges: dict[str, Any] | None,
    system: str,
    slot: int,
    tgid: int,
    *,
    subscription_store: Any | None = None,
) -> bool:
    """True when the store (or legacy BRIDGES export) has an ACTIVE leg for ``(system, slot, tgid)``."""
    if subscription_store is not None and system:
        from adn_server.application.subscription.subscription_queries import (
            system_has_active_leg_in_store,
        )

        return system_has_active_leg_in_store(subscription_store, system, slot, tgid)
    if not bridges or not system:
        return False
    legs = bridges.get(str(tgid))
    if not isinstance(legs, list):
        return False
    for leg in legs:
        if not isinstance(leg, dict) or not leg.get("ACTIVE"):
            continue
        if str(leg.get("SYSTEM", "")) != system:
            continue
        if int(leg.get("TS", 0)) != int(slot):
            continue
        return True
    return False


def peer_options_fields(peer: dict[str, Any]) -> dict[str, Any]:
    """Parse hotspot OPTIONS into fields used by SINGLE/TIMER resolution.

    Memoized against the OPTIONS blob, the way ``cached_peer_static_tgs`` already
    memoizes the static lists: OPTIONS only changes on RPTO, which drops the cache
    (``invalidate_peer_options_cache``), while ingress asks for these fields
    several times per voice frame through ``peer_single_mode``.
    """
    opts = peer.get("OPTIONS")
    key = opts if isinstance(opts, bytes) else b""
    cached = peer.get("_CACHED_OPTIONS_FIELDS")
    if cached is not None and cached[0] == key:
        return cached[1]
    from adn_server.application.report.payloads import parse_peer_options_fields

    fields = parse_peer_options_fields(opts)
    peer["_CACHED_OPTIONS_FIELDS"] = (key, fields)
    return fields


def _peer_ua_session_entry(
    sys_cfg: dict[str, Any],
    peer_id: bytes | None,
    slot: int,
) -> dict[str, Any] | None:
    if peer_id is None:
        return None
    store = sys_cfg.get("_PEER_UA_SESSIONS")
    if not isinstance(store, dict):
        return None
    pk = bytes_4(int_id(peer_id))
    per_peer = store.get(pk)
    if not isinstance(per_peer, dict):
        return None
    entry = per_peer.get(slot)
    return entry if isinstance(entry, dict) else None


def _write_peer_ua_session(
    peer: dict[str, Any],
    peer_id: bytes,
    slot: int,
    tgid: int,
    expires: float,
    sys_cfg: dict[str, Any],
    *,
    source: str = "local",
) -> None:
    entry = {"tgid": int(tgid), "expires": float(expires), "source": str(source)}
    pk = bytes_4(int_id(peer_id))
    sys_cfg.setdefault("_PEER_UA_SESSIONS", {}).setdefault(pk, {})[slot] = entry
    peer.setdefault("_UA_SESSION", {})[slot] = entry


def peer_single_mode(peer: dict[str, Any], sys_cfg: dict[str, Any]) -> bool:
    from adn_server.application.report.payloads import resolve_peer_single_and_timer

    single, _ = resolve_peer_single_and_timer(peer_options_fields(peer), sys_cfg)
    return single


def _peer_ua_multi_store(sys_cfg: dict[str, Any]) -> dict[bytes, dict[int, set[int]]]:
    store = sys_cfg.setdefault("_PEER_UA_MULTI_TGS", {})
    if not isinstance(store, dict):
        store = {}
        sys_cfg["_PEER_UA_MULTI_TGS"] = store
    return store


def _peer_static_tg_blocks_slot(peer: dict[str, Any], slot: int, tgid: int) -> bool:
    """Does this peer's static OPTIONS already cover ``tgid`` for this exact
    slot? Simplex peers have one real RF path regardless of nominal TS1/TS2,
    so any static match blocks (matches peer_receives_group_tgid's
    either-slot check); duplex peers are checked per-slot, since a static
    match on one slot must not block genuinely independent dynamic activity
    on the *other* slot (e.g. TG static on TS2, this same peer separately
    keying up the same TG on TS1)."""
    from adn_server.application.routing.peer_downlink_index import cached_peer_static_tgs

    ts1, ts2 = cached_peer_static_tgs(peer)
    tg = str(tgid)
    if peer_is_simplex(peer):
        return tg in ts1 or tg in ts2
    if int(slot) == 1:
        return tg in ts1
    if int(slot) == 2:
        return tg in ts2
    return False


def register_peer_ua_multi_tg(
    peer: dict[str, Any],
    peer_id: bytes,
    slot: int,
    tgid: int,
    sys_cfg: dict[str, Any],
) -> None:
    """SINGLE=0: accumulate keyed dynamic TGs per peer/slot until TG 4000."""
    if peer_single_mode(peer, sys_cfg):
        return
    tgid_i = int(tgid)
    if not is_ua_session_tgid(tgid_i):
        return
    if _peer_static_tg_blocks_slot(peer, slot, tgid_i):
        return
    pk = bytes_4(int_id(peer_id))
    per_peer = _peer_ua_multi_store(sys_cfg).setdefault(pk, {})
    slot_set = per_peer.setdefault(int(slot), set())
    slot_set.add(tgid_i)


def peer_owns_multi_dynamic_ua(
    peer: dict[str, Any],
    slot: int,
    tgid: int,
    sys_cfg: dict[str, Any] | None,
    *,
    peer_id: bytes | None = None,
) -> bool:
    """True when SINGLE=0 peer has keyed this non-static dynamic TG (either slot)."""
    if not sys_cfg or peer_single_mode(peer, sys_cfg):
        return False
    if peer_id is None:
        return False
    if peer_receives_group_tgid(peer, slot, tgid):
        return False
    pk = bytes_4(int_id(peer_id))
    store = sys_cfg.get("_PEER_UA_MULTI_TGS")
    if not isinstance(store, dict):
        return False
    per_peer = store.get(pk)
    if not isinstance(per_peer, dict):
        return False
    tgid_i = int(tgid)
    for voice_slot in (1, 2):
        slot_set = per_peer.get(voice_slot)
        if isinstance(slot_set, set) and tgid_i in slot_set:
            return True
    return False


def peer_dynamic_tg_active_on_slot(
    peer: dict[str, Any],
    tgid: int,
    slot: int,
    sys_cfg: dict[str, Any] | None,
    *,
    peer_id: bytes | None = None,
) -> bool:
    """True when a *dynamic* (non-static OPTIONS) TG is active on this
    specific slot for this peer right now. Covers SINGLE=1 (independent
    exclusive session per slot) and SINGLE=0 (independently keyed multi-TG
    set per slot)."""
    if not sys_cfg or peer_id is None:
        return False
    tgid_i = int(tgid)
    if peer_single_mode(peer, sys_cfg):
        locked = peer_single_exclusive_tgid(peer, slot, sys_cfg, peer_id=peer_id)
        return locked is not None and locked == tgid_i
    store = sys_cfg.get("_PEER_UA_MULTI_TGS")
    if not isinstance(store, dict):
        return False
    per_peer = store.get(bytes_4(int_id(peer_id)))
    if not isinstance(per_peer, dict):
        return False
    slot_set = per_peer.get(slot)
    return isinstance(slot_set, set) and tgid_i in slot_set


def peer_dynamic_tg_active_on_both_slots(
    peer: dict[str, Any],
    tgid: int,
    sys_cfg: dict[str, Any] | None,
    *,
    peer_id: bytes | None = None,
) -> bool:
    """True when a *dynamic* (non-static OPTIONS) TG is active on both slot 1
    and slot 2 for this peer right now -- static-or-dynamic makes no
    difference to whether a duplex peer should get the call on both slots."""
    return (
        peer_dynamic_tg_active_on_slot(peer, tgid, 1, sys_cfg, peer_id=peer_id)
        and peer_dynamic_tg_active_on_slot(peer, tgid, 2, sys_cfg, peer_id=peer_id)
    )


def register_peer_ua_session(
    peer: dict[str, Any],
    peer_id: bytes,
    slot: int,
    tgid: int,
    sys_cfg: dict[str, Any],
    *,
    now: float | None = None,
    source: str = "local",
) -> None:
    """Track UA TG for this hotspot (SINGLE=1 exclusive; SINGLE=0 multi-dynamic set)."""
    if not is_ua_session_tgid(tgid):
        return
    if not peer_single_mode(peer, sys_cfg):
        register_peer_ua_multi_tg(peer, peer_id, slot, tgid, sys_cfg)
        return
    from adn_server.application.report.payloads import resolve_peer_single_and_timer
    from adn_server.domain.ua_timer import UA_SESSION_NEVER_EXPIRES_AT, ua_timer_is_infinite

    _, timer_min = resolve_peer_single_and_timer(peer_options_fields(peer), sys_cfg)
    pkt_time = time.time() if now is None else now
    if ua_timer_is_infinite(timer_min):
        expires_at = UA_SESSION_NEVER_EXPIRES_AT
    else:
        expires_at = pkt_time + float(timer_min) * 60.0
    # One exclusive dynamic TG per hotspot (either RF slot); new local TX replaces all others.
    other_slot = 2 if int(slot) == 1 else 1
    clear_peer_ua_sessions(peer, sys_cfg, peer_id, slot=other_slot)
    _write_peer_ua_session(
        peer,
        peer_id,
        slot,
        int(tgid),
        expires_at,
        sys_cfg,
        source=source,
    )


def seed_peer_ua_session_from_status(
    peer: dict[str, Any],
    peer_id: bytes,
    slot: int,
    status_slot: dict[str, Any],
    sys_cfg: dict[str, Any],
    *,
    now: float | None = None,
) -> None:
    """Seed SINGLE session after RPTO when TX happened before OPTIONS (inject-only)."""
    if not peer_single_mode(peer, sys_cfg):
        return
    pkt_time = time.time() if now is None else now
    if peer_single_exclusive_tgid(peer, slot, sys_cfg, peer_id=peer_id, now=pkt_time) is not None:
        return
    rx_peer = status_slot.get("RX_PEER", b"\x00\x00\x00\x00")
    if bytes_4(int_id(peer_id)) != bytes_4(int_id(rx_peer)):
        return
    rx_tgid = int_id(status_slot.get("RX_TGID", b"\x00\x00\x00"))
    if not is_ua_session_tgid(rx_tgid):
        return
    connected_at = float(peer.get("CONNECTED", 0) or 0)
    rx_time = float(status_slot.get("RX_TIME", 0) or 0)
    if connected_at > 0 and rx_time < connected_at - 0.5:
        return
    register_peer_ua_session(peer, peer_id, slot, rx_tgid, sys_cfg, now=pkt_time)


def clear_peer_rx_status_slots(
    status: dict[Any, Any],
    peer_id: bytes,
    *,
    slot: int | None = None,
) -> None:
    """Reset RX fields on slots last owned by this peer (avoids stale OPTIONS seed)."""
    pk = bytes_4(int_id(peer_id))
    slots = (int(slot),) if slot is not None else (1, 2)
    for slot_id in slots:
        slot_st = status.get(slot_id)
        if not isinstance(slot_st, dict):
            continue
        if bytes_4(int_id(slot_st.get("RX_PEER", b"\x00"))) != pk:
            continue
        slot_st["RX_PEER"] = b"\x00"
        slot_st["RX_TGID"] = b"\x00\x00\x00"
        slot_st["RX_STREAM_ID"] = b"\x00"
        slot_st["RX_TIME"] = 0.0


def export_peer_ua_sessions(
    sys_cfg: dict[str, Any],
    peer_id: bytes | int,
    *,
    now: float | None = None,
) -> dict[str, dict[str, float | int]]:
    """Active SINGLE sessions for monitor snapshot (server source of truth)."""
    pkt_time = time.time() if now is None else now
    pk = bytes_4(int_id(peer_id))
    out: dict[str, dict[str, float | int]] = {}
    store = sys_cfg.get("_PEER_UA_SESSIONS")
    if not isinstance(store, dict):
        return out
    per_peer = store.get(pk)
    if not isinstance(per_peer, dict):
        return out
    for slot in (1, 2):
        entry = per_peer.get(slot)
        if not isinstance(entry, dict):
            continue
        exp = float(entry.get("expires", 0) or 0)
        tgid = int(entry.get("tgid", 0) or 0)
        if is_ua_session_tgid(tgid) and (exp == 0.0 or exp > pkt_time):
            row: dict[str, float | int] = {"tgid": tgid}
            if exp > pkt_time:
                row["expires_at"] = exp
            out[str(slot)] = row
    return out


def export_peer_ua_multi_tgs(
    sys_cfg: dict[str, Any],
    peer_id: bytes | int,
) -> dict[str, list[int]]:
    """Active SINGLE=0 multi-dynamic TG sets for monitor snapshot."""
    pk = bytes_4(int_id(peer_id))
    store = sys_cfg.get("_PEER_UA_MULTI_TGS")
    if not isinstance(store, dict):
        return {}
    per_peer = store.get(pk)
    if not isinstance(per_peer, dict):
        return {}
    out: dict[str, list[int]] = {}
    for slot in (1, 2):
        tg_set = per_peer.get(slot)
        if isinstance(tg_set, set) and tg_set:
            tgids = sorted(
                int(t) for t in tg_set if is_ua_session_tgid(int(t))
            )
            if tgids:
                out[str(slot)] = tgids
    return out


def master_dynamic_tg_slots(
    sys_cfg: dict[str, Any],
    tg_int: int,
    *,
    now: float | None = None,
) -> set[int]:
    """Slots where ``tg_int`` is an active dynamic UA session for any peer.

    SINGLE=1 sessions come from ``_PEER_UA_SESSIONS``; SINGLE=0 multi-dynamic
    sets from ``_PEER_UA_MULTI_TGS``. Used to activate SYSTEM bridge legs for
    dynamic TGs (OBP/HBP inbound) the same way OPTIONS static TGs are activated.
    """
    if not is_ua_session_tgid(tg_int):
        return set()
    pkt_time = time.time() if now is None else now
    slots: set[int] = set()
    single_store = sys_cfg.get("_PEER_UA_SESSIONS")
    if isinstance(single_store, dict):
        for per_peer in single_store.values():
            if not isinstance(per_peer, dict):
                continue
            for slot, entry in per_peer.items():
                if not isinstance(entry, dict):
                    continue
                if int(entry.get("tgid", 0) or 0) != tg_int:
                    continue
                exp = float(entry.get("expires", 0) or 0)
                if exp == 0.0 or exp > pkt_time:
                    slots.add(int(slot))
    multi_store = sys_cfg.get("_PEER_UA_MULTI_TGS")
    if isinstance(multi_store, dict):
        for per_peer in multi_store.values():
            if not isinstance(per_peer, dict):
                continue
            for slot, tg_set in per_peer.items():
                if isinstance(tg_set, set) and tg_int in {int(t) for t in tg_set}:
                    slots.add(int(slot))
    return slots


def restore_peer_ua_entries_to_memory(
    sys_cfg: dict[str, Any],
    peer_id: bytes,
    entries: list[Any],
    *,
    now: float | None = None,
) -> list[int]:
    """Apply persisted dynamic TG rows to ``_PEER_UA_SESSIONS`` / ``_PEER_UA_MULTI_TGS``."""
    pkt_time = time.time() if now is None else now
    pk = bytes_4(int_id(peer_id))
    restored: list[int] = []
    for entry in entries:
        tgid = int(entry.tgid)
        if not is_ua_session_tgid(tgid):
            continue
        slot = int(entry.slot)
        if entry.single_mode:
            from adn_server.domain.ua_timer import UA_SESSION_NEVER_EXPIRES_AT, ua_session_never_expires

            expires = entry.expires_at
            if (
                expires is not None
                and not ua_session_never_expires(float(expires))
                and float(expires) <= pkt_time
            ):
                continue
            per_peer = sys_cfg.setdefault("_PEER_UA_SESSIONS", {}).setdefault(pk, {})
            if expires is None or ua_session_never_expires(float(expires)):
                exp_mem = UA_SESSION_NEVER_EXPIRES_AT
            else:
                exp_mem = float(expires)
            per_peer[slot] = {
                "tgid": tgid,
                "expires": exp_mem,
            }
            restored.append(tgid)
        else:
            multi = sys_cfg.setdefault("_PEER_UA_MULTI_TGS", {}).setdefault(pk, {})
            multi.setdefault(slot, set()).add(tgid)
            restored.append(tgid)
    return restored


def purge_expired_peer_ua_sessions(sys_cfg: dict[str, Any], *, now: float | None = None) -> None:
    """Drop expired SINGLE=1 sessions from in-memory store."""
    pkt_time = time.time() if now is None else now
    store = sys_cfg.get("_PEER_UA_SESSIONS")
    if not isinstance(store, dict):
        return
    for pk in list(store.keys()):
        per_peer = store.get(pk)
        if not isinstance(per_peer, dict):
            continue
        for slot in list(per_peer.keys()):
            entry = per_peer.get(slot)
            if not isinstance(entry, dict):
                continue
            exp = float(entry.get("expires", 0) or 0)
            if exp > 0 and pkt_time >= exp:
                per_peer.pop(slot, None)
        if not per_peer:
            store.pop(pk, None)


def clear_peer_ua_sessions(
    peer: dict[str, Any],
    sys_cfg: dict[str, Any],
    peer_id: bytes,
    *,
    slot: int | None = None,
) -> None:
    """Clear per-peer UA state (SINGLE session and/or SINGLE=0 multi-dynamic set)."""
    pk = bytes_4(int_id(peer_id))
    store = sys_cfg.get("_PEER_UA_SESSIONS")
    if isinstance(store, dict) and pk in store:
        if slot is None:
            store.pop(pk, None)
        else:
            per_peer = store.get(pk)
            if isinstance(per_peer, dict):
                per_peer.pop(slot, None)
    multi = sys_cfg.get("_PEER_UA_MULTI_TGS")
    if isinstance(multi, dict) and pk in multi:
        if slot is None:
            multi.pop(pk, None)
        else:
            per_peer = multi.get(pk)
            if isinstance(per_peer, dict):
                per_peer.pop(int(slot), None)
    sessions = peer.get("_UA_SESSION")
    if isinstance(sessions, dict):
        if slot is None:
            sessions.clear()
        else:
            sessions.pop(slot, None)


def peer_single_exclusive_tgid(
    peer: dict[str, Any],
    slot: int,
    sys_cfg: dict[str, Any],
    *,
    peer_id: bytes | None = None,
    now: float | None = None,
) -> int | None:
    """Active SINGLE session TG on ``slot``, or ``None`` when no exclusive lock."""
    if not peer_single_mode(peer, sys_cfg):
        return None
    pkt_time = time.time() if now is None else now
    entry = _peer_ua_session_entry(sys_cfg, peer_id, slot)
    if entry is None:
        sessions = peer.get("_UA_SESSION")
        if isinstance(sessions, dict):
            entry = sessions.get(slot)
    if not isinstance(entry, dict):
        return None
    exp = float(entry.get("expires", 0) or 0)
    if exp > 0 and pkt_time >= exp:
        return None
    locked = entry.get("tgid")
    return int(locked) if locked is not None else None


def peer_single_blocks_group_voice(
    peer: dict[str, Any],
    slot: int,
    tgid: int,
    sys_cfg: dict[str, Any] | None,
    *,
    peer_id: bytes | None = None,
    now: float | None = None,
) -> bool:
    """True when SINGLE=1 peer must not receive downlink for ``tgid``.

    With an active SINGLE session on TG *X* in the peer's RF listen slot for
    *tgid*, every other TG on **that same RF slot** is blocked until the TIMER
    expires or a new local TX replaces the session.

    Duplex hotspots have independent RF timeslots: a listen lock on TS1 must not
    block a static TG on TS2 (and vice versa). Simplex hotspots always collapse
    to ``SIMPLEX_VOICE_SLOT``, so both TGs share the same RF slot and blocking
    still applies.
    """
    if not sys_cfg:
        return False
    voice_slot = peer_downlink_voice_slot(peer, int(slot), int(tgid), sys_cfg, peer_id=peer_id)
    locked = peer_single_exclusive_tgid(
        peer, voice_slot, sys_cfg, peer_id=peer_id, now=now,
    )
    return locked is not None and int(tgid) != locked


def peer_single_blocks_foreign_same_tg_downlink(
    peer: dict[str, Any],
    peer_id: bytes,
    voice_slot: int,
    incoming_tgid_b: bytes,
    peer_slots: PeerVoiceSlotMap | None,
    sys_cfg: dict[str, Any] | None,
    *,
    now: float | None = None,
) -> bool:
    """SINGLE=1 local UA on TG T: block network downlink on T unless hotspot is TX on T.

    Downlink listen locks (``source=listen``) only exclude other TGs via
    ``peer_single_blocks_group_voice``; same-TG stream overlap uses
    ``peer_hotspot_voice_slot_busy``.

    A SINGLE=1 UA session for TG T is itself proof that the peer activated T
    dynamically — the peer must receive downlink for T even when T is not in
    the static OPTIONS list. Blocking it here would make dynamic TGs deaf to
    their own activated TG once the local TX ends.
    """
    if not sys_cfg or not peer_single_mode(peer, sys_cfg):
        return False
    incoming = int_id(incoming_tgid_b)
    locked = _peer_single_locked_tgid(
        peer, sys_cfg, peer_id=peer_id, now=now, prefer_slot=voice_slot,
    )
    if locked is None or int(locked) != incoming:
        return False
    entry = _peer_ua_session_entry(sys_cfg, peer_id, voice_slot)
    if isinstance(entry, dict) and entry.get("source") == "listen":
        return False
    active = (peer_slots or {}).get(int(voice_slot))
    if isinstance(active, dict) and active.get("ingress"):
        return True
    # UA session locked to this TG (dynamic activation) must hear its downlink.
    if isinstance(entry, dict) and int(entry.get("tgid", 0) or 0) == incoming:
        return False
    if isinstance(peer, dict) and peer_receives_group_tgid(peer, voice_slot, incoming):
        return False
    return True


def peer_static_options_tg_count(peer: dict[str, Any]) -> int:
    """Count distinct static group TGs listed in peer OPTIONS (TS1 ∪ TS2)."""
    from adn_server.application.routing.peer_downlink_index import cached_peer_static_tgs

    ts1, ts2 = cached_peer_static_tgs(peer)
    return len(set(ts1) | set(ts2))


def peer_wants_downlink_single_listen_lock(peer: dict[str, Any], sys_cfg: dict[str, Any]) -> bool:
    """SINGLE=1 downlink listen lock for overlap — not full-table lab witnesses."""
    if not sys_cfg:
        return False
    if not peer_single_mode(peer, sys_cfg):
        return False
    return peer_static_options_tg_count(peer) <= 6


def peer_receives_group_tgid(peer: dict[str, Any], slot: int, tgid: int) -> bool:
    """True when peer RPTO OPTIONS list the group TG on TS1 or TS2 (legacy REPEAT parity).

    Voice may arrive on either timeslot; hotspots in repeater mode often use one RF
    slot while self-service lists the TG on the other.
    """
    del slot
    from adn_server.application.routing.peer_downlink_index import cached_peer_static_tgs

    ts1, ts2 = cached_peer_static_tgs(peer)
    tg = str(tgid)
    return tg in ts1 or tg in ts2


def peer_options_static_tg_slot(peer: dict[str, Any], tgid: int) -> int | None:
    """Timeslot (1 or 2) where peer OPTIONS list ``tgid``, when unambiguous."""
    from adn_server.application.routing.peer_downlink_index import cached_peer_static_tgs

    ts1, ts2 = cached_peer_static_tgs(peer)
    tg = str(tgid)
    in_ts1 = tg in ts1
    in_ts2 = tg in ts2
    if peer_is_simplex(peer) and (in_ts1 or in_ts2):
        return SIMPLEX_VOICE_SLOT
    if in_ts1 and not in_ts2:
        return 1
    if in_ts2 and not in_ts1:
        return 2
    return None


def synthetic_group_dmrd_burst_packet(
    slot: int,
    tgid: int,
    stream_id: bytes,
    *,
    frame_type: int = 0,
    dtype_vseq: int = 0,
    call_type: str = "group",
) -> bytes:
    """Minimal DMRD with burst header fields for slot-tracking helpers."""
    bits = 0x80 if int(slot) == 2 else 0
    if call_type == "vcsbk":
        bits |= 0x23
    else:
        bits |= (int(frame_type) & 0x3) << 4
        bits |= int(dtype_vseq) & 0xF
    sid = bytes_4(int_id(stream_id)) if stream_id else b"\x00" * 4
    return b"DMRD" + b"\x00" * 4 + bytes_3(tgid) + b"\x00" * 4 + bytes([bits]) + sid + b"\x00" * 34


def synthetic_group_dmrd_route_packet(
    slot: int,
    tgid: int,
    stream_id: bytes | None = None,
) -> bytes:
    """Minimal DMRD for downlink/monitor gate lookup (slot, TG, optional stream)."""
    sid = stream_id if stream_id else b"\x00" * 4
    return synthetic_group_dmrd_burst_packet(slot, tgid, sid)


def peer_downlink_voice_slot(
    peer: dict[str, Any],
    wire_slot: int,
    tgid: int,
    sys_cfg: dict[str, Any] | None = None,
    *,
    peer_id: bytes | None = None,
) -> int:
    """Monitor/BRDG field 7: TS where this peer listens for ``tgid`` (OPTIONS or UA)."""
    if peer_is_simplex(peer):
        return SIMPLEX_VOICE_SLOT
    static = peer_options_static_tg_slot(peer, tgid)
    if static is not None:
        return static
    from adn_server.application.routing.peer_downlink_index import cached_peer_static_tgs

    ts1, ts2 = cached_peer_static_tgs(peer)
    tg = str(tgid)
    if tg in ts1 and tg in ts2:
        # Static on both slots (peer_options_static_tg_slot returns None
        # because that's ambiguous *as a single answer*, not because the TG
        # is unresolved) -- an in-progress SINGLE=1 exclusive lock or SINGLE=0
        # UA_MULTI entry from this peer's own current transmission must not
        # override a config that already, unambiguously, permits wire_slot.
        return int(wire_slot)
    if sys_cfg is not None and peer_id is not None:
        tgid_i = int(tgid)
        pk = bytes_4(int_id(peer_id))
        for voice_slot in (1, 2):
            locked = peer_single_exclusive_tgid(
                peer, voice_slot, sys_cfg, peer_id=peer_id,
            )
            if locked is not None and int(locked) == tgid_i:
                return voice_slot
        store = sys_cfg.get("_PEER_UA_MULTI_TGS")
        if isinstance(store, dict):
            per_peer = store.get(pk)
            if isinstance(per_peer, dict):
                wire_slot_i = int(wire_slot)
                wire_slot_set = per_peer.get(wire_slot_i)
                if isinstance(wire_slot_set, set) and tgid_i in wire_slot_set:
                    return wire_slot_i
                for voice_slot in (1, 2):
                    slot_set = per_peer.get(voice_slot)
                    if isinstance(slot_set, set) and tgid_i in slot_set:
                        return voice_slot
    return int(wire_slot)


def remap_dmrd_to_peer_static_slot(
    packet: bytes,
    peer: dict[str, Any],
    sys_cfg: dict[str, Any] | None = None,
    *,
    peer_id: bytes | None = None,
) -> bytes:
    """Flip DMRD slot bit so the hotspot RF TS matches OPTIONS/UA for this TG."""
    parsed = parse_dmrd_route_fields(packet)
    if parsed is None:
        return packet
    voice_slot, tgid, call_type = parsed
    if call_type not in ("group", "vcsbk"):
        return packet
    if is_special_tg(str(tgid)):
        return packet
    cfg_slot = peer_downlink_voice_slot(
        peer, voice_slot, tgid, sys_cfg, peer_id=peer_id,
    )
    if cfg_slot == voice_slot:
        return packet
    bits = packet[15]
    new_bits = bits ^ (1 << 7)
    return packet[:15] + bytes([new_bits]) + packet[16:]


def _peer_owns_dynamic_ua(
    peer: dict[str, Any],
    slot: int,
    tgid: int,
    sys_cfg: dict[str, Any] | None,
    *,
    peer_id: bytes | None = None,
    now: float | None = None,
) -> bool:
    """True when ``tgid`` is a non-static UA this peer activated (SINGLE session owner)."""
    if peer_receives_group_tgid(peer, slot, tgid):
        return False
    if not sys_cfg:
        return False
    tgid_i = int(tgid)
    for voice_slot in (1, 2):
        locked = peer_single_exclusive_tgid(
            peer, voice_slot, sys_cfg, peer_id=peer_id, now=now,
        )
        if locked is not None and tgid_i == locked:
            return True
    return False


def peer_should_receive_group_voice(
    peer: dict[str, Any],
    slot: int,
    tgid: int,
    *,
    peer_id: bytes | None = None,
    system: str | None = None,
    bridges: dict[str, Any] | None = None,
    subscription_store: Any | None = None,
    connected_count: int = 1,
    sys_cfg: dict[str, Any] | None = None,
    now: float | None = None,
) -> bool:
    """Whether a hotspot should get downlink / monitor voice for ``(slot, tgid)``.

    Inject-only multi-hotspot rules (per peer):

    1. ``SINGLE=1`` with an active session on another TG → deny all other TGs.
    2. TG in this peer's OPTIONS static list (TS1 or TS2) → allow (when not blocked by SINGLE).
    3. ``SINGLE=1``: dynamic UA owned by this peer's exclusive session → allow.
    4. ``SINGLE=0``: dynamic UA this peer keyed (multi set) → allow.
    5. Sole connected hotspot with an ACTIVE bridge leg for ``(slot, tgid)`` → allow.
    6. Otherwise → deny (no fan-out).

    A system-wide ACTIVE bridge leg must **not** fan out to every hotspot when
    several peers are online; that was the regression when ``bridges`` alone
    decided fan-out for all connected hotspots.
    """
    if peer_single_blocks_group_voice(peer, slot, tgid, sys_cfg, peer_id=peer_id, now=now):
        return False
    if peer_receives_group_tgid(peer, slot, tgid):
        return True
    if _peer_owns_dynamic_ua(peer, slot, tgid, sys_cfg, peer_id=peer_id, now=now):
        return True
    if peer_owns_multi_dynamic_ua(peer, slot, tgid, sys_cfg, peer_id=peer_id):
        return True
    if connected_count == 1 and system and _system_has_active_bridge_leg(
        bridges, system, slot, tgid, subscription_store=subscription_store
    ):
        return True
    return False


def peer_matches_rf_source(peer_id: bytes, rf_src: bytes, peers: dict[Any, Any]) -> bool:
    """True when a hotspot radio id matches the voice RF source (parrot / echo downlink)."""
    peer_b = _peer_key_from_int(peer_id)
    return peer_b in _fuzzy_peer_matches(int_id(rf_src), peers)
