# ADN DMR Peer Server - application report monitor topology
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

"""Monitor topology parity for inject-only proxy (legacy SYSTEM-N / 56400+N).

Hotspot radio IDs often share a user/subscriber prefix, e.g. user ``7300391`` with
HS1 ``730039101``, HS2 ``730039102``, … HS99 ``730039199`` (``user * 100 + n``).
Voice-event peer resolution must not guess when several connected peers match the
same user or 6-digit legacy prefix.
"""

from __future__ import annotations

from typing import Any

from adn_server.application.proxy.deployment import is_proxy_inject_only, proxy_target_system
from adn_server.application.routing.downlink import (
    DownlinkContext,
    peer_monitor_end_tx_conflicts_with_session,
    peer_would_show_group_voice_on_monitor,
)
from adn_server.application.routing.helpers import is_special_tg, peer_should_receive_group_voice
from adn_server.domain.value_objects import bytes_4, int_id

DEFAULT_REPORT_BASE_PORT = 56400


def _connected_peers(peers: dict[Any, dict[str, Any]]) -> list[tuple[Any, dict[str, Any]]]:
    return [
        (peer_key, peer)
        for peer_key, peer in peers.items()
        if isinstance(peer, dict) and peer.get("CONNECTION") == "YES"
    ]


def _resolve_slot_map(
    connected: list[tuple[Any, dict[str, Any]]],
    peer_slots: dict[bytes, int] | None,
    *,
    max_slots: int,
) -> dict[Any, int]:
    """Map peer keys to upstream slot indices for monitor ``SYSTEM-N`` rows."""
    slot_map: dict[Any, int] = {}
    if peer_slots:
        for peer_key, _peer in connected:
            if isinstance(peer_key, bytes) and peer_key in peer_slots:
                slot_map[peer_key] = peer_slots[peer_key]
    used = set(slot_map.values())
    for peer_key, _peer in sorted(connected, key=lambda item: int_id(item[0])):
        if peer_key in slot_map:
            continue
        for index in range(max_slots):
            if index not in used:
                slot_map[peer_key] = index
                used.add(index)
                break
    return slot_map


def expand_inject_proxy_systems(
    config: dict[str, Any],
    systems: dict[str, Any],
    peer_slots: dict[bytes, int] | None = None,
) -> dict[str, Any]:
    """Fan inject-only ``SYSTEM`` peers into ``SYSTEM-N`` masters for monitor/report.

    Runtime HBP stays on a single inject target; only the topology snapshot sent to
    adn-monitor matches legacy ``expand_generator`` + adn-proxy upstream ports.
    """
    target = proxy_target_system(config)
    if not target or not is_proxy_inject_only(config, target):
        return systems
    sys_cfg = systems.get(target)
    if not isinstance(sys_cfg, dict) or sys_cfg.get("MODE") != "MASTER":
        return systems
    peers = sys_cfg.get("PEERS", {})
    if not isinstance(peers, dict):
        return systems

    max_slots = int(sys_cfg.get("MAX_PEERS", 1))
    base_port = int(sys_cfg.get("_REPORT_BASE_PORT", DEFAULT_REPORT_BASE_PORT))
    connected = _connected_peers(peers)
    slot_map = _resolve_slot_map(connected, peer_slots, max_slots=max_slots)

    out = {name: cfg for name, cfg in systems.items() if name != target}
    # Shallow: the report only reads it. A deep copy of every peer for each virtual master
    # blocked the reactor ~0.2 s per report with 26 hotspots and MAX_PEERS 200.
    base = {key: value for key, value in sys_cfg.items() if key != "PEERS"}
    # Legacy ``expand_generator``: emit every virtual master (SYSTEM-0..N-1) so the
    # monitor does not delete unused upstream slots on topology/config update.
    for slot in range(max_slots):
        virtual_name = f"{target}-{slot}"
        virtual = dict(base)
        virtual["PORT"] = base_port + slot
        # Merged TS1/TS2 on inject SYSTEM are for bridges only — not per-hotspot dashboard chips.
        virtual["TS1_STATIC"] = ""
        virtual["TS2_STATIC"] = ""
        virtual["PEERS"] = {
            peer_key: peers[peer_key]
            for peer_key, mapped in slot_map.items()
            if mapped == slot and peer_key in peers
        }
        out[virtual_name] = virtual
    return out


