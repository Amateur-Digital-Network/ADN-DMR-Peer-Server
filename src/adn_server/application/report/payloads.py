# ADN DMR Peer Server - application report payloads
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

"""Map runtime SYSTEMS / BRIDGES / BRDG_EVENT CSV to report JSON payloads (application layer)."""

from __future__ import annotations

import re
import time
from typing import Any

from adn_server.application.routing.helpers import (
    export_peer_ua_multi_tgs,
    export_peer_ua_sessions,
    peer_rf_mode,
)
from adn_server.domain import int_id
from adn_server.domain.hbp_protocol import normalize_fixed_width_ascii
from adn_server.domain.ua_timer import normalize_ua_timer_minutes, ua_timer_is_infinite

REPORT_PROTOCOL = 2
REPORT_FEATURES = (
    "INGRESS",
    "END_TX_FORWARD",
    "PUSH_ON_CONNECT",
    "REPORT_V2",
    "ROUTING_TABLE_JSON",
    "VOICE_EVENT_JSON",
    "DELTA_UPDATES",
)

_SYSTEM_MODES = frozenset({"MASTER", "PEER", "XLXPEER", "OPENBRIDGE"})
_TO_TYPES = frozenset({"ON", "OFF", "STAT", "NONE"})
_CSV_FAMILIES = {
    "GROUP VOICE": "GROUP",
    "PRIVATE VOICE": "PRIVATE",
    "UNIT DATA": "UNIT",
    # routing_use_cases emits these labels for dtype_vseq in (6, 7, 8). CSBK (3) is
    # still logged at INFO but no longer emitted to monitor (prelude storm filter).
    "UNIT CSBK": "UNIT",
    "UNIT DATA HEADER": "UNIT",
    "UNIT VCSBK 1/2 DATA BLOCK": "UNIT",
    "UNIT VCSBK 3/4 DATA BLOCK": "UNIT",
}


def _dmr_id(value: Any) -> int:
    if isinstance(value, int):
        return value
    return int_id(value)


def _peer_connected(peer: dict[str, Any]) -> bool:
    return peer.get("CONNECTION") == "YES"


def _parse_options_kv(options: Any) -> dict[str, str]:
    """Parse RPTO OPTIONS string into upper-case keys (legacy normalisation)."""
    if options is None:
        return {}
    text = normalize_fixed_width_ascii(options)
    text = text.encode("ascii", "ignore").decode()
    text = re.sub(r"['\"]", "", text).strip()
    if not text:
        return {}
    parsed: dict[str, str] = {}
    for part in text.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        parsed[key.strip().upper()] = value.strip()
    for old, new in (("TS1", "TS1_STATIC"), ("TS2", "TS2_STATIC"), ("TIMER", "DEFAULT_UA_TIMER")):
        if old in parsed and new not in parsed:
            parsed[new] = parsed[old]
    return parsed


_STATIC_TG_TOKEN_RE = re.compile(r"^\d+$")


def static_tg_list(value: Any) -> list[str]:
    """Normalize legacy TS1_STATIC / TS2_STATIC (comma string or list) to string TG ids."""
    if value is None:
        return []
    if isinstance(value, str):
        return [x.strip() for x in value.split(",") if x.strip()]
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    text = str(value).strip()
    return [text] if text else []


def dedupe_static_tg_list(parts: list[str]) -> list[str]:
    """Collapse duplicate TG ids within one TS list (order preserved)."""
    out: list[str] = []
    seen: set[str] = set()
    for tg in parts:
        t = str(tg).strip()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def normalize_static_tg_slot_lists(
    ts1: list[str],
    ts2: list[str],
) -> tuple[list[str], list[str]]:
    """Dedupe within each slot list; same TG may appear on TS1 and TS2 (duplex)."""
    return dedupe_static_tg_list(ts1), dedupe_static_tg_list(ts2)


def _static_slot_csv_valid(raw: str) -> bool:
    """True when every comma-separated token in TS1/TS2 is a numeric talkgroup id."""
    val = raw.strip()
    if not val:
        return True
    if re.search(r"[^\d,]", val):
        return False
    for token in val.split(","):
        token = token.strip()
        if not token:
            continue
        if not _STATIC_TG_TOKEN_RE.match(token):
            return False
    return True


