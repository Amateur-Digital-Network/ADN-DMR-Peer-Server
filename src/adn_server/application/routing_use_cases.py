# ADN DMR Peer Server - routing use cases
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

"""Routing facade: dmrd_received and unit/private paths.

Helpers live in application/routing/ (timers, OBP/HBP forward, LC/TA, subscription table).
"""

from __future__ import annotations

import logging
import time
from time import perf_counter
from typing import Any

from bitarray import bitarray

from ..domain import (
    HBPF_DATA_SYNC,
    HBPF_SLT_VHEAD,
    HBPF_SLT_VTERM,
    HBPF_VOICE,
    HBPF_VOICE_SYNC,
    STREAM_TO,
    bytes_3,
    bytes_4,
    int_id,
)
from ..domain.dmr import bptc
from ..domain.mesh_session import ObpBridgeSession, obp_session
from .ports import AclRouter, DmrEmbeddedLcEncoder, SubscriptionStore, TalkerAliasEmblcEncoder
from .reporting_use_cases import ReportingUseCases
from .routing.hbp_forward import HbpForwardMixin
from .routing.helpers import (
    hbp_slot_blocks_group_voice,
    inject_only_defer_obp_hbp_slot_contention,
    is_private_subscriber_dst,
    is_unit_data_ingress,
    master_slot_holds_server_broadcast,
    obp_clear_deferred_bridge_tx_leg,
    obp_deferred_bridge_tx_leg,
    obp_flat_bridge_tx_idle,
    obp_publish_flat_bridge_tx,
    obp_status_plugin_voice,
    obp_sync_flat_bridge_tx_times,
    resolve_voice_peer_id,
    slot_has_active_voice,
    unit_data_hbp_target_idle,
    unit_data_reportable,
)
from .routing.lc_ta import LcTaMixin
from .routing.obp_forward import ObpForwardMixin
from .routing.peer_downlink_index import count_connected_peers
from .routing.store_authority_mixin import StoreAuthorityMixin
from .routing.subscription_table import SubscriptionTableMixin
from .routing.timers import RoutingTimerMixin
from .routing.voice_subscription import VoiceSubscriptionMixin
from .server_voice import all_server_voice_ids
from .subscription.subscription_queries import store_has_table
from .talker_alias_use_cases import TalkerAliasUseCases

logger = logging.getLogger(__name__)

# A SUB_MAP entry younger than this (seconds) on a non-OPENBRIDGE system means the unit is
# served by this server, so unit data to it is not fanned out to OpenBridges.
UNIT_DATA_LOCAL_SUB_MAX_AGE = 900.0