def _peer_key_from_int(peer_key: Any) -> bytes:
    if isinstance(peer_key, bytes):
        return peer_key
    return bytes_4(int_id(peer_key))


def _connected_peer_keys(peers: dict[Any, Any]) -> list[bytes]:
    keys: list[bytes] = []
    for peer_key, peer in peers.items():
        if not isinstance(peer, dict) or peer.get("CONNECTION") != "YES":
            continue
        keys.append(_peer_key_from_int(peer_key))
    return keys


def _unique_peer_match(matches: list[bytes]) -> bytes | None:
    unique = list(dict.fromkeys(matches))
    return unique[0] if len(unique) == 1 else None


def _peers_for_voice_candidate(val: int, connected: list[bytes]) -> list[bytes]:
    """Match a BRDG_EVENT peer/subscriber field to connected hotspot radio ids."""
    exact = bytes_4(val)
    if exact in connected:
        return [exact]
    val_str = str(val)
    matches: list[bytes] = []
    for peer_key in connected:
        peer_int = int_id(peer_key)
        peer_str = str(peer_int)
        if peer_str == val_str:
            matches.append(peer_key)
            continue
        # user 7300391 → radios 730039101..730039199 (user * 100 + hs)
        if peer_int // 100 == val:
            matches.append(peer_key)
            continue
        if len(val_str) >= 5 and len(peer_str) >= 7 and peer_str.startswith(val_str):
            matches.append(peer_key)
            continue
        if len(val_str) >= 7 and peer_str.startswith(val_str) and len(peer_str) == len(val_str) + 2:
            matches.append(peer_key)
            continue
        # legacy 6-digit dst (bridge.py hotspot match) — ambiguous when user has >1 HS
        if len(val_str) >= 6 and len(peer_str) >= 6 and peer_str[:6] == val_str[:6]:
            matches.append(peer_key)
    return matches


def _peer_key_from_voice_csv(parts: list[str], peers: dict[Any, Any]) -> bytes | None:
    """Resolve hotspot radio id from legacy BRDG_EVENT CSV (peer_id, then rf_src).

    Prefer exact radio ids. Fuzzy user/6-digit matching applies only when a single
    connected peer matches (e.g. one HS online for user 7300391).
    """
    connected = _connected_peer_keys(peers)
    if not connected:
        return None
    field_values: list[tuple[int, int]] = []
    for idx in (5, 6):
        if len(parts) <= idx:
            continue
        raw = parts[idx].strip()
        if not raw:
            continue
        try:
            field_values.append((idx, int(raw)))
        except ValueError:
            continue
    for _idx, val in field_values:
        key = bytes_4(val)
        if key in connected:
            return key
    for _idx, val in field_values:
        matched = _peers_for_voice_candidate(val, connected)
        resolved = _unique_peer_match(matched)
        if resolved is not None:
            return resolved
    return None


def _voice_event_tgid_slot(parts: list[str]) -> tuple[int, int] | None:
    if len(parts) < 9:
        return None
    try:
        return int(parts[8].strip()), int(parts[7].strip())
    except ValueError:
        return None


def _voice_event_stream_id(parts: list[str]) -> bytes | None:
    if len(parts) <= 4:
        return None
    raw = parts[4].strip()
    if not raw:
        return None
    try:
        return bytes_4(int(raw))
    except ValueError:
        return None


def _private_voice_tx_dest_peer_id(parts: list[str]) -> int | None:
    """Destination hotspot peer id trailing a PRIVATE VOICE TX event, if present.

    routing_use_cases._pvt_call_received appends this (SUB_MAP's known peer for the
    destination) after the legacy 9 START / 10 END fields. Private call destinations
    are subscriber ids, never a real static TG -- _peers_receiving_tgid's group-voice
    subscription fan-out can never resolve one, so this field is the only way to know
    which single hotspot slot the event belongs to.
    """
    if len(parts) < 2 or parts[0].strip() != "PRIVATE VOICE" or parts[2].strip() != "TX":
        return None
    base_len = 10 if parts[1].strip() == "END" else 9
    if len(parts) <= base_len:
        return None
    try:
        return int(parts[-1].strip())
    except ValueError:
        return None