def peer_options_pass_only(options: Any) -> bool:
    """True when OPTIONS is exclusively ``PASS=password`` (self-service RPTO)."""
    parsed = _parse_options_kv(options)
    return list(parsed.keys()) == ["PASS"] and bool(parsed["PASS"].strip())


def peer_options_static_valid(options: Any) -> bool:
    """False when TS1/TS2 static lists contain non-numeric tokens (legacy bridge_master parity).

    ``PASS=`` must be sent alone (self-service); combined PASS+OPTIONS is rejected here.
    """
    parsed = _parse_options_kv(options)
    if not parsed:
        return True
    if "PASS" in parsed:
        return False
    for slot in (1, 2):
        for i in range(1, 10):
            key = f"TS{slot}_{i}"
            if key in parsed and not _STATIC_TG_TOKEN_RE.match(parsed[key].strip()):
                return False
    for key in ("TS1_STATIC", "TS2_STATIC"):
        if key in parsed and not _static_slot_csv_valid(parsed[key]):
            return False
    return True


def parse_peer_options_fields(options: Any) -> dict[str, Any]:
    """Parse OPTIONS into static lists plus optional ``SINGLE`` / ``TIMER`` when present."""
    if not peer_options_static_valid(options):
        return {}
    parsed = _parse_options_kv(options)
    if not parsed:
        return {}
    out: dict[str, Any] = {}
    ts1, ts2 = parse_peer_options_static(options)
    if ts1:
        out["TS1_STATIC"] = ts1
    if ts2:
        out["TS2_STATIC"] = ts2
    if "SINGLE" in parsed:
        out["SINGLE"] = parsed["SINGLE"]
    timer_raw = parsed.get("DEFAULT_UA_TIMER")
    if timer_raw is not None:
        try:
            out["TIMER"] = float(timer_raw)
        except (TypeError, ValueError):
            pass
    return out


def resolve_peer_single_and_timer(
    fields: dict[str, Any],
    sys_cfg: dict[str, Any],
) -> tuple[bool, float]:
    """Use OPTIONS ``SINGLE``/``TIMER`` when present; else YAML ``SINGLE_MODE``/``DEFAULT_UA_TIMER``."""
    from adn_server.domain.config_coerce import coerce_bool, parse_options_single

    if "SINGLE" in fields:
        parsed_single = parse_options_single(fields["SINGLE"])
        single = (
            parsed_single
            if parsed_single is not None
            else coerce_bool(sys_cfg.get("SINGLE_MODE", False))
        )
    else:
        single = coerce_bool(sys_cfg.get("SINGLE_MODE", False))
    if "TIMER" in fields:
        try:
            timer = normalize_ua_timer_minutes(
                float(fields["TIMER"]),
                default_minutes=float(sys_cfg.get("DEFAULT_UA_TIMER", 10)),
            )
        except (TypeError, ValueError):
            timer = normalize_ua_timer_minutes(
                float(sys_cfg.get("DEFAULT_UA_TIMER", 10)),
                default_minutes=10.0,
            )
    else:
        timer = normalize_ua_timer_minutes(
            float(sys_cfg.get("DEFAULT_UA_TIMER", 10)),
            default_minutes=10.0,
        )
    return single, timer


def parse_peer_options_static(options: Any) -> tuple[list[str], list[str]]:
    """Parse hotspot RPTO OPTIONS (``TS1=…;TS2=…;``) into static TG id lists."""
    if not peer_options_static_valid(options):
        return [], []
    parsed = _parse_options_kv(options)
    if not parsed:
        return [], []
    for old, new in (("TS1", "TS1_STATIC"), ("TS2", "TS2_STATIC")):
        if old in parsed and new not in parsed:
            parsed[new] = parsed[old]
    ts1_parts: list[str] = []
    if "TS1_1" in parsed:
        ts1_parts.append(parsed["TS1_1"])
        for i in range(2, 10):
            p = parsed.get(f"TS1_{i}")
            if p:
                ts1_parts.append(p)
    elif parsed.get("TS1_STATIC"):
        ts1_parts = [x.strip() for x in parsed["TS1_STATIC"].split(",") if x.strip()]
    ts2_parts: list[str] = []
    if "TS2_1" in parsed:
        ts2_parts.append(parsed["TS2_1"])
        for i in range(2, 10):
            p = parsed.get(f"TS2_{i}")
            if p:
                ts2_parts.append(p)
    elif parsed.get("TS2_STATIC"):
        ts2_parts = [x.strip() for x in parsed["TS2_STATIC"].split(",") if x.strip()]
    return normalize_static_tg_slot_lists(ts1_parts, ts2_parts)


