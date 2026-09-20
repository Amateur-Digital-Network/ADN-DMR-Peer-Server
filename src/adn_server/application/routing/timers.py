# ADN DMR Peer Server - bridge timer loops
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

"""Legacy bridge timer / trimmer loops (no Twisted imports)."""

from __future__ import annotations

import logging
import time
from typing import Any

from ...domain import HBPF_SLT_VTERM, int_id
from .helpers import obp_clear_deferred_bridge_tx_leg

logger = logging.getLogger(__name__)


def _obp_status_is_forward_leg(st: dict[str, Any]) -> bool:
    """``to_target`` OPENBRIDGE rows carry rewritten LC (monitor TX leg)."""
    return isinstance(st, dict) and "H_LC" in st


def _obp_status_is_ingress(st: dict[str, Any]) -> bool:
    """Ingress/routerOBP source stream (monitor canonical RX)."""
    return isinstance(st, dict) and not _obp_status_is_forward_leg(st)


class RoutingTimerMixin:
    """rule_timer, stream_trimmer, bridge_reset, stat_trimmer, bridge_debug loops."""

    def rule_timer_loop(self) -> None:
        """Run one iteration of rule_timer_loop (legacy 52s LoopingCall). Activate/deactivate by timeout."""
        from ..subscription.rule_timer_ops import apply_rule_timer_store

        apply_rule_timer_store(
            self._subscription_store,
            self._config.get("SYSTEMS", {}),
            time.time(),
            on_relay_deactivated=self._on_relay_deactivated,
        )
    def subscription_debug_loop(self) -> None:
        """Legacy bridgeDebug (bridge_master.py 487-543): remove invalid bridges, fix >1 active dial per MASTER."""
        logger.debug("(BRIDGEDEBUG) Running bridge debug")
        from ..subscription.subscription_debug_ops import apply_subscription_debug_store

        apply_subscription_debug_store(
            self._subscription_store,
            self._config.get("SYSTEMS", {}),
            time.time(),
        )
    def apply_in_band_signalling(
        self, system_name: str, slot: int, dst_id: bytes, pkt_time: float
    ) -> None:
        """Legacy in-band signalling on voice terminator (bridge_master.py ~3447-3549).

        Reflector bridges (#xxx) are ONLY processed when dst TG is 9 (legacy ~3455).
        De-activation distinguishes SINGLE_MODE True/False (legacy ~3484-3548).
        """
        from ..subscription.in_band_signalling_ops import apply_in_band_signalling_store

        apply_in_band_signalling_store(
            self._subscription_store,
            system_name,
            slot,
            dst_id,
            pkt_time,
            self._config.get("SYSTEMS", {}),
        )
        self._send_routing_table_snapshot(incremental=True)
    def _obp_emit_end_tx_forward_leg(
        self,
        tgt_name: str,
        stream_id: bytes,
        tst: dict[str, Any],
        now: float,
    ) -> bool:
        """Emit GROUP VOICE,END,TX for one to_target OBP forward leg (H_LC in STATUS).

        Same CSV shape as legacy bridge_master.py send_routing_tableEvent on VTERM (~2039, ~2121).
        Safe for legacy and v2 monitors (OPENBRIDGE STREAMS chip clear on END,TX).
        """
        if not isinstance(tst, dict) or "H_LC" not in tst:
            return False
        if tst.get("_end_tx_sent"):
            # Already cleared this leg's monitor TX chip; do not re-emit on repeated BCSQ.
            return False
        if not bool(self._config.get("REPORTS", {}).get("REPORT", True)):
            return False
        rfs = tst.get("RFS", b"\x00\x00\x00")
        peer = tst.get("RX_PEER", b"\x00\x00\x00\x00")
        tgid_b = tst.get("TGID", b"\x00\x00\x00")
        start = tst.get("START", now)
        duration = max(0.0, now - start)
        if not self._send_routing_event(
            "GROUP VOICE,END,TX,{},{},{},{},{},{},{:.2f}".format(
                tgt_name,
                int_id(stream_id),
                int_id(peer),
                int_id(rfs),
                1,
                int_id(tgid_b),
                duration,
            )
        ):
            return False
        tst["_end_tx_sent"] = True
        return True

    def _obp_emit_end_tx_for_forward_legs(self, stream_id: bytes, source_system: str, now: float) -> None:
        """Emit GROUP VOICE,END,TX for every OBP that still holds this stream as a to_target forward leg.

        On idle timeout the trimmer sends END,RX for the source only. VTERM may never arrive for
        forwarded legs, so the monitor would otherwise keep stale TX chips on destination rows.
        Forward legs are identified by STATUS[stream_id] containing H_LC (see to_target OPENBRIDGE).
        """
        protocols = self._get_protocols() if self._get_protocols else {}
        systems_cfg = self._config.get("SYSTEMS", {})
        for tgt_name, tgt_proto in (protocols or {}).items():
            if tgt_name == source_system:
                continue
            if systems_cfg.get(tgt_name, {}).get("MODE") != "OPENBRIDGE":
                continue
            tstatus = getattr(tgt_proto, "STATUS", None)
            if not tstatus or stream_id not in tstatus:
                continue
            tst = tstatus[stream_id]
            # Emit the monitor END,TX chip-clear but DO NOT pop STATUS: legacy keeps the
            # forward-leg entry until the stream trimmer removes it by age (180s). Popping
            # mid-stream caused the source/forward-leg entry to vanish and re-CALL-START churn.
            self._obp_emit_end_tx_forward_leg(tgt_name, stream_id, tst, now)

    def on_obp_bcsq_received(self, system_name: str, tgid: bytes, stream_id: bytes) -> None:
        """After valid BCSQ on this OBP leg: emit the monitor END,TX chip-clear only.

        Legacy parity (hblink.py ~629-639): BCSQ only records CONFIG['_bcsq'][tgid]=stream_id
        (done inline in udp_hbp) and routerOBP.to_target then *skips* the quenched target for
        that stream (see _obp_target_bcsq_quenches_stream). It never destroys stream state.
        We additionally emit a one-shot END,TX so the monitor clears stale TX chips, but we do
        NOT pop STATUS[stream_id]: popping mid-call made the same stream re-CALL-START over and
        over (visible as BCSQ-storm churn), which flapped loop-control and broke OBP->HBP audio.
        The forward-leg entry is removed by the stream trimmer on idle (legacy behaviour).
        """
        protocols = self._get_protocols() if self._get_protocols else {}
        tgt_proto = protocols.get(system_name)
        if not tgt_proto:
            return
        if self._config.get("SYSTEMS", {}).get(system_name, {}).get("MODE") != "OPENBRIDGE":
            return
        tstatus = getattr(tgt_proto, "STATUS", None)
        if not tstatus or stream_id not in tstatus:
            return
        tst = tstatus[stream_id]
        if not isinstance(tst, dict) or "H_LC" not in tst:
            return
        if tst.get("TGID", b"\x00\x00\x00") != tgid:
            return
        now = time.time()
        self._obp_emit_end_tx_forward_leg(system_name, stream_id, tst, now)
        tst["_bcsq_quenched"] = True

    def flush_monitor_events_for_system(self, system_name: str, protocol: Any) -> None:
        if not self._config.get("REPORTS", {}).get("REPORT", True):
            return
        status = getattr(protocol, "STATUS", None)
        if not isinstance(status, dict):
            return
        mode = self._config.get("SYSTEMS", {}).get(system_name, {}).get("MODE")
        now = time.time()

        if mode == "OPENBRIDGE":
            for stream_id, st in list(status.items()):
                if not isinstance(stream_id, (bytes, bytearray)) or not isinstance(st, dict):
                    continue
                trx = "TX" if "H_LC" in st else "RX"
                start = st.get("START", now)
                self._send_routing_event(
                    "GROUP VOICE,END,{},{},{},{},{},{},{},{:.2f}".format(
                        trx, system_name, int_id(stream_id),
                        int_id(st.get("RX_PEER", b"\x00\x00\x00\x00")),
                        int_id(st.get("RFS", b"\x00\x00\x00")), 1,
                        int_id(st.get("TGID", b"\x00\x00\x00")),
                        max(0.0, now - start),
                    )
                )
                if trx == "RX":
                    self._obp_emit_end_tx_for_forward_legs(stream_id, system_name, now)
            return

        for slot in (1, 2):
            slot_st = status.get(slot)
            if not isinstance(slot_st, dict):
                continue
            for trx, sid_key, peer_key, rfs_key, tgid_key, start_key, type_key in (
                ("RX", "RX_STREAM_ID", "RX_PEER", "RX_RFS", "RX_TGID", "RX_START", "RX_TYPE"),
                ("TX", "TX_STREAM_ID", "TX_PEER", "TX_RFS", "TX_TGID", "TX_START", "TX_TYPE"),
            ):
                sid = slot_st.get(sid_key, b"\x00")
                if sid in (b"\x00", b"") or slot_st.get(type_key) == HBPF_SLT_VTERM:
                    continue
                self._send_routing_event(
                    "GROUP VOICE,END,{},{},{},{},{},{},{},{:.2f}".format(
                        trx, system_name, int_id(sid),
                        int_id(slot_st.get(peer_key, b"\x00\x00\x00\x00")),
                        int_id(slot_st.get(rfs_key, b"\x00\x00\x00")), slot,
                        int_id(slot_st.get(tgid_key, b"\x00\x00\x00")),
                        max(0.0, now - slot_st.get(start_key, now)),
                    )
                )

    def stream_trimmer_loop(self) -> None:
        """Trim old stream state (legacy stream_trimmer_loop, 5s). RX/TX timeout per system/slot; OBP streams (legacy bridge.py 181-240)."""
        logger.debug("(ROUTER) Trimming inactive stream IDs from system lists")
        protocols = self._get_protocols() if self._get_protocols else {}
        systems_cfg = self._config.get("SYSTEMS", {})
        now = time.time()
        for system_name, protocol in protocols.items():
            if not getattr(protocol, "STATUS", None):
                continue
            # OBP: legacy bridge_master.stream_trimmer_loop:631-703 — two-stage lifecycle:
            # Stage 1 (5s idle, no _to, no _fin): set _to=True, emit END,RX, continue.
            # Stage 2 (180s idle): remove stream entry.
            #
            # Legacy parity: routerOBP.STATUS is a *flat* dict keyed only by stream_id
            # (bridge_master.py:1911). The loop iterates `for stream_id in systems[s].STATUS:`,
            # which automatically catches everything seeded by to_target HBP->OBP,
            # sendDataToOBP, pvt_call_received and the OBP source path itself. We do
            # not keep a parallel dict -- that was a divergence that produced leaks.
            if systems_cfg.get(system_name, {}).get("MODE") == "OPENBRIDGE":
                obp_status = getattr(protocol, "STATUS", None)
                if isinstance(obp_status, dict) and obp_status:
                    to_remove: list[bytes] = []
                    for stream_id, st in list(obp_status.items()):
                        if not isinstance(st, dict):
                            continue
                        last = st.get("LAST", 0)
                        # Stage 2: finished streams older than 180s → remove
                        if st.get("_fin") and last < now - 180:
                            to_remove.append(stream_id)
                            continue
                        # Stage 2: timed-out streams older than 180s → remove
                        if st.get("_to") and last < now - 180:
                            to_remove.append(stream_id)
                            continue
                        # Stage 1: 5s idle — monitor END only on ingress (canonical RX).
                        # Forward legs (H_LC): no END,RX and no cross-leg END,TX cascade.
                        if "_to" not in st and "_fin" not in st and last < now - 5:
                            if _obp_status_is_forward_leg(st):
                                if st.get("_end_tx_sent") or st.get("_bcsq_quenched"):
                                    st["_to"] = True
                                continue
                            rfs = st.get("RFS", b"\x00\x00\x00")
                            peer = st.get("RX_PEER", b"\x00\x00\x00\x00")
                            tgid = st.get("TGID", b"\x00\x00\x00")
                            start = st.get("START", now)
                            duration = max(0.0, last - start)
                            self._send_routing_event(
                                "GROUP VOICE,END,RX,{},{},{},{},{},{},{:.2f}".format(
                                    system_name, int_id(stream_id), int_id(peer), int_id(rfs), 1, int_id(tgid), duration
                                )
                            )
                            bridge = self._voice_plugin_bridge
                            if st.get("_plugin_voice") and bridge is not None:
                                bridge.emit_group_voice_end(
                                    system_name=system_name,
                                    peer_id=peer,
                                    rf_src=rfs,
                                    dst_id=tgid,
                                    slot=1,
                                    stream_id=stream_id,
                                    pkt_time=last,
                                    duration_s=duration,
                                    source_is_obp=True,
                                )
                                st.pop("_plugin_voice", None)
                            st["_to"] = True
                            self._obp_emit_end_tx_for_forward_legs(stream_id, system_name, now)
                            continue
                    for stream_id in to_remove:
                        self._obp_session(system_name).release_stream(stream_id)
                        st_rem = obp_status.get(stream_id)
                        if isinstance(st_rem, dict) and _obp_status_is_forward_leg(st_rem):
                            self._obp_emit_end_tx_forward_leg(system_name, stream_id, st_rem, now)
                        elif isinstance(st_rem, dict) and _obp_status_is_ingress(st_rem):
                            self._obp_emit_end_tx_for_forward_legs(stream_id, system_name, now)
                        obp_status.pop(stream_id, None)
                continue
            for slot in (1, 2):
                _slot = protocol.STATUS.get(slot)
                if not _slot:
                    continue
                if _slot.get("RX_TYPE") != HBPF_SLT_VTERM and _slot.get("RX_TIME", 0) < now - 5:
                    _slot["RX_TYPE"] = HBPF_SLT_VTERM
                    logger.info(
                        "(%s) *TIME OUT*  RX STREAM ID: %s SUB: %s TGID %s, TS %s, Duration: %.2f",
                        system_name, int_id(_slot.get("RX_STREAM_ID", b"")), int_id(_slot.get("RX_RFS", b"")),
                        int_id(_slot.get("RX_TGID", b"")), slot, _slot.get("RX_TIME", 0) - _slot.get("RX_START", 0),
                    )
                    self._send_routing_event(
                        "GROUP VOICE,END,RX,{},{},{},{},{},{},{:.2f}".format(
                            system_name, int_id(_slot.get("RX_STREAM_ID", b"")), int_id(_slot.get("RX_PEER", b"")),
                            int_id(_slot.get("RX_RFS", b"")), slot, int_id(_slot.get("RX_TGID", b"")),
                            _slot.get("RX_TIME", 0) - _slot.get("RX_START", 0),
                        )
                    )
                    bridge = self._voice_plugin_bridge
                    if bridge is not None:
                        bridge.emit_group_voice_end(
                            system_name=system_name,
                            peer_id=_slot.get("RX_PEER", b"\x00\x00\x00\x00"),
                            rf_src=_slot.get("RX_RFS", b"\x00\x00\x00"),
                            dst_id=_slot.get("RX_TGID", b"\x00\x00\x00"),
                            slot=slot,
                            stream_id=_slot.get("RX_STREAM_ID", b"\x00"),
                            pkt_time=_slot.get("RX_TIME", now),
                            duration_s=_slot.get("RX_TIME", 0) - _slot.get("RX_START", 0),
                            source_is_obp=False,
                        )
                if _slot.get("RX_TIME", 0) < now - 60:
                    _slot["RX_STREAM_ID"] = b"\x00"
                tx_streams = _slot.get("TX_STREAMS")
                if isinstance(tx_streams, dict):
                    for sid, leg in list(tx_streams.items()):
                        if not isinstance(leg, dict):
                            continue
                        if leg.get("TX_TYPE") != HBPF_SLT_VTERM and leg.get("TX_TIME", 0) < now - 5:
                            logger.debug(
                                "(%s) *TIME OUT*  TX STREAM ID: %s SUB: %s TGID %s, TS %s, Duration: %.2f",
                                system_name, int_id(sid), int_id(leg.get("TX_RFS", b"")),
                                int_id(leg.get("TX_TGID", b"")), slot,
                                leg.get("TX_TIME", 0) - leg.get("TX_START", 0),
                            )
                            self._send_routing_event(
                                "GROUP VOICE,END,TX,{},{},{},{},{},{},{:.2f}".format(
                                    system_name, int_id(sid), int_id(leg.get("TX_PEER", b"")),
                                    int_id(leg.get("TX_RFS", b"")), slot, int_id(leg.get("TX_TGID", b"")),
                                    leg.get("TX_TIME", 0) - leg.get("TX_START", 0),
                                )
                            )
                            obp_clear_deferred_bridge_tx_leg(_slot, sid, now)
                if _slot.get("TX_TYPE") != HBPF_SLT_VTERM and _slot.get("TX_TIME", 0) < now - 5:
                    _slot["TX_TYPE"] = HBPF_SLT_VTERM
                    logger.debug(
                        "(%s) *TIME OUT*  TX STREAM ID: %s SUB: %s TGID %s, TS %s, Duration: %.2f",
                        system_name, int_id(_slot.get("TX_STREAM_ID", b"")), int_id(_slot.get("TX_RFS", b"")),
                        int_id(_slot.get("TX_TGID", b"")), slot, _slot.get("TX_TIME", 0) - _slot.get("TX_START", 0),
                    )
                    self._send_routing_event(
                        "GROUP VOICE,END,TX,{},{},{},{},{},{},{:.2f}".format(
                            system_name, int_id(_slot.get("TX_STREAM_ID", b"")), int_id(_slot.get("TX_PEER", b"")),
                            int_id(_slot.get("TX_RFS", b"")), slot, int_id(_slot.get("TX_TGID", b"")),
                            _slot.get("TX_TIME", 0) - _slot.get("TX_START", 0),
                        )
                    )
            # -- Intentional divergence from legacy --
            # Legacy bridge_master.py:602 only iterates `range(1,3)` in the HBP branch,
            # so any STATUS[stream_id] entry seeded by voice_use_cases (announcements
            # via sys_obj.STATUS[stream_id], cf. bridge_master.py:1156/1626) or by
            # send_voice_packet against HBP MASTER targets is never freed in legacy.
            # Here we defensively sweep any bytes-keyed entry with LAST > 180 s.
            # This does not change the wire protocol; it only releases memory.
            hbp_status = getattr(protocol, "STATUS", None)
            if isinstance(hbp_status, dict):
                for _k in list(hbp_status.keys()):
                    if isinstance(_k, (bytes, bytearray)):
                        _v = hbp_status.get(_k)
                        if isinstance(_v, dict) and _v.get("LAST", 0) < now - 180:
                            hbp_status.pop(_k, None)
            if hasattr(protocol, "trim_dmra_streams"):
                protocol.trim_dmra_streams()

    def subscription_reset_loop(self) -> None:
        """Bridge reset iteration (legacy bridge_reset, 6s). Clear _reset and remove_bridge_system."""
        systems_cfg = self._config.get("SYSTEMS", {})
        from ..subscription.subscription_reset_ops import (
            deactivate_system_legs_store,
            restore_prohibited_static_legs_store,
        )

        now = time.time()
        for system_name in list(systems_cfg.keys()):
            sys_cfg = systems_cfg.get(system_name, {})
            if not sys_cfg.get("_reset"):
                continue
            logger.info("(BRIDGERESET) Bridge reset for %s - no peers", system_name)
            deactivate_system_legs_store(self._subscription_store, system_name, now)
            sys_cfg.pop("_opt_key", None)
            sys_cfg.pop("_options_static_apply_fp", None)
            restore_prohibited_static_legs_store(
                self._subscription_store,
                system_name,
                sys_cfg,
                self.acl_check,
                now,
            )
            sys_cfg["_reset"] = False
            sys_cfg["_resetlog"] = False
    def _restore_prohibited_static_bridge_legs(self, system_name: str) -> None:
        """After BRIDGERESET / peer RPTO: restore static TGs in prohibited_tgs (parity with _seed_echo_routing_table)."""
        sys_cfg = self._config.get("SYSTEMS", {}).get(system_name, {})
        if sys_cfg.get("MODE") != "MASTER" or not sys_cfg.get("ENABLED", True):
            return
        from ..subscription.subscription_reset_ops import restore_prohibited_static_legs_store
        restore_prohibited_static_legs_store(
            self._subscription_store,
            system_name,
            sys_cfg,
            self.acl_check,
            time.time(),
        )
    def stat_trimmer_loop(self) -> None:
        """Trim STAT-only bridges with no ON/OFF in use (legacy statTrimmer, 303s)."""
        logger.debug("(ROUTER) STAT trimmer loop started")
        from ..subscription.stat_trimmer_ops import apply_stat_trimmer_store

        apply_stat_trimmer_store(self._subscription_store)