def _peers_receiving_tgid(
    connected: list[tuple[Any, dict[str, Any]]],
    *,
    slot: int,
    tgid: int,
    exclude: bytes | None = None,
    system: str | None = None,
    bridges: dict[str, Any] | None = None,
    sys_cfg: dict[str, Any] | None = None,
    downlink_ctx: DownlinkContext | None = None,
    stream_id: bytes | None = None,
    for_end_tx: bool = False,
) -> list[tuple[Any, dict[str, Any]]]:
    out: list[tuple[Any, dict[str, Any]]] = []
    n_connected = len(connected)
    for peer_key, peer in connected:
        pk = _peer_key_from_int(peer_key)
        if exclude is not None and pk == exclude:
            continue
        options_ok = peer_should_receive_group_voice(
            peer,
            slot,
            tgid,
            peer_id=pk,
            system=system,
            bridges=bridges,
            connected_count=n_connected,
            sys_cfg=sys_cfg,
        )
        if peer_would_show_group_voice_on_monitor(
            downlink_ctx,
            pk,
            peer,
            slot,
            tgid,
            options_eligible=options_ok,
            stream_id=stream_id,
        ):
            if for_end_tx and peer_monitor_end_tx_conflicts_with_session(
                downlink_ctx, pk, peer, slot, tgid, stream_id,
            ):
                continue
            out.append((peer_key, peer))
    return out


def _echo_tx_target_peer(
    parts: list[str],
    peers: dict[Any, Any],
    *,
    status: dict[int, dict[str, Any]] | None = None,
) -> bytes | None:
    """Echo/service downlink TX: field 5 may be 9990 or hotspot id; field 8 is 9990–9999.

    For special TGs (9990–9999) the audio is point-to-point: the downlink goes only to
    the peer that originated the call. The runtime ``STATUS[slot]["RX_PEER"]`` holds that
    exact peer id, so we prefer it. Fuzzy matching on ``rf_src`` (field 6) resolves the
    wrong hotspot when a user has several radios sharing a DMR-id prefix (e.g.
    rf_src 7140023 matches peer 714002301 via ``// 100`` instead of the real 714000103).
    """
    tgid_slot = _voice_event_tgid_slot(parts)
    if tgid_slot is not None:
        tgid, voice_slot = tgid_slot
        if is_special_tg(str(tgid)):
            if status is not None:
                slot_st = status.get(voice_slot, {})
                rx_peer = slot_st.get("RX_PEER", b"")
                if rx_peer and rx_peer != b"\x00\x00\x00\x00":
                    rx_b = bytes_4(int_id(rx_peer))
                    connected = _connected_peer_keys(peers)
                    if rx_b in connected:
                        return rx_b
            return _peer_key_from_voice_csv(parts, peers)
    if len(parts) <= 5:
        return None
    try:
        if int(parts[5].strip()) != 9990:
            return None
    except ValueError:
        return None
    return _peer_key_from_voice_csv(parts, peers)


def _remap_voice_event_to_slot(
    parts: list[str],
    *,
    target: str,
    slot: int,
    peer_key: bytes | None,
) -> str:
    out = list(parts)
    out[3] = f"{target}-{slot}"
    # RX legs: field 5 is the RF source peer — normalize to full hotspot radio id.
    # TX legs: keep legacy field 5 (echo 9990, OBP server id) so the hotspot chip
    # shows TX/green while receiving; rewriting to the hotspot id would mark RX/red.
    if (
        peer_key is not None
        and len(out) > 5
        and len(out) > 2
        and out[2].strip() == "RX"
    ):
        resolved_peer = int_id(peer_key)
        try:
            reported_peer = int(out[5].strip())
        except ValueError:
            reported_peer = None
        if reported_peer != resolved_peer:
            out[5] = str(resolved_peer)
    return ",".join(out)


