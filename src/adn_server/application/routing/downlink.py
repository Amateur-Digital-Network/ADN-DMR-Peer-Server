# ADN DMR Peer Server - per-hotspot downlink gate
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

"""Single authority for hotspot downlink eligibility, remap, slot busy, and hangtime."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from adn_server.domain import HBPF_DATA_SYNC, HBPF_SLT_VHEAD, HBPF_SLT_VTERM, bytes_3, bytes_4, int_id
from adn_server.domain.hbp_protocol import STREAM_TO

from .helpers import (
    SIMPLEX_VOICE_SLOT,
    _peer_transmit_hangtime_blocks,
    _peer_ua_session_entry,
    clear_peer_ua_sessions,
    hbp_slot_blocks_group_voice_for_peer_reason,
    is_special_tg,
    is_ua_session_tgid,
    master_per_peer_slot_contention,
    parse_dmrd_burst_fields,
    parse_dmrd_route_fields,
    peer_downlink_voice_slot,
    peer_dynamic_tg_active_on_both_slots,
    peer_dynamic_tg_active_on_slot,
    peer_is_simplex,
    peer_options_static_tg_slot,
    peer_receives_group_tgid,
    peer_should_receive_group_voice,
    peer_single_exclusive_tgid,
    peer_single_mode,
    peer_wants_downlink_single_listen_lock,
    register_peer_ua_session,
    remap_dmrd_to_peer_static_slot,
    slot_has_active_voice,
    slot_status_hotspot_owner,
    synthetic_group_dmrd_route_packet,
)

PeerVoiceSlotRow = dict[str, Any]
PeerVoiceSlotMap = dict[int, PeerVoiceSlotRow]
PeerHangMap = dict[int, tuple[int, float]]


@dataclass
class DownlinkContext:
    """Runtime state for per-hotspot downlink gating on one MASTER."""

    config: dict[str, Any]
    system_name: str
    sys_cfg: dict[str, Any]
    peers: dict[Any, Any]
    status: dict[int, dict[str, Any]]
    connected_count: int = 0
    peer_voice_slots: dict[bytes, PeerVoiceSlotMap] = field(default_factory=dict)
    peer_voice_hangtime: dict[bytes, PeerHangMap] = field(default_factory=dict)
    subscription_store: Any | None = None

    def per_peer_contention(self) -> bool:
        return master_per_peer_slot_contention(
            self.config,
            self.system_name,
            self.sys_cfg,
            connected_count=self.connected_count,
        )


def normalize_ua_voice_slot(peer: dict[str, Any], wire_slot: int) -> int:
    """SINGLE / UA dynamics use TS2 on simplex hotspots (MMDVMHost DMO parity)."""
    if peer_is_simplex(peer):
        return SIMPLEX_VOICE_SLOT
    return int(wire_slot)


def peer_listen_slots(peer: dict[str, Any], tgid: int) -> list[int]:
    """RF slots where this peer listens for ``tgid`` (static OPTIONS or wire fallback).

    A genuinely duplex-capable peer (see ``peer_is_simplex``) with the same TG
    listed on both TS1 and TS2 gets both slots back -- it has two independent
    RF timeslots and is expected to key up on both, same as a real repeater
    configured that way. Simplex peers/bridges always collapse to one slot.
    """
    from adn_server.application.routing.peer_downlink_index import cached_peer_static_tgs

    ts1, ts2 = cached_peer_static_tgs(peer)
    if peer_is_simplex(peer):
        tg = str(tgid)
        if tg in ts1 or tg in ts2:
            return [SIMPLEX_VOICE_SLOT]
        static = peer_options_static_tg_slot(peer, tgid)
        if static is not None:
            return [static]
        return []
    tg = str(tgid)
    in_ts1 = tg in ts1
    in_ts2 = tg in ts2
    if in_ts1 and in_ts2:
        return [1, 2]
    if in_ts1:
        return [1]
    if in_ts2:
        return [2]
    static = peer_options_static_tg_slot(peer, tgid)
    if static is not None:
        return [static]
    return []


def peer_accepts_group_downlink(
    ctx: DownlinkContext,
    peer_id: bytes,
    peer: dict[str, Any],
    wire_slot: int,
    tgid: int,
    *,
    call_type: str = "group",
) -> bool:
    """P1: same eligibility as DMRD group downlink for ``(wire_slot, tgid)``."""
    if call_type not in ("group", "vcsbk"):
        return True
    return peer_should_receive_group_voice(
        peer,
        wire_slot,
        tgid,
        peer_id=peer_id,
        system=ctx.system_name,
        bridges=None,
        subscription_store=ctx.subscription_store,
        connected_count=ctx.connected_count,
        sys_cfg=ctx.sys_cfg,
    )


def peer_hangtime_voice_slots(
    peer: dict[str, Any],
    wire_slot: int,
    tgid: int,
    sys_cfg: dict[str, Any] | None,
    *,
    peer_id: bytes | None = None,
) -> set[int]:
    """RF / OPTIONS slots that share transmit hangtime for this downlink.

    ``wire_slot`` is already the caller's final, decided delivery slot (the
    packet has already been remapped). If this peer is genuinely eligible for
    ``tgid`` on ``wire_slot`` (static or dynamic, per the same combined logic
    ``iter_downlink_voice_slots`` uses for the actual delivery decision),
    trust it and check only that slot -- falling back to also deriving
    ``rf_slot``/``listen_slot`` would pull in an unrelated slot (e.g. a static
    slot for the same TG on this peer) whenever ``peer_downlink_voice_slot``'s
    single-slot answer differs from ``wire_slot``, wrongly reporting that
    unrelated slot's own busy/ingress state instead of ``wire_slot``'s.
    """
    wire_slot_i = int(wire_slot)
    eligible = iter_downlink_voice_slots(peer, wire_slot_i, int(tgid), sys_cfg, peer_id=peer_id)
    if wire_slot_i in eligible:
        return {wire_slot_i}
    rf_slot = normalize_ua_voice_slot(peer, wire_slot_i)
    listen_slot = peer_downlink_voice_slot(
        peer, wire_slot_i, int(tgid), sys_cfg, peer_id=peer_id,
    )
    return {int(rf_slot), wire_slot_i, int(listen_slot)}


def peer_slot_block_reason(
    ctx: DownlinkContext,
    peer_id: bytes,
    peer: dict[str, Any],
    packet: bytes,
    *,
    pkt_time: float | None = None,
) -> str | None:
    """P2/P3: reason this downlink is slot-busy/hangtime blocked, or None if not.

    Same logic as ``peer_slot_blocks_downlink`` (which just checks ``is not
    None``) -- kept as one function so the diagnostic reason can never drift
    from the actual accept/reject decision.
    """
    if packet[:4] != b"DMRD":
        return None
    parsed = parse_dmrd_route_fields(packet)
    if parsed is None:
        return None
    wire_slot, tgid, call_type = parsed
    if call_type not in ("group", "vcsbk"):
        return None
    pk = bytes_4(int_id(peer_id))
    burst = parse_dmrd_burst_fields(packet)
    stream_id = burst[3] if burst is not None else b""
    if burst is not None:
        _wire_slot, frame_type, dtype_vseq, _sid, dst_id, _call_type = burst
        if frame_type == HBPF_DATA_SYNC and dtype_vseq == HBPF_SLT_VTERM:
            now_vt = time.time() if pkt_time is None else float(pkt_time)
            listen_slot = peer_downlink_voice_slot(
                peer, wire_slot, int_id(dst_id), ctx.sys_cfg, peer_id=peer_id,
            )
            slot_st = ctx.status.get(int(listen_slot), {})
            per_slot = ctx.peer_voice_slots.get(pk, {})
            for row in per_slot.values():
                if not isinstance(row, dict):
                    continue
                active_stream = row.get("stream_id")
                if stream_id and active_stream and active_stream != stream_id:
                    active_time = float(row.get("time", 0) or 0)
                    if (now_vt - active_time) < STREAM_TO:
                        active_tg = int(row.get("tgid", 0) or 0)
                        incoming_tg = int_id(dst_id)
                        if (
                            active_tg
                            and incoming_tg
                            and active_tg != incoming_tg
                            and not row.get("ingress")
                        ):
                            owner = slot_status_hotspot_owner(slot_st, ctx.peers)
                            pk_vt = bytes_4(int_id(peer_id))
                            rx_listening = (
                                owner is not None
                                and bytes_4(int_id(owner)) == pk_vt
                                and slot_has_active_voice(slot_st, now_vt)
                                and int_id(slot_st.get("RX_TGID", b"")) == active_tg
                            )
                            if not rx_listening:
                                return f"VTERM: other stream TG {active_tg} still active on this peer"
            active = per_slot.get(int(listen_slot))
            if not isinstance(active, dict):
                for row in per_slot.values():
                    if not isinstance(row, dict):
                        continue
                    if int(row.get("tgid", 0) or 0) == int_id(dst_id):
                        return None
                return None
            if active.get("stream_id") == stream_id:
                return None
            if int(active.get("tgid", 0) or 0) == int_id(dst_id):
                return None
            return f"VTERM: listen slot busy with TG {int(active.get('tgid', 0) or 0)}"
    if not ctx.per_peer_contention():
        return None
    hang = float(ctx.sys_cfg.get("GROUP_HANGTIME", 0) or 0)
    now = time.time() if pkt_time is None else float(pkt_time)
    seed_hangtime_for_stale_ingress_voice_slots(ctx, peer_id, pkt_time=now)
    incoming_tgid_b = bytes_3(tgid)
    if hang > 0:
        for hang_row in ctx.peer_voice_hangtime.get(pk, {}).values():
            if _peer_transmit_hangtime_blocks(hang_row, incoming_tgid_b, now, hang):
                return "GROUP_HANGTIME: recent local transmit still hanging on this peer"
    peer_slots = ctx.peer_voice_slots.get(pk)
    voice_slots = peer_hangtime_voice_slots(
        peer, wire_slot, tgid, ctx.sys_cfg, peer_id=peer_id,
    )
    for voice_slot in sorted(voice_slots):
        hang_row = ctx.peer_voice_hangtime.get(pk, {}).get(voice_slot)
        slot_st = ctx.status.get(voice_slot, {})
        busy_reason = hbp_slot_blocks_group_voice_for_peer_reason(
            slot_st,
            peer_id,
            incoming_tgid_b,
            stream_id,
            now,
            hang,
            per_peer=True,
            peers=ctx.peers,
            peer_slots=peer_slots,
            peer_hang_row=hang_row,
            voice_slot=voice_slot,
            sys_cfg=ctx.sys_cfg,
        )
        if busy_reason is not None:
            return busy_reason
    return None


def peer_slot_blocks_downlink(
    ctx: DownlinkContext,
    peer_id: bytes,
    peer: dict[str, Any],
    packet: bytes,
    *,
    pkt_time: float | None = None,
) -> bool:
    """P2/P3: True when slot busy or GROUP_HANGTIME blocks this downlink."""
    return peer_slot_block_reason(ctx, peer_id, peer, packet, pkt_time=pkt_time) is not None


def remap_dmrd_for_peer(
    packet: bytes,
    peer: dict[str, Any],
    sys_cfg: dict[str, Any] | None,
    *,
    peer_id: bytes | None = None,
    voice_slot: int | None = None,
) -> bytes:
    """Flip DMRD slot bit to the peer RF listen slot for this TG."""
    if voice_slot is not None:
        parsed = parse_dmrd_route_fields(packet)
        if parsed is None:
            return packet
        wire_slot, tgid, call_type = parsed
        if call_type not in ("group", "vcsbk") or is_special_tg(str(tgid)):
            return packet
        if int(voice_slot) == int(wire_slot):
            return packet
        bits = packet[15]
        new_bits = bits ^ (1 << 7)
        return packet[:15] + bytes([new_bits]) + packet[16:]
    return remap_dmrd_to_peer_static_slot(packet, peer, sys_cfg, peer_id=peer_id)


def apply_hangtime_after_vterm(
    ctx: DownlinkContext,
    peer_id: bytes,
    voice_slot: int,
    tgid: int,
    *,
    pkt_time: float | None = None,
) -> None:
    """P3: record post-VTERM GROUP_HANGTIME window for SINGLE=0 hotspots."""
    now = time.time() if pkt_time is None else float(pkt_time)
    pk = bytes_4(int_id(peer_id))
    per_slot = ctx.peer_voice_slots.get(pk, {})
    per_slot.pop(int(voice_slot), None)
    ctx.peer_voice_hangtime.setdefault(pk, {})[int(voice_slot)] = (int(tgid), float(now))


def touch_peer_voice_slot(
    ctx: DownlinkContext,
    peer_id: bytes,
    voice_slot: int,
    stream_id: bytes,
    tgid: bytes,
    *,
    pkt_time: float | None = None,
    clear_hangtime: bool = True,
    ingress: bool = False,
) -> None:
    """Open per-hotspot voice session until VTERM."""
    now = time.time() if pkt_time is None else float(pkt_time)
    pk = bytes_4(int_id(peer_id))
    row: dict[str, Any] = {
        "stream_id": stream_id,
        "tgid": int_id(tgid),
        "time": float(now),
    }
    prev = ctx.peer_voice_slots.get(pk, {}).get(int(voice_slot))
    if not ingress and isinstance(prev, dict) and prev.get("ingress"):
        return
    if ingress or (isinstance(prev, dict) and prev.get("ingress")):
        row["ingress"] = True
    ctx.peer_voice_slots.setdefault(pk, {})[int(voice_slot)] = row
    if clear_hangtime:
        ctx.peer_voice_hangtime.get(pk, {}).pop(int(voice_slot), None)


def seed_hangtime_for_stale_ingress_voice_slots(
    ctx: DownlinkContext,
    peer_id: bytes,
    *,
    pkt_time: float | None = None,
) -> None:
    """Start GROUP_HANGTIME from last ingress burst when PTT ended before VTERM."""
    hang = float(ctx.sys_cfg.get("GROUP_HANGTIME", 0) or 0)
    if hang <= 0:
        return
    now = time.time() if pkt_time is None else float(pkt_time)
    pk = bytes_4(int_id(peer_id))
    per_slot = ctx.peer_voice_slots.get(pk)
    if not isinstance(per_slot, dict):
        return
    peer_hang = ctx.peer_voice_hangtime.setdefault(pk, {})
    for voice_slot, active in per_slot.items():
        if not isinstance(active, dict) or not active.get("ingress"):
            continue
        active_time = float(active.get("time", 0) or 0)
        if active_time <= 0 or (now - active_time) < STREAM_TO:
            continue
        ended_tg = int(active.get("tgid", 0) or 0)
        if not ended_tg or int(voice_slot) in peer_hang:
            continue
        peer_hang[int(voice_slot)] = (ended_tg, active_time + STREAM_TO)


def end_peer_voice_slot(
    ctx: DownlinkContext,
    peer_id: bytes,
    voice_slot: int,
    stream_id: bytes,
    tgid: bytes,
    *,
    pkt_time: float | None = None,
    apply_hangtime: bool = True,
    from_ingress: bool = False,
) -> None:
    """Close session on VTERM; ingress VTERM may start GROUP_HANGTIME window."""
    now = time.time() if pkt_time is None else float(pkt_time)
    pk = bytes_4(int_id(peer_id))
    per_slot = ctx.peer_voice_slots.get(pk, {})
    ended_tg = int_id(tgid)
    active = per_slot.pop(int(voice_slot), None)
    if active is None and ended_tg:
        for vs, row in list(per_slot.items()):
            if isinstance(row, dict) and int(row.get("tgid", 0) or 0) == ended_tg:
                voice_slot = int(vs)
                active = per_slot.pop(vs, None)
                break
    peer = ctx.peers.get(peer_id) or ctx.peers.get(pk)
    if (
        isinstance(peer, dict)
        and ended_tg
        and ctx.sys_cfg is not None
        and not peer_single_mode(peer, ctx.sys_cfg)
        and ctx.subscription_store is not None
        and ctx.system_name
    ):
        from adn_server.application.subscription.subscription_queries import (
            system_has_active_leg_in_store,
        )

        voice_ended = isinstance(active, dict) and (
            active.get("stream_id") or active.get("ingress")
        )
        if voice_ended and system_has_active_leg_in_store(
            ctx.subscription_store,
            ctx.system_name,
            int(voice_slot),
            int(ended_tg),
        ):
            per_slot[int(voice_slot)] = {
                "stream_id": b"",
                "tgid": int(ended_tg),
                "time": float(now),
                "bridge_hold": True,
                "bridge_hold_ingress": bool(from_ingress),
            }
            ctx.peer_voice_slots.setdefault(pk, {})[int(voice_slot)] = per_slot[int(voice_slot)]
            return
    if not apply_hangtime:
        return
    if isinstance(active, dict):
        active_tg = int(active.get("tgid", 0) or 0)
        # Only seed hangtime when the VTERM TG matches the session TG. A VTERM for a
        # different TG (e.g. foreign stream that this peer never heard) must not seed
        # hangtime from an unrelated active/bridge_hold session.
        if active_tg and active_tg == ended_tg:
            apply_hangtime_after_vterm(ctx, peer_id, voice_slot, active_tg, pkt_time=now)
        return
    # No active session for this peer: VTERM for a stream it never received — do not
    # seed GROUP_HANGTIME (would block a fresh PTT on a different TG).


def track_peer_group_dmrd(
    ctx: DownlinkContext,
    peer_id: bytes,
    packet: bytes,
    peer: dict[str, Any],
    *,
    pkt_time: float | None = None,
    from_ingress: bool = False,
    voice_slot: int | None = None,
) -> None:
    """Update per-hotspot slot state from downlink or ingress DMRD."""
    burst = parse_dmrd_burst_fields(packet)
    if burst is None:
        return
    wire_slot, frame_type, dtype_vseq, stream_id, dst_id, _call_type = burst
    if voice_slot is None:
        voice_slot = peer_downlink_voice_slot(
            peer, wire_slot, int_id(dst_id), ctx.sys_cfg, peer_id=peer_id,
        )
    else:
        voice_slot = int(voice_slot)
    if frame_type == HBPF_DATA_SYNC and dtype_vseq == HBPF_SLT_VTERM:
        if (
            not from_ingress
            and peer_wants_downlink_single_listen_lock(peer, ctx.sys_cfg)
        ):
            pk = bytes_4(int_id(peer_id))
            per_slot = ctx.peer_voice_slots.get(pk, {})
            active = per_slot.get(int(voice_slot))
            if isinstance(active, dict) and not active.get("ingress"):
                ended_tg = int_id(dst_id)
                locked = peer_single_exclusive_tgid(
                    peer, voice_slot, ctx.sys_cfg, peer_id=peer_id, now=pkt_time,
                )
                active_tg = int(active.get("tgid", 0) or 0)
                entry = _peer_ua_session_entry(ctx.sys_cfg, peer_id, voice_slot)
                listen_lock = isinstance(entry, dict) and entry.get("source") == "listen"
                if locked is not None and locked == ended_tg:
                    if not isinstance(entry, dict) or entry.get("source") != "local":
                        clear_peer_ua_sessions(peer, ctx.sys_cfg, peer_id, slot=voice_slot)
                elif listen_lock and active_tg and active_tg == ended_tg:
                    clear_peer_ua_sessions(peer, ctx.sys_cfg, peer_id, slot=voice_slot)
        pk = bytes_4(int_id(peer_id))
        apply_hangtime = from_ingress
        if not from_ingress and not peer_single_mode(peer, ctx.sys_cfg):
            apply_hangtime = True
        end_peer_voice_slot(
            ctx,
            peer_id,
            voice_slot,
            stream_id,
            dst_id,
            pkt_time=pkt_time,
            apply_hangtime=apply_hangtime,
            from_ingress=from_ingress,
        )
        return
    if (
        not from_ingress
        and frame_type == HBPF_DATA_SYNC
        and dtype_vseq == HBPF_SLT_VHEAD
        and peer_wants_downlink_single_listen_lock(peer, ctx.sys_cfg)
    ):
        dst_tgid = int_id(dst_id)
        listen_lock_tg = is_ua_session_tgid(dst_tgid) or peer_receives_group_tgid(
            peer, wire_slot, dst_tgid,
        )
        if listen_lock_tg:
            pk = bytes_4(int_id(peer_id))
            per_slot = ctx.peer_voice_slots.get(pk, {})
            active = per_slot.get(int(voice_slot))
            locked = peer_single_exclusive_tgid(
                peer, voice_slot, ctx.sys_cfg, peer_id=peer_id, now=pkt_time,
            )
            entry = _peer_ua_session_entry(ctx.sys_cfg, peer_id, voice_slot)
            local_lock = (
                locked is not None
                and int(locked) == int(dst_tgid)
                and isinstance(entry, dict)
                and entry.get("source") == "local"
            )
            if local_lock:
                pass
            elif not isinstance(active, dict) or active.get("stream_id") != stream_id:
                now = time.time() if pkt_time is None else float(pkt_time)
                register_peer_ua_session(
                    peer, peer_id, voice_slot, dst_tgid, ctx.sys_cfg, now=now, source="listen",
                )
    touch_peer_voice_slot(
        ctx,
        peer_id,
        voice_slot,
        stream_id,
        dst_id,
        pkt_time=pkt_time,
        clear_hangtime=from_ingress,
        ingress=from_ingress,
    )


def peer_accepts_group_dmrd_packet(
    ctx: DownlinkContext,
    peer_id: bytes,
    peer: dict[str, Any],
    packet: bytes,
    *,
    routed: bool = False,
) -> bool:
    """True when group/vcsbk DMRD passes per-hotspot slot gate (OPTIONS checked separately).

    Special service TGs (9990–9999: echo/parrot/playback) are exempt from
    per-peer slot contention. Their delivery is already gated by RX_PEER matching
    in ``_peer_should_receive_dmrd``; applying the per-peer busy/hangtime gate
    on top blocks the echo's own playback from reaching the loopback peer.
    Legacy parity: ``bridge_master.to_target`` contention compares TGID + time
    only, and these TGs have a short 10s timeout with no per-peer scoping.
    """
    if not ctx.per_peer_contention():
        return True
    parsed = parse_dmrd_route_fields(packet)
    if parsed is not None:
        _slot, tgid, _call_type = parsed
        if is_special_tg(str(tgid)):
            return True
    route_pkt = (
        packet
        if routed
        else remap_dmrd_for_peer(packet, peer, ctx.sys_cfg, peer_id=peer_id)
    )
    return not peer_slot_blocks_downlink(ctx, peer_id, peer, route_pkt)


def peer_accepts_dmra(
    ctx: DownlinkContext,
    peer_id: bytes,
    slot: int,
    tgid: int,
    *,
    pkt_time: float | None = None,
) -> bool:
    """P1: DMRA uses same accept + slot-busy rules as DMRD."""
    peer = ctx.peers.get(peer_id)
    if not isinstance(peer, dict):
        return False
    route_pkt = synthetic_group_dmrd_route_packet(slot, tgid)
    if not peer_accepts_group_downlink(ctx, peer_id, peer, slot, tgid):
        return False
    remapped = remap_dmrd_for_peer(route_pkt, peer, ctx.sys_cfg, peer_id=peer_id)
    return not peer_slot_blocks_downlink(
        ctx, peer_id, peer, remapped, pkt_time=pkt_time,
    )


def peer_would_show_group_voice_on_monitor(
    ctx: DownlinkContext | None,
    peer_id: bytes,
    peer: dict[str, Any],
    wire_slot: int,
    tgid: int,
    *,
    options_eligible: bool,
    stream_id: bytes | None = None,
) -> bool:
    """Monitor fan-out: OPTIONS plus the same slot/hangtime gate as ``send_peer``.

    ``stream_id`` must match the BRDG_EVENT stream (field 4) so bridge ``TX_STREAM_ID``
    exemption aligns with live DMRD delivery — zero stream id false-blocks after hangtime.
    """
    if not options_eligible:
        return False
    if ctx is None or not ctx.per_peer_contention():
        return True
    route_pkt = synthetic_group_dmrd_route_packet(wire_slot, tgid, stream_id)
    remapped = remap_dmrd_for_peer(route_pkt, peer, ctx.sys_cfg, peer_id=peer_id)
    return not peer_slot_blocks_downlink(ctx, peer_id, peer, remapped)


def peer_monitor_end_tx_conflicts_with_session(
    ctx: DownlinkContext | None,
    peer_id: bytes,
    peer: dict[str, Any],
    wire_slot: int,
    tgid: int,
    stream_id: bytes | None,
) -> bool:
    """True when END/TX must not touch this hotspot (active QSO on another stream/TG)."""
    if ctx is None or not stream_id:
        return False
    pk = bytes_4(int_id(peer_id))
    peer_slots = ctx.peer_voice_slots.get(pk)
    if not isinstance(peer_slots, dict):
        return False
    incoming_tg = int(tgid)
    now = time.time()
    for voice_slot in peer_hangtime_voice_slots(
        peer, wire_slot, tgid, ctx.sys_cfg, peer_id=peer_id,
    ):
        row = peer_slots.get(int(voice_slot))
        if not isinstance(row, dict) or row.get("ingress"):
            continue
        active_stream = row.get("stream_id")
        active_tg = int(row.get("tgid", 0) or 0)
        if not active_stream or active_stream == stream_id:
            continue
        if (now - float(row.get("time", 0) or 0)) >= STREAM_TO:
            continue
        if not active_tg or active_tg == incoming_tg:
            continue
        slot_st = ctx.status.get(int(voice_slot), {})
        owner = slot_status_hotspot_owner(slot_st, ctx.peers)
        if (
            owner is not None
            and bytes_4(int_id(owner)) == pk
            and slot_has_active_voice(slot_st, now)
            and int_id(slot_st.get("RX_TGID", b"")) == active_tg
        ):
            return True
    return False


def iter_downlink_voice_slots(
    peer: dict[str, Any],
    wire_slot: int,
    tgid: int,
    sys_cfg: dict[str, Any] | None = None,
    *,
    peer_id: bytes | None = None,
) -> list[int]:
    """Voice slot(s) to deliver a group downlink to this peer.

    Trusts ``peer_listen_slots`` -- it already collapses simplex peers/bridges
    to one slot via ``peer_is_simplex``, and only returns more than one slot
    for a peer confirmed duplex-capable with the TG on both TS1 and TS2, which
    should genuinely receive on both (see peer_listen_slots docstring). Static
    vs dynamic makes no difference here -- a TG independently activated on
    both slots (SINGLE=0 keyed, or SINGLE=1 exclusive session per slot) gets
    the same dual-slot treatment via peer_dynamic_tg_active_on_both_slots.

    Mixed case: static on exactly one slot *and* dynamically active on the
    other (e.g. TS1 static, TS2 keyed dynamically) must also deliver to both
    -- checked via peer_dynamic_tg_active_on_slot on whichever slot the
    static list didn't already claim.
    """
    listen = peer_listen_slots(peer, tgid)
    if len(listen) > 1:
        return listen
    if len(listen) == 1 and not peer_is_simplex(peer):
        static_slot = listen[0]
        other_slot = 2 if static_slot == 1 else 1
        if peer_dynamic_tg_active_on_slot(peer, tgid, other_slot, sys_cfg, peer_id=peer_id):
            return sorted({static_slot, other_slot})
        return listen
    if peer_dynamic_tg_active_on_both_slots(peer, tgid, sys_cfg, peer_id=peer_id):
        return [1, 2]
    if listen:
        return listen
    if peer_receives_group_tgid(peer, wire_slot, tgid):
        return [peer_downlink_voice_slot(peer, wire_slot, tgid, sys_cfg, peer_id=peer_id)]
    return [int(wire_slot)]