def _peer_field_json(value: Any) -> str | None:
    """Sanitize a legacy peer field for JSON (no secrets)."""
    if value is None:
        return None
    text = normalize_fixed_width_ascii(value)
    return text or None


# Legacy CONFIG peer keys → topology JSON (display / lnksys classification).
_TOPOLOGY_PEER_FIELDS: tuple[tuple[str, str], ...] = (
    ("CALLSIGN", "callsign"),
    ("RX_FREQ", "rx_freq"),
    ("TX_FREQ", "tx_freq"),
    ("LOCATION", "location"),
    ("DESCRIPTION", "description"),
    ("URL", "url"),
    ("SLOTS", "slots"),
    ("PACKAGE_ID", "package_id"),
    ("SOFTWARE_ID", "software_id"),
    ("COLORCODE", "colorcode"),
    ("TX_POWER", "tx_power"),
)


def _peer_connected_at(peer: dict[str, Any]) -> int | None:
    """Unix time when peer logged in (legacy CONFIG ``CONNECTED``), or None."""
    if not _peer_connected(peer):
        return None
    raw = peer.get("CONNECTED", 0)
    try:
        ts = int(float(raw))
    except (TypeError, ValueError):
        return None
    return ts if ts > 0 else None


def _sanitized_peer_options_text(options: Any) -> str | None:
    """RPTO OPTIONS for monitor display (omit ``PASS=`` secrets)."""
    if options is None:
        return None
    text = normalize_fixed_width_ascii(options)
    if not text:
        return None
    parts: list[str] = []
    for part in text.split(";"):
        piece = part.strip()
        if not piece:
            continue
        if piece.upper().startswith("PASS="):
            continue
        parts.append(piece)
    if not parts:
        return None
    return ";".join(parts) + ";"


def _topology_peer_row(
    peer_key: Any,
    peer: dict[str, Any],
    *,
    sys_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": _dmr_id(peer_key),
        "connected": _peer_connected(peer),
    }
    connected_at = _peer_connected_at(peer)
    if connected_at is not None:
        row["connected_at"] = connected_at
    if peer.get("IP"):
        row["ip"] = str(peer["IP"])
    if peer.get("PORT") is not None:
        row["port"] = int(peer["PORT"])
    for legacy_key, json_key in _TOPOLOGY_PEER_FIELDS:
        if legacy_key not in peer:
            continue
        text = _peer_field_json(peer[legacy_key])
        if text is not None:
            row[json_key] = text
    yaml_cfg = sys_cfg if isinstance(sys_cfg, dict) else {}
    if "OPTIONS" in peer and peer_options_static_valid(peer.get("OPTIONS")):
        opt_text = _sanitized_peer_options_text(peer.get("OPTIONS"))
        if opt_text:
            row["options"] = opt_text
        fields = parse_peer_options_fields(peer.get("OPTIONS"))
        ts1 = fields.get("TS1_STATIC") or []
        ts2 = fields.get("TS2_STATIC") or []
        if ts1:
            row["ts1_static"] = ts1
        if ts2:
            row["ts2_static"] = ts2
        single, timer = resolve_peer_single_and_timer(fields, yaml_cfg)
    else:
        single, timer = resolve_peer_single_and_timer({}, yaml_cfg)
        ts1 = static_tg_list(yaml_cfg.get("TS1_STATIC"))
        ts2 = static_tg_list(yaml_cfg.get("TS2_STATIC"))
        if ts1:
            row["ts1_static"] = ts1
        if ts2:
            row["ts2_static"] = ts2
    row["single_mode"] = single
    if not ua_timer_is_infinite(timer):
        row["ua_timer_min"] = timer
    row["ua_sessions"] = export_peer_ua_sessions(yaml_cfg, peer_key)
    row["ua_multi_tgs"] = export_peer_ua_multi_tgs(yaml_cfg, peer_key)
    row["rf_mode"] = peer_rf_mode(peer)
    return row