def remap_inject_proxy_voice_events(
    event: str,
    config: dict[str, Any],
    systems: dict[str, Any],
    peer_slots: dict[bytes, int] | None = None,
    bridges: dict[str, Any] | None = None,
    downlink_ctx: DownlinkContext | None = None,
) -> list[str]:
    """Map inject-only ``SYSTEM`` voice events to one or more ``SYSTEM-N`` rows.

    Inject-only multi-hotspot needs fan-out in two cases:

    * **TX** (bridge downlink, OBP → SYSTEM): peers that would get the DMRD downlink
      (per-peer OPTIONS static list, sole connected hotspot, or owned dynamic UA).
    * **RX** (local hotspot TX + HBP REPEAT): transmitter keeps RX; other eligible
      peers get companion **TX** (field 5 = transmitter radio id).
    Echo/static TX (field 5 == 9990) still targets a single resolved hotspot.
    """
    target = proxy_target_system(config)
    if not target or not is_proxy_inject_only(config, target):
        return [event]
    parts = event.split(",")
    if len(parts) < 6 or parts[3].strip() != target:
        return [event]
    if len(parts) > 9 and parts[-1].strip() == "1":
        # Synthetic announcement PTT: never attribute/fan-out to a real hotspot
        # row — a connected peer's radio id may coincidentally match the
        # announcement's spoofed rf_src (field 6).
        return [event]
    sys_cfg = systems.get(target, {})
    if not isinstance(sys_cfg, dict):
        return [event]
    peers = sys_cfg.get("PEERS", {})
    if not isinstance(peers, dict):
        return [event]
    max_slots = int(sys_cfg.get("MAX_PEERS", 1))
    connected = _connected_peers(peers)
    slot_map = _resolve_slot_map(connected, peer_slots, max_slots=max_slots)
    trx = parts[2].strip() if len(parts) > 2 else ""
    stream_id = _voice_event_stream_id(parts)

    if trx == "TX":
        dest_peer_id = _private_voice_tx_dest_peer_id(parts)
        if dest_peer_id is not None:
            peer_key = bytes_4(dest_peer_id)
            if peer_key not in _connected_peer_keys(peers):
                return []
            slot = slot_map.get(peer_key)
            if slot is None:
                return []
            return [
                _remap_voice_event_to_slot(
                    parts, target=target, slot=slot, peer_key=peer_key
                )
            ]
        tgid_slot = _voice_event_tgid_slot(parts)
        echo_peer = _echo_tx_target_peer(
            parts, peers, status=downlink_ctx.status if downlink_ctx else None
        )
        if echo_peer is not None:
            slot = slot_map.get(echo_peer)
            if slot is not None:
                tx_parts = list(parts)
                if tgid_slot is not None:
                    tgid, _ = tgid_slot
                    if is_special_tg(str(tgid)):
                        tx_parts[5] = str(tgid)
                return [
                    _remap_voice_event_to_slot(
                        tx_parts, target=target, slot=slot, peer_key=echo_peer
                    )
                ]
        try:
            if int(parts[5].strip()) == 9990:
                # Echo/static path with ambiguous hotspot — legacy leaves event unchanged.
                return [event]
        except ValueError:
            pass
        tgid_slot = _voice_event_tgid_slot(parts)
        if tgid_slot is None:
            return [event]
        tgid, voice_slot = tgid_slot
        action = parts[1].strip() if len(parts) > 1 else ""
        receivers = _peers_receiving_tgid(
            connected,
            slot=voice_slot,
            tgid=tgid,
            system=target,
            bridges=bridges,
            sys_cfg=sys_cfg,
            downlink_ctx=downlink_ctx,
            stream_id=stream_id,
            for_end_tx=(action == "END"),
        )
        if not receivers:
            return []
        remapped: list[str] = []
        for peer_key, _peer in receivers:
            mapped_slot = slot_map.get(peer_key)
            if mapped_slot is None:
                continue
            remapped.append(
                _remap_voice_event_to_slot(
                    parts, target=target, slot=mapped_slot, peer_key=peer_key
                )
            )
        return remapped

    peer_key = _peer_key_from_voice_csv(parts, peers)
    if peer_key is None:
        return [event]
    slot = slot_map.get(peer_key)
    if slot is None:
        return [event]
    results = [
        _remap_voice_event_to_slot(
            parts, target=target, slot=slot, peer_key=peer_key
        )
    ]
    action = parts[1].strip() if len(parts) > 1 else ""
    tgid_slot = _voice_event_tgid_slot(parts)
    if action in ("START", "END") and tgid_slot is not None:
        tgid, voice_slot = tgid_slot
        tx_parts = list(parts)
        tx_parts[2] = "TX"
        tx_parts[5] = str(int_id(peer_key))
        for other_key, _peer in _peers_receiving_tgid(
            connected,
            slot=voice_slot,
            tgid=tgid,
            exclude=peer_key,
            system=target,
            bridges=bridges,
            sys_cfg=sys_cfg,
            downlink_ctx=downlink_ctx,
            stream_id=stream_id,
        ):
            other_slot = slot_map.get(other_key)
            if other_slot is None:
                continue
            results.append(
                _remap_voice_event_to_slot(
                    tx_parts,
                    target=target,
                    slot=other_slot,
                    peer_key=other_key,
                )
            )
    return results