class RoutingUseCases(
    RoutingTimerMixin,
    ObpForwardMixin,
    HbpForwardMixin,
    LcTaMixin,
    SubscriptionTableMixin,
    StoreAuthorityMixin,
    VoiceSubscriptionMixin,
):
    """Use cases for subscription-based voice routing."""

    def _obp_session(self, system_name: str) -> ObpBridgeSession:
        """Live state of an OPENBRIDGE leg (peer, keepalive, quench)."""
        return obp_session(self._config, system_name)

    def __init__(
        self,
        acl_router: AclRouter,
        config: dict[str, Any],
        subscription_store: SubscriptionStore,
        send_to_system: Any = None,
        get_protocols: Any = None,
        reporting: ReportingUseCases | None = None,
        on_relay_deactivated: Any = None,
        send_bcsq: Any = None,
        send_dmra_to_system: Any = None,
        get_dmra_blocks: Any = None,
        call_later: Any = None,
        encode_emblc: DmrEmbeddedLcEncoder | None = None,
        ta_emblc_encoder: TalkerAliasEmblcEncoder | None = None,
        voice_plugin_bridge: Any = None,
        data_plugin_bridge: Any = None,
    ) -> None:
        self._acl_router = acl_router
        self._config = config
        self._subscription_store = subscription_store
        self._subscription_router = None
        self._routing_table_legacy_view = None
        self._send_to_system = send_to_system  # (system_name, packet, **kwargs) -> None
        self._get_protocols = get_protocols  # () -> dict[str, protocol]
        self._reporting = reporting
        self._on_relay_deactivated = on_relay_deactivated  # (system_name: str) -> None; legacy disconnectedVoice
        self._send_bcsq = send_bcsq  # (system_name, tgid, stream_id) -> None; legacy OBP send_bcsq from router
        self._send_dmra_to_system = send_dmra_to_system
        self._get_dmra_blocks = get_dmra_blocks
        self._call_later = call_later
        if encode_emblc is None or ta_emblc_encoder is None:
            raise TypeError("encode_emblc and ta_emblc_encoder are required (wire from main.py)")
        self._encode_emblc = encode_emblc
        self._talker_alias = TalkerAliasUseCases(config, ta_emblc_encoder=ta_emblc_encoder)
        self._voice_plugin_bridge = voice_plugin_bridge
        self._data_plugin_bridge = data_plugin_bridge
        # (source_system, stream_id) -> {rf_src, peer, targets, timer}
        self._both_ta_wait: dict[tuple[str, bytes], dict[str, Any]] = {}
        # Passthrough DMRA/embed relay already applied for this source stream.
        self._passthrough_relayed: set[tuple[str, bytes]] = set()

    def _send_routing_event(self, event: str | bytes) -> bool:
        """Send BRDG_EVENT via ReportingUseCases when REPORT is enabled."""
        if not self._config.get("REPORTS", {}).get("REPORT", True):
            return False
        if not self._reporting:
            return False
        if isinstance(event, bytes):
            event = event.decode("utf-8", "ignore")
        try:
            self._reporting.send_routing_event(event)
            return True
        except Exception:
            return False

    def _send_routing_table_snapshot(self, *, incremental: bool = True) -> None:
        """Push BRIDGES / routing_table to report clients (monitor SINGLE_TS chips)."""
        if not self._config.get("REPORTS", {}).get("REPORT", True):
            return
        if not self._reporting:
            return
        try:
            self._reporting.send_routing_table(self._routing_table_for_report(), incremental=incremental)
        except Exception as e:
            logger.warning("(ROUTER) send_routing_table failed: %s", e)

    def routing_table_for_report(self) -> dict[str, list[dict[str, Any]]]:
        """Return BRIDGES export for monitor/report only (routing uses ``SubscriptionStore``)."""
        return self._routing_table_for_report()

    def acl_check(self, id_val: bytes | int, acl: tuple[bool, list[tuple[int, int]]]) -> bool:
        """Check ID against ACL. Legacy acl_check."""
        return self._acl_router.acl_check(id_val, acl)


    def dmrd_received(
        self,
        system_name: str,
        peer_id: bytes,
        rf_src: bytes,
        dst_id: bytes,
        seq: int,
        slot: int,
        call_type: str,
        frame_type: int,
        dtype_vseq: int,
        stream_id: bytes,
        data: bytes,
        *,
        ingress_pkt_time: float | None = None,
        obp_use_parsed: bool = False,
        obp_hops: bytes = b"",
        obp_source_server: bytes | None = None,
        obp_ber: bytes = b"\x00",
        obp_rssi: bytes = b"\x00",
        obp_source_rptr: bytes = b"\x00\x00\x00\x00",
        synthetic_announcement: bool = False,
    ) -> bool:
        """Called by UDP when DMRD is received. Forward to other systems in same bridge (to_target).

        Returns True if the packet was accepted (passed ingress controls), False/None if dropped.
        Legacy `hblink.dmrd_received` passes `_hash,_hops,_source_server,_ber,_rssi,_source_rptr` after
        parsing OPENBRIDGE DMRD v1 / DMRE (`hblink.py` ~309–416, ~592–596). When `obp_use_parsed` is True,
        the OBP path uses those values (1:1 with `bridge.py` `routerOBP.dmrd_received` → `send_system`).
        HBP sources should pass ``ingress_pkt_time`` from UDP receive (legacy single ``pkt_time`` at router entry).

        ``synthetic_announcement=True`` marks a frame injected by the announcement/TTS
        PTT path (`routing/announcement_ptt_inject.py`) rather than a real peer
        connection. Its ``peer_id`` is already the global SERVER_ID (never a real
        peer — see `_announcement_packet_peer`), so `resolve_voice_peer_id`'s rf_src/
        fuzzy fallback must be skipped: matching the announcement's spoofed rf_src
        against a real connected peer's ID would misattribute the call (TX state,
        dynamic TG) to that peer.
        """
        if not self._send_to_system:
            return
        # Legacy routerHBP.dmrd_received ~3015-3020: log once on first packet after _reset.
        # Only applies to HBP sources (routerOBP has no _reset gate).
        sys_cfg = self._config.get("SYSTEMS", {}).get(system_name, {})
        if sys_cfg.get("MODE") != "OPENBRIDGE" and sys_cfg.get("_reset") and not sys_cfg.get("_resetlog"):
            logger.info("(%s) disallow transmission until reset cycle is complete", system_name)
            sys_cfg["_resetlog"] = True
            return
        # Legacy bridge_master 3080–3085: private call to ID 4000 only disconnects dynamics; do not route as PC.
        if call_type == "unit" and int_id(dst_id) == 4000:
            return
        if call_type == "unit":
            _int_dst = int_id(dst_id)
            if dtype_vseq in (6, 7, 8) or (dtype_vseq == 3 and not self._is_stream_known(system_name, stream_id, slot)):
                self._unit_data_received(
                    system_name, peer_id, rf_src, dst_id, seq, slot,
                    frame_type, dtype_vseq, stream_id, data,
                    obp_use_parsed=obp_use_parsed, obp_hops=obp_hops,
                    obp_source_server=obp_source_server,
                    obp_ber=obp_ber, obp_rssi=obp_rssi, obp_source_rptr=obp_source_rptr,
                )
            # Legacy routerHBP ~3252-3254: 7-digit routing runs after unit data, not
            # instead of it. D-APRS ARS/LRRP downlink (dst 7300392) uses pvt_call_received
            # when SUB_MAP sendDataToHBP is blocked by the strict idle check.
            if len(str(_int_dst)) == 7:
                self._pvt_call_received(
                    system_name, peer_id, rf_src, dst_id, seq, slot,
                    frame_type, dtype_vseq, stream_id, data,
                )
            return True
        systems_cfg = self._config.get("SYSTEMS", {})
        source_is_obp = systems_cfg.get(system_name, {}).get("MODE") == "OPENBRIDGE"
        pkt_time = ingress_pkt_time if ingress_pkt_time is not None else time.time()
        if not source_is_obp and call_type in ("group", "vcsbk"):
            if not self._hbp_group_voice_ingress_controls(
                system_name, peer_id, rf_src, dst_id, seq, slot, stream_id, data, pkt_time,
            ):
                return
            # Arm ON in-band rules on VHEAD (echo 9990 and UA bridges); VTERM handled in udp_hbp too.
            if frame_type == HBPF_DATA_SYNC and dtype_vseq == HBPF_SLT_VHEAD:
                self.apply_in_band_signalling(system_name, slot, dst_id, pkt_time)
        relay_table_key = str(int_id(dst_id))
        dst_int = int_id(dst_id)
        # Legacy bridge_master to_target: OpenBridge clears TS bit — "all OpenBridge streams are
        # effectively on TS1". DMRD v1 rejects slot != 1; DMRE v5 can still set slot 2 from bits.
        # BRIDGES entries for OBP use TS:1 (ensure_dynamic_relay / ensure_stat_relay). Match that.
        bridge_match_slot = 1 if source_is_obp else slot
        if not store_has_table(self._subscription_store, relay_table_key):
            if dst_int < 5 or dst_int == 9 or dst_int == 4000 or dst_int == 5000:
                logger.debug(
                    "(ROUTER) No bridge for TG %s (excluded TGID), not creating",
                    dst_int,
                )
            elif source_is_obp and self._config.get("GLOBAL", {}).get("GEN_STAT_BRIDGES"):
                logger.debug("(%s) Bridge for STAT TG %s does not exist. Creating", system_name, dst_int)
                self.ensure_stat_relay(dst_id)
                self.apply_static_tg_to_bridge(dst_int)
            else:
                tmout = self._ua_timer_minutes_for_peer(system_name, peer_id)
                logger.info(
                    "(%s) Bridge for TG %s does not exist. Creating as User Activated. Timeout %s",
                    system_name, dst_int, tmout,
                )
                self.ensure_dynamic_relay(dst_id, system_name, slot, float(tmout))
                self.apply_static_tg_to_bridge(dst_int)
                self._send_routing_table_snapshot(incremental=True)
        # Legacy bridge_master routerOBP ~2413-2418: scan every BRIDGES[_bridge] table; forward only within
        # a table that contains a matching ACTIVE source row (not a flat merge of TG + #TG only).
        dst_id_b = dst_id if isinstance(dst_id, bytes) and len(dst_id) >= 3 else bytes_3(dst_int)

        if source_is_obp:
            self._ensure_obp_source_for_tg(system_name, relay_table_key, dst_id_b, dst_int)
        if source_is_obp and call_type in ("group", "vcsbk"):
            if not self._obp_group_voice_router_obp(
                system_name,
                peer_id,
                rf_src,
                dst_id,
                seq,
                slot,
                call_type,
                frame_type,
                dtype_vseq,
                stream_id,
                data,
                obp_hops if obp_use_parsed else b"",
                synthetic_announcement=synthetic_announcement,
            ):
                return
            # SINGLE_MODE in-band VTERM on another TG (e.g. 9990 echo) deactivates static OFF
            # legs; re-arm OPTIONS static destinations before forward (inject-only merged lists).
            if frame_type == HBPF_DATA_SYNC and dtype_vseq == HBPF_SLT_VHEAD:
                self.apply_static_tg_to_bridge(dst_int)
        # Legacy bridge_master routerHBP: BRDG_EVENT on VHEAD/VTERM before bridge source scan.
        _obp_grp = source_is_obp and call_type in ("group", "vcsbk")
        if frame_type == HBPF_DATA_SYNC and dtype_vseq == HBPF_SLT_VHEAD:
            if not _obp_grp:
                _is_new_rx_stream = True
                _suppress_uplink_rx = False
                if not source_is_obp:
                    protocols = self._get_protocols() if self._get_protocols else {}
                    src_proto = protocols.get(system_name) if protocols else None
                    if src_proto and getattr(src_proto, "STATUS", None):
                        slot_st = src_proto.STATUS.get(slot, {})
                        if isinstance(slot_st, dict):
                            _is_new_rx_stream = stream_id != slot_st.get("RX_STREAM_ID")
                            _suppress_uplink_rx = bool(slot_st.get("_suppress_uplink"))
                if _is_new_rx_stream and not _suppress_uplink_rx:
                    _rx_report_peer = peer_id
                    if not source_is_obp and not synthetic_announcement:
                        _rx_report_peer = resolve_voice_peer_id(
                            peer_id,
                            rf_src,
                            system_name,
                            systems_cfg,
                        )
                    self._send_routing_event(
                        "GROUP VOICE,START,RX,{},{},{},{},{},{},{}".format(
                            system_name,
                            int_id(stream_id),
                            int_id(_rx_report_peer),
                            int_id(rf_src),
                            slot,
                            int_id(dst_id),
                            int(synthetic_announcement),
                        )
                    )
                    if self._voice_plugin_bridge is not None:
                        self._voice_plugin_bridge.emit_group_voice_start(
                            system_name=system_name,
                            peer_id=peer_id,
                            rf_src=rf_src,
                            dst_id=dst_id,
                            slot=slot,
                            stream_id=stream_id,
                            pkt_time=pkt_time,
                            source_is_obp=False,
                            synthetic_announcement=synthetic_announcement,
                        )
        elif frame_type == HBPF_DATA_SYNC and dtype_vseq == HBPF_SLT_VTERM:
            if not _obp_grp:
                duration = 0.0
                _suppress_uplink_vterm = False
                protocols = self._get_protocols() if self._get_protocols else {}
                src_proto = protocols.get(system_name) if protocols else None
                if src_proto and getattr(src_proto, "STATUS", None):
                    st = src_proto.STATUS
                    ent = st.get(stream_id)
                    start = ent.get("START") if isinstance(ent, dict) else None
                    if start is None and slot in st:
                        start = st.get(slot, {}).get("RX_START")
                    if start is not None:
                        duration = pkt_time - start
                    slot_st_vterm = st.get(slot, {})
                    if isinstance(slot_st_vterm, dict):
                        _suppress_uplink_vterm = bool(slot_st_vterm.get("_suppress_uplink"))
                if _suppress_uplink_vterm:
                    self._clear_silent_activation_log(system_name, peer_id, dst_id)
                if not _suppress_uplink_vterm:
                    _rx_report_peer = peer_id
                    if not source_is_obp and not synthetic_announcement:
                        _rx_report_peer = resolve_voice_peer_id(
                            peer_id,
                            rf_src,
                            system_name,
                            systems_cfg,
                        )
                    self._send_routing_event(
                        "GROUP VOICE,END,RX,{},{},{},{},{},{},{:.2f},{}".format(
                            system_name,
                            int_id(stream_id),
                            int_id(_rx_report_peer),
                            int_id(rf_src),
                            slot,
                            int_id(dst_id),
                            duration,
                            int(synthetic_announcement),
                        )
                    )
                    if self._voice_plugin_bridge is not None:
                        self._voice_plugin_bridge.emit_group_voice_end(
                            system_name=system_name,
                            peer_id=peer_id,
                            rf_src=rf_src,
                            dst_id=dst_id,
                            slot=slot,
                            stream_id=stream_id,
                            pkt_time=pkt_time,
                            duration_s=duration,
                            source_is_obp=False,
                            synthetic_announcement=synthetic_announcement,
                        )
        has_source = bool(
            self._voice_relay_tables_with_active_source(system_name, bridge_match_slot, dst_int)
        )
        if not has_source and systems_cfg.get(system_name, {}).get("MODE") == "MASTER":
            self.options_config_for_system(system_name)
            has_source = bool(
                self._voice_relay_tables_with_active_source(system_name, bridge_match_slot, dst_int)
            )
        # Do not call ensure_dynamic_relay here for 9990–9999 when BRIDGES["9990"] already exists:
        # ensure_dynamic_relay replaces the whole table and only sets the source MASTER ACTIVE; every
        # other system (including ECHO with TO_TYPE NONE) becomes ACTIVE False — to_target then has
        # no active target for the echo path (legacy _seed_echo_routing_table / make_bridges keeps ECHO
        # ACTIVE True; activation of the source row is via in-band ON on VTERM, bridge_master ~3465).
        if not has_source:
            logger.debug(
                "(ROUTER) No matching source rule for TG %s from %s slot %s (ACTIVE), not forwarding",
                relay_table_key, system_name, bridge_match_slot,
            )
            return True

        # ── Exact port of legacy bridge.py routerOBP/routerHBP forwarding to targets ──
        pkt_time = time.time()
        dmrpkt = data[20:53] if len(data) >= 53 else b""
        _bits = data[15] if len(data) > 15 else 0
        protocols = self._get_protocols() if self._get_protocols else {}
        src_proto = protocols.get(system_name) if protocols else None
        # Legacy `bridge.py` `routerOBP.dmrd_received` forwards `_hops,_ber,_rssi,_source_server,_source_rptr`
        # exactly as received from `hblink` (`bridge.py` ~486). Values are set in `hblink`:
        # - DMRD v1 OBP: SERVER_ID, zeros rptr, empty hops (`hblink.py` ~338–345)
        # - DMRE v5: packet fields + incremented hops (`hblink.py` ~592–596)
        # Legacy `bridge_master` `routerHBP.dmrd_received`: ber/rssi from payload, SERVER_ID + peer as rptr (~2936–2940).
        if source_is_obp and obp_use_parsed:
            _hops = obp_hops
            _ber = obp_ber
            _rssi = obp_rssi
            _sid = self._config.get("GLOBAL", {}).get("SERVER_ID")
            if obp_source_server is not None:
                _source_server = obp_source_server
            elif isinstance(_sid, bytes) and len(_sid) >= 4:
                _source_server = _sid
            elif _sid is not None:
                _source_server = bytes_4(int(_sid) & 0xFFFFFFFF)
            else:
                _source_server = b"\x00\x00\x00\x00"
            _source_rptr = obp_source_rptr
        elif source_is_obp:
            _ber = b"\x00"
            _rssi = b"\x00"
            _hops = b""
            _sid = self._config.get("GLOBAL", {}).get("SERVER_ID")
            if isinstance(_sid, bytes) and len(_sid) >= 4:
                _source_server = _sid
            elif _sid is not None:
                _source_server = bytes_4(int(_sid) & 0xFFFFFFFF)
            else:
                _source_server = b"\x00\x00\x00\x00"
            _source_rptr = b"\x00\x00\x00\x00"
        else:
            _ber = data[53:54] if len(data) > 53 else b"\x00"
            _rssi = data[54:55] if len(data) > 54 else b"\x00"
            _hops = b""
            _sid = self._config.get("GLOBAL", {}).get("SERVER_ID")
            if isinstance(_sid, bytes) and len(_sid) >= 4:
                _source_server = _sid
            elif _sid is not None:
                _source_server = bytes_4(int(_sid) & 0xFFFFFFFF)
            else:
                _source_server = b"\x00\x00\x00\x00"
            _source_rptr = peer_id
        # Legacy: source LC — routerOBP uses self.STATUS[_stream_id]['LC'], routerHBP uses self.STATUS[_slot]['RX_LC']
        source_lc = None
        if src_proto and getattr(src_proto, "STATUS", None):
            st = src_proto.STATUS
            if source_is_obp:
                ent = st.get(stream_id)
                source_lc = ent.get("LC") if isinstance(ent, dict) else None
            else:
                source_lc = st.get(slot, {}).get("RX_LC")
        if not source_lc or len(source_lc) < 9:
            source_lc = b"\x00\x00\x20" + dst_id_b + rf_src
        # Legacy bridge_master routerOBP: _sysIgnore accumulates across each to_target(BRIDGES[_bridge])
        # pass; dedupe (SYSTEM, TS) for OpenBridge targets so the same leg is not sent twice per packet.
        # SubscriptionRouter.resolve() already applies OBP dedup on OpenBridge targets, and the plan
        # collapses same-(target, translated TGID) MASTER/PEER legs (see _group_voice_forward_plan).
        _plan = self._group_voice_forward_plan(
            system_name=system_name,
            slot=slot,
            call_type=call_type,
            source_is_obp=source_is_obp,
            bridge_match_slot=bridge_match_slot,
            dst_int=dst_int,
        )
        _tx_report_peer = int_id(peer_id)
        if not source_is_obp and not synthetic_announcement:
            _tx_report_peer = int_id(
                resolve_voice_peer_id(peer_id, rf_src, system_name, systems_cfg)
            )
        forwarded = []
        # When a TX arrived on a TG with an active QSO, the ingress gate set
        # ``_suppress_uplink`` on the source slot status. The TG was activated dynamically
        # (so downlink of the active QSO reaches this peer), but this stream's audio must
        # not be forwarded upstream to avoid disrupting the in-progress conversation.
        _suppress_uplink = False
        if not source_is_obp and src_proto and getattr(src_proto, "STATUS", None):
            _src_slot_st = src_proto.STATUS.get(slot, {})
            if isinstance(_src_slot_st, dict) and _src_slot_st.get("_suppress_uplink"):
                _suppress_uplink = True
        _leg_iter = _plan.entries

        for _relay_table_key, entry in _leg_iter:
                if _suppress_uplink:
                    continue
                if not systems_cfg.get(entry["SYSTEM"], {}).get("ENABLED", True):
                    continue
                _target_system = systems_cfg.get(entry["SYSTEM"], {})
                target_mode = _target_system.get("MODE")
                tgt_proto = protocols.get(entry["SYSTEM"]) if protocols else None
                _target_status = getattr(tgt_proto, "STATUS", None) if tgt_proto else None

                if target_mode == "OPENBRIDGE":
                    # ── bridge_master.routerOBP.to_target OPENBRIDGE branch (~1850-1959) ──
                    entry_ts_obp = entry.get("TS")
                    if entry_ts_obp is None:
                        entry_ts_obp = 1
                    target_tgid = entry.get("TGID")
                    if isinstance(target_tgid, int):
                        target_tgid = bytes_3(target_tgid)
                    # If target has quenched us, don't send (~1856-1859).
                    _target_session = self._obp_session(entry["SYSTEM"])
                    if _target_session.quenches(dst_id_b, stream_id):
                        continue
                    # If target has missed keepalives (ENHANCED_OBP), don't send (~1861-1863)
                    if _target_system.get("ENHANCED_OBP") and not _target_session.keepalive_ok(pkt_time):
                        continue
                    # Talkgroup ACL (global + per-system TG1) (~1865-1873)
                    _global_cfg = self._config.get("GLOBAL", {})
                    if _global_cfg.get("USE_ACL"):
                        if not self.acl_check(target_tgid, _global_cfg.get("TG1_ACL", (True, []))):
                            continue
                        if not self.acl_check(target_tgid, _target_system.get("TG1_ACL", (True, []))):
                            continue
                    if _target_status is not None:
                        _prev_obp_st = _target_status.get(stream_id)
                        # Legacy routerOBP: only replace a finished stream; ingress rows keep 1ST/loop fields.
                        if isinstance(_prev_obp_st, dict) and _prev_obp_st.get("_fin"):
                            del _target_status[stream_id]
                            self.clear_talker_alias_stream(system_name, stream_id)
                            _prev_obp_st = None
                        # Reused stream_id on a forward leg: drop stale row before START,TX.
                        if isinstance(_prev_obp_st, dict) and "H_LC" in _prev_obp_st:
                            _stale_forward = (
                                _prev_obp_st.get("_end_tx_sent")
                                or _prev_obp_st.get("RX_PEER") != peer_id
                                or _prev_obp_st.get("RFS") != rf_src
                                or _prev_obp_st.get("TGID") != dst_id_b
                            )
                            if _stale_forward:
                                del _target_status[stream_id]
                        if stream_id not in _target_status:
                            _target_status[stream_id] = {
                                "START": pkt_time,
                                "CONTENTION": False,
                                "RFS": rf_src,
                                "TGID": dst_id_b,
                                "RX_PEER": peer_id,
                                "EMB_LC": {1: b"\x00", 2: b"\x00", 3: b"\x00", 4: b"\x00"},
                                "H_LC": b"\x00",
                                "T_LC": b"\x00",
                            }
                            try:
                                dst_lc = source_lc[0:3] + target_tgid + rf_src
                            except Exception:
                                logger.exception("(to_target) caught exception")
                                _target_status[stream_id]["LAST"] = pkt_time
                                return
                            _target_status[stream_id]["H_LC"] = bptc.encode_header_lc(dst_lc)
                            _target_status[stream_id]["T_LC"] = bptc.encode_terminator_lc(dst_lc)
                            _target_status[stream_id]["EMB_LC"] = self._encode_emblc(dst_lc)
                            self._init_talker_alias_embed(
                                _target_status[stream_id],
                                system_name,
                                entry["SYSTEM"],
                                rf_src,
                                stream_id,
                            )
                            logger.debug(
                                "(%s) Conference Bridge: %s, Call Bridged to OBP System: %s TS: %s, TGID: %s",
                                system_name, _relay_table_key, entry["SYSTEM"], entry.get("TS", 1), int_id(target_tgid),
                            )
                            self._send_routing_event(
                                "GROUP VOICE,START,TX,{},{},{},{},{},{},{}".format(
                                    entry["SYSTEM"], int_id(stream_id), _tx_report_peer, int_id(rf_src), entry.get("TS", 1), int_id(target_tgid), int(synthetic_announcement)
                                )
                            )
                        if "EMB_LC" not in _target_status[stream_id]:
                            try:
                                dst_lc = source_lc[0:3] + target_tgid + rf_src
                                _target_status[stream_id]["EMB_LC"] = self._encode_emblc(dst_lc)
                            except Exception:
                                logger.exception("(to_target) caught exception while creating EMB_LC")
                                return
                        if "H_LC" not in _target_status[stream_id]:
                            try:
                                dst_lc = source_lc[0:3] + target_tgid + rf_src
                                _target_status[stream_id]["H_LC"] = bptc.encode_header_lc(dst_lc)
                            except Exception:
                                logger.exception("(to_target) caught exception while creating H_LC")
                                return
                        if "T_LC" not in _target_status[stream_id]:
                            try:
                                dst_lc = source_lc[0:3] + target_tgid + rf_src
                                _target_status[stream_id]["T_LC"] = bptc.encode_terminator_lc(dst_lc)
                            except Exception:
                                logger.exception("(to_target) caught exception while creating T_LC")
                                return
                        _target_status[stream_id]["LAST"] = pkt_time
                        _tmp_bits = _bits & ~(1 << 7)
                        _tmp_data = b"".join([data[:8], target_tgid, data[11:15], bytes([_tmp_bits]), data[16:20]])
                        dmrbits = bitarray(endian="big")
                        dmrbits.frombytes(dmrpkt)
                        if frame_type == HBPF_DATA_SYNC and dtype_vseq == HBPF_SLT_VHEAD:
                            dmrbits = _target_status[stream_id]["H_LC"][0:98] + dmrbits[98:166] + _target_status[stream_id]["H_LC"][98:197]
                        elif frame_type == HBPF_DATA_SYNC and dtype_vseq == HBPF_SLT_VTERM:
                            dmrbits = _target_status[stream_id]["T_LC"][0:98] + dmrbits[98:166] + _target_status[stream_id]["T_LC"][98:197]
                            self._clear_talker_alias_embed(_target_status[stream_id])
                            call_duration = pkt_time - _target_status[stream_id].get("START", pkt_time)
                            self._send_routing_event(
                                "GROUP VOICE,END,TX,{},{},{},{},{},{},{:.2f},{}".format(
                                    entry["SYSTEM"], int_id(stream_id), _tx_report_peer, int_id(rf_src), entry.get("TS", 1), int_id(target_tgid), call_duration, int(synthetic_announcement)
                                )
                            )
                        elif dtype_vseq in (1, 2, 3, 4):
                            self._rewrite_embed_lc(
                                dmrbits, _target_status[stream_id], dtype_vseq, "EMB_LC",
                            )
                        dmrpkt_out = dmrbits.tobytes()
                        _tmp_data = b"".join([_tmp_data, dmrpkt_out])
                    else:
                        _tmp_bits = _bits & ~(1 << 7)
                        _tmp_data = b"".join([data[:8], target_tgid, data[11:15], bytes([_tmp_bits]), data[16:20]])
                        _tmp_data = b"".join([_tmp_data, dmrpkt])
                    try:
                        self._send_to_system(
                            entry["SYSTEM"], _tmp_data,
                            _hops=_hops, _ber=_ber, _rssi=_rssi,
                            _source_server=_source_server, _source_rptr=_source_rptr,
                        )
                        forwarded.append(entry["SYSTEM"])
                    except Exception as e:
                        logger.warning("(ROUTER) send_to_system %s failed: %s", entry.get("SYSTEM"), e)

                else:
                    # ── Exact port of legacy bridge.py HBP target (lines 403-486 / 720-803) ──
                    entry_ts = entry.get("TS")
                    if entry_ts is None:
                        continue
                    entry_tgid_b = entry.get("TGID")
                    if isinstance(entry_tgid_b, int):
                        entry_tgid_b = bytes_3(entry_tgid_b)
                    if _target_status is None or entry_ts not in _target_status:
                        continue
                    _ts_st = _target_status[entry_ts]
                    if source_is_obp:
                        _src_stream_st = getattr(src_proto, "STATUS", {}).get(stream_id, {}) if src_proto else {}
                    else:
                        _src_stream_st = getattr(src_proto, "STATUS", {}).get(slot, {}) if src_proto else {}
                    # Slot contention: active QSO blocks any other stream; post-VTERM uses GROUP_HANGTIME.
                    _group_hangtime = float(_target_system.get("GROUP_HANGTIME", 0) or 0)
                    _tgt_peers = getattr(tgt_proto, "_peers", None) if tgt_proto else None
                    if _tgt_peers is None:
                        _tgt_peers = _target_system.get("PEERS", {})
                    _target_connected = (
                        count_connected_peers(_tgt_peers) if isinstance(_tgt_peers, dict) else 0
                    )
                    _defer_slot_contention = inject_only_defer_obp_hbp_slot_contention(
                        self._config,
                        entry["SYSTEM"],
                        _target_system,
                        source_is_obp=source_is_obp,
                        source_is_hbp=not source_is_obp,
                        connected_count=_target_connected,
                    )
                    _obp_deferred_bridge = _defer_slot_contention and source_is_obp
                    _bridge_tx_leg: dict[str, Any] | None = None
                    if _obp_deferred_bridge:
                        _bridge_tx_leg = obp_deferred_bridge_tx_leg(_ts_st, stream_id)
                        _lc_row = _bridge_tx_leg
                        _closing_bridge_leg = (
                            frame_type == HBPF_DATA_SYNC
                            and dtype_vseq == HBPF_SLT_VTERM
                            and _bridge_tx_leg.get("TX_TYPE") is not None
                            and _bridge_tx_leg.get("TX_TYPE") != HBPF_SLT_VTERM
                        )
                    else:
                        _lc_row = _ts_st
                        _closing_bridge_leg = (
                            frame_type == HBPF_DATA_SYNC
                            and dtype_vseq == HBPF_SLT_VTERM
                            and _ts_st.get("TX_STREAM_ID") == stream_id
                            and _ts_st.get("TX_TYPE") != HBPF_SLT_VTERM
                        )
                    if _closing_bridge_leg:
                        if _obp_deferred_bridge and _bridge_tx_leg is not None:
                            call_duration = pkt_time - _bridge_tx_leg.get("TX_START", pkt_time)
                            _end_peer = _bridge_tx_leg.get("TX_PEER", peer_id)
                        else:
                            call_duration = pkt_time - _ts_st.get("TX_START", pkt_time)
                            _end_peer = _ts_st.get("TX_PEER", peer_id)
                        _end_report_peer = int_id(_end_peer)
                        if not source_is_obp and not synthetic_announcement:
                            _end_report_peer = int_id(
                                resolve_voice_peer_id(
                                    _end_peer, rf_src, system_name, systems_cfg
                                )
                            )
                        self._send_routing_event(
                            "GROUP VOICE,END,TX,{},{},{},{},{},{},{:.2f},{}".format(
                                entry["SYSTEM"],
                                int_id(stream_id),
                                _end_report_peer,
                                int_id(rf_src),
                                entry_ts,
                                int_id(entry_tgid_b),
                                call_duration,
                                int(synthetic_announcement),
                            )
                        )
                        if not _obp_deferred_bridge:
                            _ts_st["TX_TYPE"] = HBPF_SLT_VTERM
                    _apply_master_slot_contention = (
                        not _defer_slot_contention
                        or master_slot_holds_server_broadcast(
                            _ts_st,
                            pkt_time,
                            server_voice_rf_srcs=all_server_voice_ids(self._config),
                        )
                    )
                    if (
                        _apply_master_slot_contention
                        and hbp_slot_blocks_group_voice(
                            _ts_st,
                            entry_tgid_b,
                            stream_id,
                            pkt_time,
                            _group_hangtime,
                            is_vterm=(
                                frame_type == HBPF_DATA_SYNC
                                and dtype_vseq == HBPF_SLT_VTERM
                            ),
                        )
                    ):
                        if not _src_stream_st.get("CONTENTION"):
                            _src_stream_st["CONTENTION"] = True
                            if slot_has_active_voice(_ts_st, pkt_time):
                                logger.info(
                                    "(%s) Call not routed to TGID %s, target slot busy: HBSystem: %s, TS: %s, TGID: %s",
                                    system_name,
                                    int_id(entry_tgid_b),
                                    entry.get("SYSTEM"),
                                    entry_ts,
                                    int_id(_ts_st.get("RX_TGID", b"") or _ts_st.get("TX_TGID", b"")),
                                )
                            else:
                                logger.info(
                                    "(%s) Call not routed to TGID %s, target in group hangtime: HBSystem: %s, TS: %s, TGID: %s",
                                    system_name,
                                    int_id(entry_tgid_b),
                                    entry.get("SYSTEM"),
                                    entry_ts,
                                    int_id(_ts_st.get("RX_TGID", b"") or _ts_st.get("TX_TGID", b"")),
                                )
                        continue
                    # New stream detection — legacy OBP uses flat TX_STREAM_ID; deferred OBP uses per-stream legs.
                    if _obp_deferred_bridge and _bridge_tx_leg is not None:
                        _is_new_stream = "TX_STREAM_ID" not in _bridge_tx_leg
                    elif source_is_obp:
                        _is_new_stream = (_ts_st.get("TX_STREAM_ID") != stream_id)
                    else:
                        src_status = getattr(src_proto, "STATUS", None) if src_proto else None
                        _is_new_stream = (src_status.get(slot, {}).get("RX_STREAM_ID") if src_status else b"") != stream_id
                    if _is_new_stream:
                        dst_lc = source_lc[0:3] + entry_tgid_b + rf_src
                        if _obp_deferred_bridge and _bridge_tx_leg is not None:
                            _bridge_tx_leg["TX_START"] = pkt_time
                            _bridge_tx_leg["TX_TGID"] = entry_tgid_b
                            _bridge_tx_leg["TX_STREAM_ID"] = stream_id
                            _bridge_tx_leg["TX_RFS"] = rf_src
                            _bridge_tx_leg["TX_PEER"] = peer_id
                            _bridge_tx_leg["TX_H_LC"] = bptc.encode_header_lc(dst_lc)
                            _bridge_tx_leg["TX_T_LC"] = bptc.encode_terminator_lc(dst_lc)
                            _bridge_tx_leg["TX_EMB_LC"] = self._encode_emblc(dst_lc)
                            if obp_flat_bridge_tx_idle(_ts_st, pkt_time) or _ts_st.get("TX_STREAM_ID") == stream_id:
                                obp_publish_flat_bridge_tx(_ts_st, _bridge_tx_leg)
                            self._dispatch_talker_alias_on_bridge_open(
                                _bridge_tx_leg,
                                system_name,
                                entry["SYSTEM"],
                                rf_src,
                                stream_id,
                                peer_id,
                                int(entry_ts),
                                int_id(entry_tgid_b),
                            )
                        else:
                            _ts_st["TX_START"] = pkt_time
                            _ts_st["TX_TGID"] = entry_tgid_b
                            _ts_st["TX_STREAM_ID"] = stream_id
                            _ts_st["TX_RFS"] = rf_src
                            _ts_st["TX_PEER"] = peer_id
                            _ts_st["TX_H_LC"] = bptc.encode_header_lc(dst_lc)
                            _ts_st["TX_T_LC"] = bptc.encode_terminator_lc(dst_lc)
                            _ts_st["TX_EMB_LC"] = self._encode_emblc(dst_lc)
                            self._dispatch_talker_alias_on_bridge_open(
                                _ts_st,
                                system_name,
                                entry["SYSTEM"],
                                rf_src,
                                stream_id,
                                peer_id,
                                int(entry_ts),
                                int_id(entry_tgid_b),
                            )
                        logger.info(
                            "(%s) Conference Bridge: %s, Call Bridged to HBP System: %s TS: %s, TGID: %s",
                            system_name, _relay_table_key, entry["SYSTEM"], entry_ts, int_id(entry_tgid_b),
                        )
                        self._send_routing_event(
                            "GROUP VOICE,START,TX,{},{},{},{},{},{},{}".format(
                                entry["SYSTEM"],
                                int_id(stream_id),
                                _tx_report_peer,
                                int_id(rf_src),
                                entry_ts,
                                int_id(entry_tgid_b),
                                int(synthetic_announcement),
                            )
                        )
                    if _obp_deferred_bridge and _bridge_tx_leg is not None:
                        obp_sync_flat_bridge_tx_times(
                            _ts_st, _bridge_tx_leg, stream_id, pkt_time, dtype_vseq,
                        )
                    else:
                        _ts_st["TX_TIME"] = pkt_time
                        _ts_st["TX_TYPE"] = dtype_vseq
                    # Slot bit rewrite (legacy bridge.py 457-460 / 770-773)
                    _src_entry_ts = slot
                    if _src_entry_ts != entry_ts:
                        _tmp_bits = _bits ^ (1 << 7)
                    else:
                        _tmp_bits = _bits
                    _tmp_data = b"".join([data[:8], entry_tgid_b, data[11:15], bytes([_tmp_bits]), data[16:20]])
                    # LC rewrite (exact port of legacy bridge.py 468-482 / 781-799)
                    dmrbits = bitarray(endian="big")
                    dmrbits.frombytes(dmrpkt)
                    if frame_type == HBPF_DATA_SYNC and dtype_vseq == HBPF_SLT_VHEAD:
                        dmrbits = _lc_row["TX_H_LC"][0:98] + dmrbits[98:166] + _lc_row["TX_H_LC"][98:197]
                    elif frame_type == HBPF_DATA_SYNC and dtype_vseq == HBPF_SLT_VTERM:
                        dmrbits = _lc_row["TX_T_LC"][0:98] + dmrbits[98:166] + _lc_row["TX_T_LC"][98:197]
                        if not _closing_bridge_leg:
                            if _obp_deferred_bridge and _bridge_tx_leg is not None:
                                call_duration = pkt_time - _bridge_tx_leg.get("TX_START", pkt_time)
                                _end_peer = _bridge_tx_leg.get("TX_PEER", peer_id)
                            else:
                                call_duration = pkt_time - _ts_st.get("TX_START", pkt_time)
                                _end_peer = _ts_st.get("TX_PEER", peer_id)
                            _end_report_peer = int_id(_end_peer)
                            if not source_is_obp and not synthetic_announcement:
                                _end_report_peer = int_id(
                                    resolve_voice_peer_id(
                                        _end_peer, rf_src, system_name, systems_cfg
                                    )
                                )
                            self._send_routing_event(
                                "GROUP VOICE,END,TX,{},{},{},{},{},{},{:.2f},{}".format(
                                    entry["SYSTEM"],
                                    int_id(stream_id),
                                    _end_report_peer,
                                    int_id(rf_src),
                                    entry_ts,
                                    int_id(entry_tgid_b),
                                    call_duration,
                                    int(synthetic_announcement),
                                )
                            )
                        if not _obp_deferred_bridge:
                            _ts_st["TX_TYPE"] = HBPF_SLT_VTERM
                    elif dtype_vseq in (1, 2, 3, 4):
                        self._rewrite_embed_lc(
                            dmrbits, _lc_row, dtype_vseq, "TX_EMB_LC",
                        )
                    dmrpkt_out = dmrbits.tobytes()
                    # bridge_master.routerOBP.to_target HBP branch: _tmp_data + dmrpkt only (~2041-2042);
                    # HBP source adds BER/RSSI from payload (bridge.py routerHBP ~800).
                    if source_is_obp:
                        _tmp_data = b"".join([_tmp_data, dmrpkt_out])
                    else:
                        _tmp_data = b"".join([_tmp_data, dmrpkt_out, data[53:55]])
                    try:
                        self._send_to_system(
                            entry["SYSTEM"], _tmp_data,
                            _hops=_hops, _ber=_ber, _rssi=_rssi,
                            _source_server=_source_server, _source_rptr=_source_rptr,
                        )
                        forwarded.append(entry["SYSTEM"])
                    except Exception as e:
                        logger.warning("(ROUTER) send_to_system %s failed: %s", entry.get("SYSTEM"), e)
                    if frame_type == HBPF_DATA_SYNC and dtype_vseq == HBPF_SLT_VTERM:
                        if _obp_deferred_bridge and _bridge_tx_leg is not None:
                            self._clear_talker_alias_embed(_bridge_tx_leg)
                            obp_clear_deferred_bridge_tx_leg(_ts_st, stream_id, pkt_time)
                        else:
                            self._clear_talker_alias_embed(_ts_st)
                        self._talker_alias.clear_stream(entry["SYSTEM"], stream_id)
        # Legacy bridge_master routerOBP ~2420-2434: after to_target, VTERM — CALL END log, END RX report, _fin, lastSeq
        if (
            source_is_obp
            and call_type in ("group", "vcsbk")
            and frame_type == HBPF_DATA_SYNC
            and dtype_vseq == HBPF_SLT_VTERM
        ):
            _src_p = protocols.get(system_name) if protocols else None
            # Legacy parity (bridge_master.py:1911): routerOBP.STATUS is the flat dict.
            _obp_st = getattr(_src_p, "STATUS", None) if _src_p else None
            _ost_candidate = _obp_st.get(stream_id) if isinstance(_obp_st, dict) else None
            if isinstance(_ost_candidate, dict):
                ost = _ost_candidate
                _end_t = time.time()
                call_duration = _end_t - ost.get("START", _end_t)
                packet_rate = (ost.get("packets", 0) / call_duration) if call_duration else 0.0
                loss_pct = ((ost.get("loss", 0) / ost["packets"]) * 100) if ost.get("packets") else 0.0
                logger.info(
                    "(%s) *CALL END*   STREAM ID: %s SUB: %s PEER: %s TGID %s, TS %s, Duration: %.2f, Packet rate: %.2f/s, Loss: %.2f%%",
                    system_name,
                    int_id(stream_id),
                    int_id(rf_src),
                    int_id(peer_id),
                    int_id(dst_id),
                    slot,
                    call_duration,
                    packet_rate,
                    loss_pct,
                )
                self._send_routing_event(
                    "GROUP VOICE,END,RX,{},{},{},{},{},{},{:.2f},{}".format(
                        system_name,
                        int_id(stream_id),
                        int_id(peer_id),
                        int_id(rf_src),
                        slot,
                        int_id(dst_id),
                        call_duration,
                        int(synthetic_announcement),
                    )
                )
                if ost.get("_plugin_voice") and self._voice_plugin_bridge is not None:
                    self._voice_plugin_bridge.emit_group_voice_end(
                        system_name=system_name,
                        peer_id=peer_id,
                        rf_src=rf_src,
                        dst_id=dst_id,
                        slot=slot,
                        stream_id=stream_id,
                        pkt_time=_end_t,
                        duration_s=call_duration,
                        source_is_obp=True,
                        obp_hops=obp_hops if obp_use_parsed else b"",
                        obp_source_server=obp_source_server,
                        obp_ber=obp_ber,
                        obp_rssi=obp_rssi,
                        obp_source_rptr=obp_source_rptr,
                        synthetic_announcement=synthetic_announcement,
                        forwarded=tuple(forwarded),
                    )
                    ost.pop("_plugin_voice", None)
                ost["_fin"] = True
                self._obp_emit_end_tx_for_forward_legs(stream_id, system_name, _end_t)
                ost["lastSeq"] = False
        if forwarded:
            if frame_type == HBPF_DATA_SYNC and dtype_vseq == HBPF_SLT_VHEAD:
                logger.info(
                    "(ROUTER) Bridged TG %s from %s -> %s",
                    relay_table_key, system_name, ", ".join(forwarded),
                )
        if (
            call_type in ("group", "vcsbk")
            and self._voice_plugin_bridge is not None
            and frame_type in (HBPF_VOICE, HBPF_VOICE_SYNC)
            and (
                not source_is_obp
                or obp_status_plugin_voice(protocols, system_name, stream_id)
            )
        ):
            self._voice_plugin_bridge.notify_group_after_forward(
                system_name=system_name,
                peer_id=peer_id,
                rf_src=rf_src,
                dst_id=dst_id,
                slot=slot,
                frame_type=frame_type,
                dtype_vseq=dtype_vseq,
                stream_id=stream_id,
                data=data,
                pkt_time=pkt_time,
                source_is_obp=source_is_obp,
                obp_hops=obp_hops if obp_use_parsed else b"",
                obp_source_server=obp_source_server,
                obp_ber=obp_ber,
                obp_rssi=obp_rssi,
                obp_source_rptr=obp_source_rptr,
                synthetic_announcement=synthetic_announcement,
                forwarded=forwarded,
            )
        return True

    # ── Unit DATA path (SMS, GPS, CSBK) — legacy routerOBP/routerHBP unit data branch ──

    def _log_unit_data_hbp_busy(
        self,
        system_name: str,
        d_system: str,
        d_slot: int,
        int_dst_id: int,
        dst_slot: dict,
        d_sys_cfg: dict,
        pkt_time: float,
    ) -> None:
        """Legacy logs busy at debug only; include slot state for D-APRS downlink triage."""
        logger.info(
            "(%s) UNIT Data not bridged to HBP - target busy: %s slot %s DST_ID: %s "
            "(RX_TYPE=%s TX_TYPE=%s RX_TGID=%s TX_TGID=%s TX_age=%.2fs hangtime=%s)",
            system_name,
            d_system,
            d_slot,
            int_dst_id,
            dst_slot.get("RX_TYPE"),
            dst_slot.get("TX_TYPE"),
            int_id(dst_slot.get("RX_TGID", b"\x00\x00\x00")),
            int_id(dst_slot.get("TX_TGID", b"\x00\x00\x00")),
            pkt_time - dst_slot.get("TX_TIME", 0),
            d_sys_cfg.get("GROUP_HANGTIME", 5),
        )

    def _unit_data_received(
        self,
        system_name: str,
        peer_id: bytes,
        rf_src: bytes,
        dst_id: bytes,
        seq: int,
        slot: int,
        frame_type: int,
        dtype_vseq: int,
        stream_id: bytes,
        data: bytes,
        *,
        obp_use_parsed: bool = False,
        obp_hops: bytes = b"",
        obp_source_server: bytes | None = None,
        obp_ber: bytes = b"\x00",
        obp_rssi: bytes = b"\x00",
        obp_source_rptr: bytes = b"\x00\x00\x00\x00",
    ) -> None:
        """Legacy routerOBP/routerHBP unit data branch: DATA-GATEWAY + OBP fan-out + SUB_MAP/hotspot."""
        pkt_time = time.time()
        dmrpkt = data[20:53] if len(data) >= 53 else b""
        _bits = data[15] if len(data) > 15 else 0
        _int_dst_id = int_id(dst_id)
        _forwarded: list[str] = []
        systems_cfg = self._config.get("SYSTEMS", {})
        source_is_obp = systems_cfg.get(system_name, {}).get("MODE") == "OPENBRIDGE"
        global_cfg = self._config.get("GLOBAL", {})
        if source_is_obp and obp_source_server is not None:
            _source_server = obp_source_server
        else:
            _sid = global_cfg.get("SERVER_ID", b"\x00\x00\x00\x00")
            if isinstance(_sid, bytes) and len(_sid) >= 4:
                _source_server = _sid
            elif _sid is not None:
                _source_server = bytes_4(int(_sid) & 0xFFFFFFFF)
            else:
                _source_server = b"\x00\x00\x00\x00"
        _source_rptr = peer_id if not source_is_obp else obp_source_rptr

        if source_is_obp:
            _hops = obp_hops
            _ber = obp_ber
            _rssi = obp_rssi
        else:
            _hops = b""
            _ber = data[53:54] if len(data) > 53 else b"\x00"
            _rssi = data[54:55] if len(data) > 54 else b"\x00"

        # OBP unit-data loop control (legacy routerOBP ~2201-2255)
        if source_is_obp:
            protocols = self._get_protocols() if self._get_protocols else {}
            src_proto = protocols.get(system_name)
            if src_proto:
                status = getattr(src_proto, "STATUS", None)
                if status is not None:
                    if stream_id not in status:
                        status[stream_id] = {
                            "START": pkt_time, "CONTENTION": False, "RFS": rf_src,
                            "TGID": dst_id, "1ST": perf_counter(), "lastSeq": False,
                            "lastData": False, "RX_PEER": peer_id, "packets": 0, "crcs": set(),
                        }
                    status[stream_id]["LAST"] = pkt_time
                    status[stream_id]["packets"] = status[stream_id].get("packets", 0) + 1
                    # HBP loop check: if any HBP already has this RX_STREAM_ID, drop
                    for other_name, proto in protocols.items():
                        omode = systems_cfg.get(other_name, {}).get("MODE")
                        if other_name != system_name and omode != "OPENBRIDGE":
                            ostatus = getattr(proto, "STATUS", None)
                            if not ostatus:
                                continue
                            for _sysslot in ostatus:
                                slot_st = ostatus.get(_sysslot)
                                if isinstance(slot_st, dict) and stream_id == slot_st.get("RX_STREAM_ID"):
                                    if not status[stream_id].get("LOOPLOG"):
                                        logger.debug(
                                            "(%s) OBP UNIT *LoopControl* FIRST HBP: %s, STREAM ID: %s, TG: %s, TS: %s, IGNORE THIS SOURCE",
                                            system_name, other_name, int_id(stream_id), int_id(dst_id), _sysslot,
                                        )
                                        status[stream_id]["LOOPLOG"] = True
                                    return
                    # OBP earliest-wins: compare 1ST across OBP systems
                    hr_times: dict[str, float] = {}
                    for other_name, proto in protocols.items():
                        omode = systems_cfg.get(other_name, {}).get("MODE")
                        if omode == "OPENBRIDGE":
                            obp_st = getattr(proto, "STATUS", None)
                            if obp_st and stream_id in obp_st and "1ST" in obp_st[stream_id]:
                                if obp_st[stream_id].get("TGID") == dst_id:
                                    hr_times[other_name] = obp_st[stream_id]["1ST"]
                    fi = min(hr_times, key=hr_times.get, default=False)
                    if not fi:
                        logger.warning(
                            "(%s) OBP UNIT *LoopControl* fi is empty: STREAM ID: %s, TG: %s",
                            system_name, int_id(stream_id), int_id(dst_id),
                        )
                        return
                    if system_name != fi:
                        if not status[stream_id].get("LOOPLOG"):
                            logger.debug(
                                "(%s) OBP UNIT *LoopControl* FIRST OBP %s, STREAM ID: %s, TG %s, IGNORE THIS SOURCE",
                                system_name, fi, int_id(stream_id), int_id(dst_id),
                            )
                            status[stream_id]["LOOPLOG"] = True
                        return

        # Dtype-specific logging (legacy routerOBP ~2257-2280, routerHBP ~3052-3082)
        if dtype_vseq == 3:
            logger.info(
                "(%s) *UNIT CSBK* STREAM ID: %s SUB: %s PEER: %s DST_ID %s TS %s",
                system_name, int_id(stream_id), int_id(rf_src), int_id(peer_id), _int_dst_id, slot,
            )
        elif dtype_vseq == 6:
            logger.info(
                "(%s) *UNIT DATA HEADER* STREAM ID: %s SUB: %s PEER: %s DST_ID %s TS %s",
                system_name, int_id(stream_id), int_id(rf_src), int_id(peer_id), _int_dst_id, slot,
            )
        elif dtype_vseq == 7:
            logger.info(
                "(%s) *UNIT VCSBK 1/2 DATA BLOCK* STREAM ID: %s SUB: %s PEER: %s TGID %s TS %s",
                system_name, int_id(stream_id), int_id(rf_src), int_id(peer_id), _int_dst_id, slot,
            )
        elif dtype_vseq == 8:
            logger.info(
                "(%s) *UNIT VCSBK 3/4 DATA BLOCK* STREAM ID: %s SUB: %s PEER: %s TGID %s TS %s",
                system_name, int_id(stream_id), int_id(rf_src), int_id(peer_id), _int_dst_id, slot,
            )
        else:
            logger.info(
                "(%s) *UNKNOWN DATA TYPE* STREAM ID: %s SUB: %s PEER: %s TGID %s TS %s",
                system_name, int_id(stream_id), int_id(rf_src), int_id(peer_id), _int_dst_id, slot,
            )

        if unit_data_reportable(dtype_vseq):
            _dtype_labels = {6: "UNIT DATA HEADER", 7: "UNIT VCSBK 1/2 DATA BLOCK", 8: "UNIT VCSBK 3/4 DATA BLOCK"}
            _label = _dtype_labels.get(dtype_vseq, "UNIT DATA")
            self._send_routing_event(
                "{},DATA,RX,{},{},{},{},{},{}".format(
                    _label, system_name, int_id(stream_id), int_id(peer_id), int_id(rf_src), slot, _int_dst_id,
                )
            )

        # DATA-GATEWAY forwarding (legacy ~2281-2284 / ~3083-3087)
        if global_cfg.get("DATA_GATEWAY"):
            dg_cfg = systems_cfg.get("DATA-GATEWAY", {})
            if dg_cfg.get("MODE") == "OPENBRIDGE" and dg_cfg.get("ENABLED"):
                logger.debug("(%s) DATA packet sent to DATA-GATEWAY", system_name)
                _forwarded.append("DATA-GATEWAY")
                self._send_data_to_obp(
                    system_name, "DATA-GATEWAY", data, dmrpkt, pkt_time, stream_id,
                    dst_id, peer_id, rf_src, _bits, slot,
                    hops=_hops, ber=_ber, rssi=_rssi,
                    source_server=_source_server, source_rptr=_source_rptr,
                )

        # Fan-out to all OBP systems with VER > 1 and dst_id >= 1000000 (legacy ~2286-2295 / ~3088-3097)
        # ...unless the destination is a subscriber recently seen on a LOCAL (non-OPENBRIDGE)
        # system: SUB_MAP below already delivers it here. Flooding every OpenBridge with e.g.
        # each ARS/LRRP answer of a D-APRS gateway to a local MOTOTRBO multiplies traffic by
        # the number of bridges, and remote masters that mis-file those frames create a
        # dynamic talkgroup named after the radio id.
        protocols = self._get_protocols() if self._get_protocols else {}
        _local_sub = self._config.get("_SUB_MAP", {}).get(dst_id)
        _dst_is_fresh_local_sub = bool(
            _local_sub
            and systems_cfg.get(_local_sub[0], {}).get("MODE") != "OPENBRIDGE"
            and pkt_time - _local_sub[2] < UNIT_DATA_LOCAL_SUB_MAX_AGE
        )
        for sys_name, sys_cfg in systems_cfg.items():
            if _dst_is_fresh_local_sub:
                break
            if sys_name == system_name:
                continue
            if sys_name == "DATA-GATEWAY":
                continue
            if sys_cfg.get("MODE") == "OPENBRIDGE" and sys_cfg.get("VER", 1) > 1 and _int_dst_id >= 1000000:
                _forwarded.append(sys_name)
                self._send_data_to_obp(
                    system_name, sys_name, data, dmrpkt, pkt_time, stream_id,
                    dst_id, peer_id, rf_src, _bits, slot,
                    hops=_hops, ber=_ber, rssi=_rssi,
                    source_server=_source_server, source_rptr=_source_rptr,
                )

        # SUB_MAP lookup (legacy ~2297-2312 / ~3099-3114)
        sub_map = self._config.get("_SUB_MAP", {})
        if dst_id in sub_map:
            _sub_map_entry = sub_map[dst_id]
            _d_system, _d_slot, _d_time = _sub_map_entry[:3]
            _d_peer_id = _sub_map_entry[3] if len(_sub_map_entry) > 3 else None
            _d_proto = protocols.get(_d_system)
            if _d_proto:
                _dst_slot = getattr(_d_proto, "STATUS", {}).get(_d_slot, {})
                logger.info("(%s) SUB_MAP matched, System: %s Slot: %s, Time: %s", system_name, _d_system, _d_slot, _d_time)
                _d_sys_cfg = systems_cfg.get(_d_system, {})
                _hangtime = _d_sys_cfg.get("GROUP_HANGTIME", 5)
                if unit_data_hbp_target_idle(_dst_slot, pkt_time, _hangtime):
                    _tmp_bits = _bits ^ (1 << 7) if slot != _d_slot else _bits
                    _forwarded.append(_d_system)
                    self._send_data_to_hbp(system_name, _d_system, _d_slot, dst_id, _tmp_bits, data, dmrpkt, rf_src, stream_id, peer_id, d_peer_id=_d_peer_id)
                elif not is_private_subscriber_dst(dst_id):
                    self._log_unit_data_hbp_busy(
                        system_name, _d_system, _d_slot, _int_dst_id, _dst_slot, _d_sys_cfg, pkt_time,
                    )
        else:
            # Hotspot 6/7-digit peer ID match (legacy ~3131-3168 / ~2314-2345)
            for _d_system, _d_sys_cfg in systems_cfg.items():
                if _d_sys_cfg.get("MODE") != "MASTER":
                    continue
                _d_proto = protocols.get(_d_system)
                if not _d_proto:
                    continue
                _peers = _d_sys_cfg.get("PEERS", {})
                _matched = False
                for _to_peer in _peers:
                    _int_to_peer = int_id(_to_peer)
                    _dst_str = str(_int_dst_id)
                    _to_str = str(_int_to_peer)
                    if len(_dst_str) == 6:
                        if _to_str[:6] == _dst_str:
                            _d_slot = 2
                            _dst_slot = getattr(_d_proto, "STATUS", {}).get(_d_slot, {})
                            logger.info("(%s) User Peer Hotspot ID (6-digit) matched, System: %s Slot: %s", system_name, _d_system, _d_slot)
                            _hangtime = _d_sys_cfg.get("GROUP_HANGTIME", 5)
                            if unit_data_hbp_target_idle(_dst_slot, pkt_time, _hangtime):
                                _tmp_bits = _bits ^ (1 << 7) if slot != 2 else _bits
                                _forwarded.append(_d_system)
                                self._send_data_to_hbp(system_name, _d_system, _d_slot, dst_id, _tmp_bits, data, dmrpkt, rf_src, stream_id, peer_id, d_peer_id=_to_peer)
                            elif not is_private_subscriber_dst(dst_id):
                                self._log_unit_data_hbp_busy(
                                    system_name, _d_system, _d_slot, _int_dst_id, _dst_slot, _d_sys_cfg, pkt_time,
                                )
                            _matched = True
                            break
                    elif len(_dst_str) >= 7:
                        if _to_str[:7] == _dst_str[:7]:
                            _d_slot = 2
                            _dst_slot = getattr(_d_proto, "STATUS", {}).get(_d_slot, {})
                            logger.info("(%s) User Peer Hotspot ID (7-digit) matched, System: %s Slot: %s", system_name, _d_system, _d_slot)
                            _hangtime = _d_sys_cfg.get("GROUP_HANGTIME", 5)
                            if unit_data_hbp_target_idle(_dst_slot, pkt_time, _hangtime):
                                _tmp_bits = _bits ^ (1 << 7) if slot != 2 else _bits
                                _forwarded.append(_d_system)
                                self._send_data_to_hbp(system_name, _d_system, _d_slot, dst_id, _tmp_bits, data, dmrpkt, rf_src, stream_id, peer_id, d_peer_id=_to_peer)
                            elif not is_private_subscriber_dst(dst_id):
                                self._log_unit_data_hbp_busy(
                                    system_name, _d_system, _d_slot, _int_dst_id, _dst_slot, _d_sys_cfg, pkt_time,
                                )
                            _matched = True
                            break
                if _matched:
                    break

        if self._data_plugin_bridge is not None:
            self._data_plugin_bridge.notify_after_forward(
                route="unit",
                system_name=system_name,
                peer_id=peer_id,
                rf_src=rf_src,
                dst_id=dst_id,
                seq=seq,
                slot=slot,
                frame_type=frame_type,
                dtype_vseq=dtype_vseq,
                stream_id=stream_id,
                data=data,
                pkt_time=pkt_time,
                source_is_obp=source_is_obp,
                obp_hops=_hops,
                obp_source_server=_source_server if source_is_obp else None,
                obp_ber=_ber,
                obp_rssi=_rssi,
                obp_source_rptr=_source_rptr,
                forwarded=_forwarded,
            )

    def _pvt_call_received(
        self,
        system_name: str,
        peer_id: bytes,
        rf_src: bytes,
        dst_id: bytes,
        seq: int,
        slot: int,
        frame_type: int,
        dtype_vseq: int,
        stream_id: bytes,
        data: bytes,
    ) -> None:
        """Legacy pvt_call_received: route private (unit) calls via SUB_MAP lookup."""
        pkt_time = time.time()
        dmrpkt = data[20:53] if len(data) >= 53 else b""
        _bits = data[15] if len(data) > 15 else 0
        sub_map = self._config.get("_SUB_MAP", {})
        if sub_map is not None:
            sub_map[rf_src] = (system_name, slot, pkt_time, peer_id)
        systems_cfg = self._config.get("SYSTEMS", {})
        protocols = self._get_protocols() if self._get_protocols else {}
        source_proto = protocols.get(system_name)
        source_status = getattr(source_proto, "STATUS", {}) if source_proto else {}
        if source_proto:
            source_status.setdefault(slot, {})
        slot_st = source_status[slot] if source_proto and slot in source_status else {}
        _notify_pvt_start = False
        _unit_data = is_unit_data_ingress(
            "unit", dtype_vseq, stream_id, slot_st.get("RX_STREAM_ID"),
        )
        if stream_id != slot_st.get("RX_STREAM_ID"):
            if (slot_st.get("RX_TYPE") != HBPF_SLT_VTERM) and (pkt_time < (slot_st.get("RX_TIME", 0) + STREAM_TO)) and (rf_src != slot_st.get("RX_RFS")):
                logger.warning(
                    "(%s) PRIVATE CALL Packet received with STREAM ID: %s <FROM> SUB: %s PEER: %s <TO> UNIT %s, SLOT %s collided with existing call",
                    system_name, int_id(stream_id), int_id(rf_src), int_id(peer_id), int_id(dst_id), slot,
                )
                return
            _notify_pvt_start = True
            slot_st["RX_START"] = pkt_time
            self._pvt_same_system_dst_peer_id = None
            if dst_id in sub_map:
                _dst_entry = sub_map[dst_id]
                if _dst_entry[0] != system_name:
                    self._pvt_targets = [_dst_entry[0]]
                    # Exact hotspot the destination was last heard on, if SUB_MAP has it
                    # (4th element) -- lets delivery target that one peer on the target
                    # system instead of send_to_system's broadcast-to-every-peer default.
                    self._pvt_target_peer_ids = {
                        _dst_entry[0]: (_dst_entry[3] if len(_dst_entry) > 3 else None)
                    }
                else:
                    self._pvt_targets = []
                    self._pvt_target_peer_ids = {}
                    logger.error("PRIVATE call to a subscriber on the same system, send nothing")
                    # Delivery is already handled by the MASTER's own local REPEAT
                    # (_pvt_repeat_targets in udp_hbp.py) -- this branch only exists so the
                    # monitor also learns who is *receiving*, which nothing else reports.
                    if len(_dst_entry) > 3 and _dst_entry[3] is not None:
                        _dest_peers = getattr(source_proto, "_peers", {}) if source_proto else {}
                        if _dst_entry[3] in _dest_peers:
                            self._pvt_same_system_dst_peer_id = _dst_entry[3]
            else:
                self._pvt_targets = []
                self._pvt_target_peer_ids = {}
            logger.info(
                "(%s) *PRIVATE CALL START* STREAM ID: %s SUB: %s PEER: %s DST: %s, TS: %s, FORWARD: %s",
                system_name, int_id(stream_id), int_id(rf_src), int_id(peer_id), int_id(dst_id), slot, self._pvt_targets,
            )
            # Unit data (ARS/LRRP/SMS) has no VTERM; PRIVATE VOICE START without END leaves
            # monitor timeslot chips stuck. UNIT DATA events cover lastheard instead.
            if not _unit_data:
                self._send_routing_event(
                    "PRIVATE VOICE,START,RX,{},{},{},{},{},{}".format(
                        system_name, int_id(stream_id), int_id(peer_id), int_id(rf_src), slot, int_id(dst_id)
                    )
                )
                if self._pvt_same_system_dst_peer_id is not None:
                    self._send_routing_event(
                        "PRIVATE VOICE,START,TX,{},{},{},{},{},{},{}".format(
                            system_name, int_id(stream_id), int_id(peer_id), int_id(rf_src), slot, int_id(dst_id),
                            int_id(self._pvt_same_system_dst_peer_id),
                        )
                    )
        for _target in getattr(self, "_pvt_targets", []):
            target_proto = protocols.get(_target)
            if not target_proto:
                continue
            _target_status = getattr(target_proto, "STATUS", {})
            _target_system = systems_cfg.get(_target, {})
            _target_peer_id = None
            if _target_system.get("MODE") != "OPENBRIDGE":
                _target_peer_id = getattr(self, "_pvt_target_peer_ids", {}).get(_target)
            if _target_system.get("MODE") == "OPENBRIDGE":
                if _target_system.get("ENHANCED_OBP") and self._obp_session(_target).keepalive_stale(pkt_time):
                    continue
                if stream_id not in _target_status:
                    _target_status[stream_id] = {
                        "START": pkt_time,
                        "CONTENTION": False,
                        "RFS": rf_src,
                        "TYPE": "UNIT",
                        "DST": dst_id,
                        "ACTIVE": True,
                    }
                    logger.info(
                        "(%s) PRIVATE call bridged to OBP System: %s TS: %s, UNIT: %s",
                        system_name, _target, slot if _target_system.get("BOTH_SLOTS") else 1, int_id(dst_id),
                    )
                    if not _unit_data:
                        self._send_routing_event(
                            "PRIVATE VOICE,START,TX,{},{},{},{},{},{}".format(
                                _target, int_id(stream_id), int_id(peer_id), int_id(rf_src), slot, int_id(dst_id),
                            ).encode("utf-8", "ignore")
                        )
                _target_status[stream_id]["LAST"] = pkt_time
                if _target_system.get("BOTH_SLOTS"):
                    _tmp_bits = _bits
                else:
                    _tmp_bits = _bits & ~(1 << 7)
                _tmp_data = b"".join([data[:15], _tmp_bits.to_bytes(1, "big"), data[16:20]])
                send_data = b"".join([_tmp_data, dmrpkt])
                if frame_type == HBPF_DATA_SYNC and dtype_vseq == HBPF_SLT_VTERM:
                    _target_status[stream_id]["ACTIVE"] = False
            else:
                ts_st = _target_status.get(slot, {})
                # Voice private-call contention only. Unit data (ARS/LRRP/SMS) must
                # pierce group/voice RX on the target slot — legacy pvt_call still
                # sends via send_system even when sendDataToHBP is busy.
                if not _unit_data:
                    if (dst_id == ts_st.get("RX_TGID")) and ((pkt_time - ts_st.get("RX_TIME", 0)) < STREAM_TO):
                        if frame_type == HBPF_DATA_SYNC and dtype_vseq == HBPF_SLT_VHEAD and slot_st.get("RX_STREAM_ID") != stream_id:
                            logger.info(
                                "(%s) PRIVATE Call not routed to destination %s, matching call already active on target: HBSystem: %s, TS: %s, DEST: %s",
                                system_name, int_id(dst_id), _target, slot, int_id(ts_st.get("RX_TGID", b"")),
                            )
                        continue
                    if (dst_id == ts_st.get("TX_TGID")) and (rf_src != ts_st.get("TX_RFS")) and ((pkt_time - ts_st.get("TX_TIME", 0)) < STREAM_TO):
                        if frame_type == HBPF_DATA_SYNC and dtype_vseq == HBPF_SLT_VHEAD and slot_st.get("RX_STREAM_ID") != stream_id:
                            logger.info(
                                "(%s) PRIVATE Call not routed for subscriber %s, call route in progress on target: HBSystem: %s, TS: %s, DEST: %s, SUB: %s",
                                system_name, int_id(rf_src), _target, slot, int_id(ts_st.get("TX_TGID", b"")), int_id(ts_st.get("TX_RFS", b"")),
                            )
                        continue
                if stream_id != slot_st.get("RX_STREAM_ID"):
                    ts_st["TX_START"] = pkt_time
                    ts_st["TX_TGID"] = dst_id
                    ts_st["TX_STREAM_ID"] = stream_id
                    ts_st["TX_RFS"] = rf_src
                    ts_st["TX_PEER"] = peer_id
                    logger.info("(%s) PRIVATE call bridged to HBP System: %s TS: %s, DST: %s", system_name, _target, slot, int_id(dst_id))
                    if not _unit_data:
                        if _target_peer_id is not None and _target_peer_id in getattr(target_proto, "_peers", {}):
                            self._send_routing_event(
                                "PRIVATE VOICE,START,TX,{},{},{},{},{},{},{}".format(
                                    _target, int_id(stream_id), int_id(peer_id), int_id(rf_src), slot, int_id(dst_id),
                                    int_id(_target_peer_id),
                                )
                            )
                        else:
                            self._send_routing_event(
                                "PRIVATE VOICE,START,TX,{},{},{},{},{},{}".format(
                                    _target, int_id(stream_id), int_id(peer_id), int_id(rf_src), slot, int_id(dst_id),
                                )
                            )
                ts_st["TX_TIME"] = pkt_time
                ts_st["TX_TYPE"] = dtype_vseq
                send_data = data
            try:
                if _target_peer_id is not None and target_proto is not None and _target_peer_id in getattr(target_proto, "_peers", {}):
                    target_proto.send_peer(_target_peer_id, send_data)
                else:
                    self._send_to_system(_target, send_data)
            except Exception as e:
                logger.warning("(ROUTER) send_to_system %s failed: %s", _target, e)
        if self._data_plugin_bridge is not None and _unit_data:
            self._data_plugin_bridge.notify_after_forward(
                route="private",
                system_name=system_name,
                peer_id=peer_id,
                rf_src=rf_src,
                dst_id=dst_id,
                seq=seq,
                slot=slot,
                frame_type=frame_type,
                dtype_vseq=dtype_vseq,
                stream_id=stream_id,
                data=data,
                pkt_time=pkt_time,
                source_is_obp=False,
                obp_hops=b"",
                obp_source_server=None,
                obp_ber=data[53:54] if len(data) > 53 else b"\x00",
                obp_rssi=data[54:55] if len(data) > 54 else b"\x00",
                obp_source_rptr=b"\x00\x00\x00\x00",
                forwarded=list(getattr(self, "_pvt_targets", [])),
            )
        if frame_type == HBPF_DATA_SYNC and dtype_vseq == HBPF_SLT_VTERM and slot_st.get("RX_TYPE") != HBPF_SLT_VTERM:
            self._pvt_targets = []
            call_duration = pkt_time - slot_st.get("RX_START", pkt_time)
            logger.info(
                "(%s) *PRIVATE CALL END*   STREAM ID: %s SUB: %s PEER: %s DST: %s, TS %s, Duration: %.2f",
                system_name, int_id(stream_id), int_id(rf_src), int_id(peer_id), int_id(dst_id), slot, call_duration,
            )
            self._send_routing_event(
                "PRIVATE VOICE,END,RX,{},{},{},{},{},{},{:.2f}".format(
                    system_name,
                    int_id(stream_id),
                    int_id(peer_id),
                    int_id(rf_src),
                    slot,
                    int_id(dst_id),
                    call_duration,
                )
            )
            _same_system_dst_peer_id = getattr(self, "_pvt_same_system_dst_peer_id", None)
            if _same_system_dst_peer_id is not None:
                self._send_routing_event(
                    "PRIVATE VOICE,END,TX,{},{},{},{},{},{},{:.2f},{}".format(
                        system_name,
                        int_id(stream_id),
                        int_id(peer_id),
                        int_id(rf_src),
                        slot,
                        int_id(dst_id),
                        call_duration,
                        int_id(_same_system_dst_peer_id),
                    )
                )
            self._pvt_same_system_dst_peer_id = None
        if self._voice_plugin_bridge is not None and not _unit_data:
            _pvt_fwd = list(getattr(self, "_pvt_targets", []))
            if _notify_pvt_start:
                self._voice_plugin_bridge.notify_private_after_forward(
                    system_name=system_name,
                    peer_id=peer_id,
                    rf_src=rf_src,
                    dst_id=dst_id,
                    slot=slot,
                    frame_type=frame_type,
                    dtype_vseq=dtype_vseq,
                    stream_id=stream_id,
                    data=data,
                    pkt_time=pkt_time,
                    synthetic_announcement=False,
                    forwarded_targets=_pvt_fwd,
                    phase="START",
                )
            elif frame_type in (HBPF_VOICE, HBPF_VOICE_SYNC):
                self._voice_plugin_bridge.notify_private_after_forward(
                    system_name=system_name,
                    peer_id=peer_id,
                    rf_src=rf_src,
                    dst_id=dst_id,
                    slot=slot,
                    frame_type=frame_type,
                    dtype_vseq=dtype_vseq,
                    stream_id=stream_id,
                    data=data,
                    pkt_time=pkt_time,
                    synthetic_announcement=False,
                    forwarded_targets=_pvt_fwd,
                    phase="FRAME",
                )
            elif frame_type == HBPF_DATA_SYNC and dtype_vseq == HBPF_SLT_VTERM and slot_st.get("RX_TYPE") != HBPF_SLT_VTERM:
                self._voice_plugin_bridge.notify_private_after_forward(
                    system_name=system_name,
                    peer_id=peer_id,
                    rf_src=rf_src,
                    dst_id=dst_id,
                    slot=slot,
                    frame_type=frame_type,
                    dtype_vseq=dtype_vseq,
                    stream_id=stream_id,
                    data=data,
                    pkt_time=pkt_time,
                    synthetic_announcement=False,
                    forwarded_targets=_pvt_fwd,
                    phase="END",
                    duration_s=call_duration,
                )
        if slot_st:
            if _unit_data:
                # Keep stream continuity for multi-frame unit data without marking the
                # slot voice-busy (parity with is_unit_data_ingress on HBP ingress).
                slot_st["RX_STREAM_ID"] = stream_id
            else:
                slot_st["RX_PEER"] = peer_id
                slot_st["RX_SEQ"] = seq
                slot_st["RX_RFS"] = rf_src
                slot_st["RX_TYPE"] = dtype_vseq
                slot_st["RX_TGID"] = dst_id
                slot_st["RX_TIME"] = pkt_time
                slot_st["RX_STREAM_ID"] = stream_id