def _system_has_connected_users(cfg: dict[str, Any]) -> bool:
    """True when an enabled system has at least one live peer or upstream link."""
    if not cfg.get("ENABLED", True):
        return False
    mode = cfg.get("MODE", "MASTER")
    if mode == "MASTER":
        return any(
            _peer_connected(peer)
            for peer in cfg.get("PEERS", {}).values()
            if isinstance(peer, dict)
        )
    if mode == "OPENBRIDGE":
        return any(
            _peer_connected(peer)
            for peer in cfg.get("PEERS", {}).values()
            if isinstance(peer, dict)
        )
    if mode in ("PEER", "XLXPEER"):
        for key in ("XLXSTATS", "STATS"):
            block = cfg.get(key)
            if isinstance(block, dict) and block.get("CONNECTION") == "YES":
                return True
        return False
    return False


def hello_connected_system_names(systems: dict[str, Any]) -> list[str]:
    """Enabled system names with connected users — optional HELLO hint (topology is authoritative)."""
    return sorted(
        name
        for name, cfg in systems.items()
        if isinstance(cfg, dict) and _system_has_connected_users(cfg)
    )


def build_topology(systems: dict[str, Any], *, seq: int, ts: float | None = None) -> dict[str, Any]:
    """Sanitized topology snapshot (no passphrases or runtime-only keys)."""
    epoch = time.time() if ts is None else ts
    out_systems: list[dict[str, Any]] = []
    for name, cfg in systems.items():
        if not isinstance(cfg, dict):
            continue
        mode = cfg.get("MODE", "MASTER")
        if mode not in _SYSTEM_MODES:
            mode = str(mode)
        entry: dict[str, Any] = {
            "name": name,
            "mode": mode,
            "enabled": bool(cfg.get("ENABLED", True)),
        }
        if cfg.get("IP"):
            entry["ip"] = str(cfg["IP"])
        port = cfg.get("PORT")
        if port is not None:
            entry["port"] = int(port)
        if "REPEAT" in cfg:
            entry["repeat"] = bool(cfg["REPEAT"])
        if cfg.get("ENHANCED_OBP"):
            entry["enhanced_obp"] = True
        if mode == "OPENBRIDGE" and cfg.get("NETWORK_ID") is not None:
            entry["network_id"] = _dmr_id(cfg["NETWORK_ID"])
        if mode == "MASTER":
            ts1 = static_tg_list(cfg.get("TS1_STATIC"))
            ts2 = static_tg_list(cfg.get("TS2_STATIC"))
            if ts1:
                entry["ts1_static"] = ts1
            if ts2:
                entry["ts2_static"] = ts2
        peers_out: list[dict[str, Any]] = []
        for peer_key, peer in cfg.get("PEERS", {}).items():
            if not isinstance(peer, dict):
                continue
            peers_out.append(_topology_peer_row(peer_key, peer, sys_cfg=cfg))
        entry["peers"] = peers_out
        out_systems.append(entry)
    return {"type": "topology", "seq": int(seq), "ts": float(epoch), "systems": out_systems}


def build_routing_table(bridges: dict[str, Any], *, seq: int, ts: float | None = None) -> dict[str, Any]:
    """Routing table snapshot from BRIDGES."""
    epoch = time.time() if ts is None else ts
    routes: list[dict[str, Any]] = []
    for relay_table_key, legs in bridges.items():
        if not isinstance(legs, list):
            continue
        leg_rows: list[dict[str, Any]] = []
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            to_type = leg.get("TO_TYPE", "NONE")
            if to_type not in _TO_TYPES:
                to_type = str(to_type)
            row: dict[str, Any] = {
                "system": str(leg.get("SYSTEM", "")),
                "ts": int(leg.get("TS", 1)),
                "tgid": _dmr_id(leg.get("TGID", 0)),
                "active": bool(leg.get("ACTIVE", False)),
                "to_type": to_type,
            }
            timer = leg.get("TIMER")
            if timer is not None:
                row["timer_expires_at"] = float(timer)
            leg_rows.append(row)
        routes.append({"relay_table_key": str(relay_table_key), "legs": leg_rows})
    return {"type": "routing_table", "seq": int(seq), "ts": float(epoch), "routes": routes}