def remap_inject_proxy_voice_event(
    event: str,
    config: dict[str, Any],
    systems: dict[str, Any],
    peer_slots: dict[bytes, int] | None = None,
    bridges: dict[str, Any] | None = None,
    downlink_ctx: DownlinkContext | None = None,
) -> str:
    """Single-event view of :func:`remap_inject_proxy_voice_events` (first mapping)."""
    return remap_inject_proxy_voice_events(
        event, config, systems, peer_slots, bridges, downlink_ctx
    )[0]


def remap_inject_proxy_voice_event_for_peer(
    event: str,
    config: dict[str, Any],
    systems: dict[str, Any],
    peer_key: bytes,
    peer_slots: dict[bytes, int] | None = None,
    bridges: dict[str, Any] | None = None,
    downlink_ctx: DownlinkContext | None = None,
) -> str | None:
    """Map one ``SYSTEM`` voice event to ``SYSTEM-N`` for a single peer when eligible."""
    target = proxy_target_system(config)
    if not target or not is_proxy_inject_only(config, target):
        return None
    parts = event.split(",")
    if len(parts) < 6 or parts[3].strip() != target:
        return None
    sys_cfg = systems.get(target, {})
    if not isinstance(sys_cfg, dict):
        return None
    peers = sys_cfg.get("PEERS", {})
    if not isinstance(peers, dict):
        return None
    pk = _peer_key_from_int(peer_key)
    peer = peers.get(pk)
    if not isinstance(peer, dict):
        return None
    tgid_slot = _voice_event_tgid_slot(parts)
    if tgid_slot is None:
        return None
    tgid, voice_slot = tgid_slot
    stream_id = _voice_event_stream_id(parts)
    action = parts[1].strip() if len(parts) > 1 else ""
    if not peer_would_show_group_voice_on_monitor(
        downlink_ctx,
        pk,
        peer,
        voice_slot,
        tgid,
        options_eligible=peer_should_receive_group_voice(
            peer,
            voice_slot,
            tgid,
            peer_id=pk,
            system=target,
            bridges=bridges,
            connected_count=len(_connected_peers(peers)),
            sys_cfg=sys_cfg,
        ),
        stream_id=stream_id,
    ):
        return None
    if action == "END" and peer_monitor_end_tx_conflicts_with_session(
        downlink_ctx, pk, peer, voice_slot, tgid, stream_id,
    ):
        return None
    max_slots = int(sys_cfg.get("MAX_PEERS", 1))
    slot_map = _resolve_slot_map(_connected_peers(peers), peer_slots, max_slots=max_slots)
    mapped_slot = slot_map.get(pk)
    if mapped_slot is None:
        return None
    return _remap_voice_event_to_slot(
        parts, target=target, slot=mapped_slot, peer_key=pk
    )