def parse_bridge_event_csv(event: str, *, ts: float | None = None) -> dict[str, Any] | None:
    """Parse legacy BRDG_EVENT CSV into a ``voice_event`` dict."""
    parts = [p.strip() for p in event.split(",")]
    if len(parts) < 9:
        return None
    call_family = _CSV_FAMILIES.get(parts[0])
    if call_family is None:
        return None
    phase = parts[1]
    direction = parts[2]
    try:
        stream_id = int(parts[4])
        peer_id = int(parts[5])
        src_id = int(parts[6])
        slot = int(parts[7])
        dst_id = int(parts[8])
    except (ValueError, IndexError):
        return None
    if slot not in (1, 2):
        return None
    if direction not in ("RX", "TX"):
        return None
    epoch = time.time() if ts is None else ts
    voice: dict[str, Any] = {
        "type": "voice_event",
        "ts": epoch,
        "call_family": call_family,
        "phase": phase,
        "direction": direction,
        "system": parts[3],
        "stream_id": stream_id,
        "peer_id": peer_id,
        "src_id": src_id,
        "slot": slot,
        "dst_id": dst_id,
    }
    if phase == "END" and len(parts) > 9:
        try:
            voice["duration_s"] = float(parts[9])
        except ValueError:
            voice["duration_s"] = None
    elif phase != "END":
        voice["duration_s"] = None
    if call_family == "PRIVATE" and direction == "TX":
        # routing_use_cases._pvt_call_received appends the destination hotspot's own
        # peer id (from SUB_MAP) after the legacy fields -- there's no static TG a
        # private call's subscriber-id destination could ever match, so this is the
        # only way the monitor can know which single hotspot is receiving.
        voice["is_announcement"] = False
        base_len = 10 if phase == "END" else 9
        if len(parts) > base_len:
            try:
                voice["dest_peer_id"] = int(parts[-1])
            except ValueError:
                pass
    else:
        voice["is_announcement"] = len(parts) > 9 and parts[-1] == "1"
    return voice


def routing_table_delta(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    *,
    seq: int,
    ts: float | None = None,
) -> dict[str, Any] | None:
    """Build a delta message when only some routes changed; ``None`` if unchanged."""
    if previous is None:
        return None
    prev_routes = {r["relay_table_key"]: r for r in previous.get("routes", [])}
    changed: list[dict[str, Any]] = []
    for route in current.get("routes", []):
        key = route["relay_table_key"]
        if prev_routes.get(key) != route:
            changed.append(route)
    if not changed:
        return None
    since_seq = int(previous.get("seq", 0))
    epoch = time.time() if ts is None else ts
    patch = {
        "type": "routing_table",
        "seq": int(seq),
        "ts": float(epoch),
        "routes": changed,
    }
    return {
        "type": "delta",
        "seq": int(seq),
        "ts": float(epoch),
        "since_seq": since_seq,
        "patch": patch,
    }


def topology_delta(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    *,
    seq: int,
    ts: float | None = None,
) -> dict[str, Any] | None:
    """Build a delta when only some systems changed; ``None`` if unchanged."""
    if previous is None:
        return None
    prev_systems = {s["name"]: s for s in previous.get("systems", [])}
    changed: list[dict[str, Any]] = []
    for system in current.get("systems", []):
        name = system["name"]
        if prev_systems.get(name) != system:
            changed.append(system)
    if not changed:
        return None
    since_seq = int(previous.get("seq", 0))
    epoch = time.time() if ts is None else ts
    patch = {
        "type": "topology",
        "seq": int(seq),
        "ts": float(epoch),
        "systems": changed,
    }
    return {
        "type": "delta",
        "seq": int(seq),
        "ts": float(epoch),
        "since_seq": since_seq,
        "patch": patch,
    }
