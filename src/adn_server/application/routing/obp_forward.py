# ADN DMR Peer Server - bridge OBP forward path
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

"""OpenBridge ingress routing and unit-data forward (no Twisted imports)."""

from __future__ import annotations

import logging
import time
from hashlib import blake2b
from time import perf_counter
from typing import Any

from ...domain import HBPF_DATA_SYNC, HBPF_SLT_VHEAD, int_id
from ...domain.dmr import decode
from ...domain.dmr.const import LC_OPT
from ..subscription.obp_source_ops import ensure_obp_source_for_tg_store, obp_source_needs_ensure
from ..subscription.subscription_queries import store_has_table
from .helpers import group_voice_tg_ingress_collision, obp_is_canonical_ingress, unit_data_reportable

logger = logging.getLogger(__name__)

# Idle time before another destination may take over a stream_id. The trimmer only
# drops a row after 180s; well above one voice frame so interleaved streams keep theirs.
OBP_REUSED_STREAM_IDLE_S = 2.0


class ObpForwardMixin:
    """routerOBP group voice, stream tracking, sendDataToOBP."""

    def _ensure_obp_source_for_tg(
        self,
        system_name: str,
        relay_table_key: str,
        dst_id_b: bytes,
        dst_int: int,
    ) -> None:
        """Ensure this OBP has an ACTIVE source row for TG (TS1) in main and #reflector bridges.

        remove_bridge_system / BRIDGERESET sets all rows for a system to ACTIVE False. Local MASTER
        traffic still matches MASTER source rows; inbound OBP traffic needs these OBP rows re-enabled
        or added (e.g. new OBP in config after bridge was built).
        Same TG range as ensure_dynamic_relay OBP entries.
        """
        systems_cfg = self._config.get("SYSTEMS", {})
        if systems_cfg.get(system_name, {}).get("MODE") != "OPENBRIDGE":
            return
        if not systems_cfg.get(system_name, {}).get("ENABLED", True):
            return
        if not (79 <= dst_int < 9990 or dst_int > 9999):
            return
        store = self._subscription_store
        if not obp_source_needs_ensure(store, system_name, relay_table_key, dst_int):
            return
        ensure_obp_source_for_tg_store(
            store,
            system_name,
            relay_table_key,
            dst_id_b,
            dst_int,
            time.time(),
        )
    def _obp_wire_stream_dict(self, src_proto: Any, stream_id: bytes, st: dict[str, Any]) -> None:
        """Legacy routerOBP.STATUS is a single flat dict keyed by stream_id (bridge_master.py:1911).
        Write only there; trimmer iterates the same dict (parity)."""
        status = getattr(src_proto, "STATUS", None)
        if status is not None:
            status[stream_id] = st

    def _is_stream_known(self, system_name: str, stream_id: bytes, slot: int = 0) -> bool:
        """Return True if *stream_id* is already tracked.

        Legacy routerHBP (bridge_master.py ~3054): ``_stream_id != STATUS[_slot]['RX_STREAM_ID']``
        Legacy routerOBP (bridge_master.py ~2193): ``_stream_id not in self.STATUS``

        For HBP (MASTER/PEER) we compare against the per-slot ``RX_STREAM_ID``.
        For OBP we check membership in the flat ``STATUS`` dict — keyed only by
        stream_id since routerOBP.__init__ does ``self.STATUS = {}`` (legacy
        bridge_master.py:1911); slot-keyed entries do not exist on OBP protocols.
        """
        systems_cfg = self._config.get("SYSTEMS", {})
        sys_mode = systems_cfg.get(system_name, {}).get("MODE", "")
        if sys_mode == "OPENBRIDGE":
            protocols = self._get_protocols() if self._get_protocols else {}
            proto = protocols.get(system_name)
            if proto is None:
                return False
            status = getattr(proto, "STATUS", None)
            if status is None:
                return False
            return stream_id in status
        protocols = self._get_protocols() if self._get_protocols else {}
        proto = protocols.get(system_name)
        if proto is None:
            return False
        status = getattr(proto, "STATUS", None)
        if status is None or not isinstance(status, dict):
            return False
        slot_st = status.get(slot)
        if not isinstance(slot_st, dict):
            return False
        return slot_st.get("RX_STREAM_ID") == stream_id

    def _obp_group_voice_router_obp(
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
        obp_hops: bytes,
        synthetic_announcement: bool = False,
    ) -> bool:
        """Port of bridge_master.routerOBP.dmrd_received group/vcsbk (~2269-2411). False = drop packet."""
        pkt_time = time.time()
        dmrpkt = data[20:53] if len(data) >= 53 else b""
        _h = blake2b(digest_size=16)
        _h.update(data)
        _pkt_crc = _h.digest()
        protocols = self._get_protocols() if self._get_protocols else {}
        src_proto = protocols.get(system_name) if protocols else None
        if not src_proto:
            return True
        systems_cfg = self._config.get("SYSTEMS", {})
        _do_report = bool(self._config.get("REPORTS", {}).get("REPORT", True))
        status = getattr(src_proto, "STATUS", None)
        if status is None:
            return True

        # A peer reusing a stream_id for another destination is starting a new call,
        # not continuing the one the row describes. to_target already evicts on a
        # forward leg; without the same here loop control refuses the new call.
        _prev = status.get(stream_id)
        if isinstance(_prev, dict) and _prev.get("TGID") != dst_id:
            _idle = pkt_time - _prev.get("LAST", _prev.get("START", pkt_time))
            if _idle >= OBP_REUSED_STREAM_IDLE_S:
                logger.info(
                    "(%s) stream %s reused for TG %s (was TG %s, idle %.1fs): starting a new call",
                    system_name,
                    int_id(stream_id),
                    int_id(dst_id),
                    int_id(_prev.get("TGID", b"\x00\x00\x00")),
                    _idle,
                )
                del status[stream_id]

        if stream_id not in status:
            if group_voice_tg_ingress_collision(
                protocols, systems_cfg, dst_id, stream_id, rf_src, pkt_time,
            ):
                self._log_ingress_warning_once(
                    self._ingress_drop_key(
                        "tg_busy", system_name, peer_id, dst_id, stream_id,
                    ),
                    "(%s) TG %s busy — dropping OBP stream %s from peer %s",
                    system_name, int_id(dst_id), int_id(stream_id), int_id(peer_id),
                )
                return False
            st: dict[str, Any] = {
                "START": pkt_time,
                "CONTENTION": False,
                "RFS": rf_src,
                "TGID": dst_id,
                "1ST": perf_counter(),
                "lastSeq": False,
                "lastData": False,
                "RX_PEER": peer_id,
                "packets": 0,
                "loss": 0,
                "crcs": set(),
            }
            if frame_type == HBPF_DATA_SYNC and dtype_vseq == HBPF_SLT_VHEAD:
                try:
                    decoded = decode.voice_head_term(dmrpkt)
                    st["LC"] = decoded["LC"]
                except Exception:
                    st["LC"] = LC_OPT + dst_id + rf_src
            else:
                st["LC"] = LC_OPT + dst_id + rf_src
            self._obp_wire_stream_dict(src_proto, stream_id, st)
            _inthops = int.from_bytes(obp_hops, "big") if obp_hops else 0
            logger.info(
                "(%s) *CALL START* STREAM ID: %s SUB: %s PEER: %s TGID %s TS %s HOPS %s",
                system_name,
                int_id(stream_id),
                int_id(rf_src),
                int_id(peer_id),
                int_id(dst_id),
                slot,
                _inthops,
            )
            # INGRESS: debug-only (all OBP legs); monitor logs it but does not update OPENBRIDGES chips until START.
            if _do_report:
                self._send_routing_event(
                    "GROUP VOICE,INGRESS,RX,{},{},{},{},{},{},0".format(
                        system_name, int_id(stream_id), int_id(peer_id), int_id(rf_src), slot, int_id(dst_id)
                    )
                )
            bridge = self._voice_plugin_bridge
            if bridge is not None and bridge.has_subscribers() and obp_is_canonical_ingress(
                protocols, systems_cfg, system_name, stream_id, dst_id, rf_src,
            ) and bridge.emit_group_voice_start(
                    system_name=system_name,
                    peer_id=peer_id,
                    rf_src=rf_src,
                    dst_id=dst_id,
                    slot=slot,
                    stream_id=stream_id,
                    pkt_time=pkt_time,
                    source_is_obp=True,
                    obp_hops=obp_hops if obp_hops else b"",
                    synthetic_announcement=synthetic_announcement,
                    voice_phase="INGRESS",
                ):
                status[stream_id]["_plugin_voice"] = True
        else:
            st = status[stream_id]
            if "packets" in st:
                st["packets"] = st["packets"] + 1
            if "_fin" in st:
                if "_finlog" not in st:
                    logger.debug(
                        "(%s) OBP *LoopControl* STREAM ID: %s ALREADY FINISHED FROM THIS SOURCE, IGNORING",
                        system_name,
                        int_id(stream_id),
                    )
                st["_finlog"] = True
                return False
            if st["START"] + 180 < pkt_time:
                if "LOOPLOG" not in st or not st["LOOPLOG"]:
                    logger.info(
                        "(%s) OBP *TIMEOUT*, STREAM ID: %s, TG: %s, IGNORE THIS SOURCE",
                        system_name,
                        int_id(stream_id),
                        int_id(dst_id),
                    )
                    st["LOOPLOG"] = True
                st["LAST"] = pkt_time
                return False

            # Legacy routerOBP ~2409: LoopControl only on 2nd+ packet (else branch).
            hr_times: dict[str, float] = {}
            for other_name, proto in (protocols or {}).items():
                omode = systems_cfg.get(other_name, {}).get("MODE")
                if other_name != system_name and omode != "OPENBRIDGE":
                    ostatus = getattr(proto, "STATUS", None)
                    if not ostatus:
                        continue
                    for _sysslot in ostatus:
                        slot_st = ostatus.get(_sysslot)
                        if not isinstance(slot_st, dict):
                            continue
                        if "RX_STREAM_ID" in slot_st and stream_id == slot_st.get("RX_STREAM_ID"):
                            if "LOOPLOG" not in st or not st["LOOPLOG"]:
                                logger.debug(
                                    "(%s) OBP *LoopControl* FIRST HBP: %s, STREAM ID: %s, TG: %s, TS: %s, IGNORE THIS SOURCE",
                                    system_name,
                                    other_name,
                                    int_id(stream_id),
                                    int_id(dst_id),
                                    _sysslot,
                                )
                                st["LOOPLOG"] = True
                            st["LAST"] = pkt_time
                            return False
                else:
                    obp_status = getattr(proto, "STATUS", None)
                    if not obp_status:
                        continue
                    if (
                        stream_id in obp_status
                        and "1ST" in obp_status[stream_id]
                        and obp_status[stream_id].get("TGID") == dst_id
                    ):
                        hr_times[other_name] = obp_status[stream_id]["1ST"]

            fi = min(hr_times, key=hr_times.get, default=False)
            if not fi:
                if not st.get("LOOPLOG"):
                    logger.warning(
                        "(%s) OBP *LoopControl* no bridge holds stream %s for TG %s, dropping",
                        system_name,
                        int_id(stream_id),
                        int_id(dst_id),
                    )
                    st["LOOPLOG"] = True
                st["LAST"] = pkt_time
                return False
            if system_name != fi:
                if "LOOPLOG" not in st or not st["LOOPLOG"]:
                    call_duration = pkt_time - st["START"]
                    logger.debug(
                        "(%s) OBP *LoopControl* FIRST OBP %s, STREAM ID: %s, TG %s, IGNORE THIS SOURCE. PACKET RATE %0.2f/s",
                        system_name,
                        fi,
                        int_id(stream_id),
                        int_id(dst_id),
                        call_duration,
                    )
                    st["LOOPLOG"] = True
                    if _do_report:
                        self._send_routing_event(
                            "GROUP VOICE,END,TX,{},{},{},{},{},{},{:.2f}".format(
                                system_name,
                                int_id(stream_id),
                                int_id(peer_id),
                                int_id(rf_src),
                                slot,
                                int_id(dst_id),
                                max(0.0, pkt_time - st.get("START", pkt_time)),
                            )
                        )
                st["LAST"] = pkt_time
                if systems_cfg.get(system_name, {}).get("ENHANCED_OBP") and self._send_bcsq and "_bcsq" not in st:
                    self._send_bcsq(system_name, dst_id, stream_id)
                    st["_bcsq"] = True
                return False

        st = status[stream_id]
        # Legacy skips packet control on the first frame of a stream (else branch only on 2nd+ packet).
        if st.get("packets", 0) > 0:
            # Legacy routerOBP ~2452: packets/START (START is epoch time, not elapsed) — never triggers in practice.
            if st["packets"] > 18 and (st["packets"] / st["START"]) > 25:
                logger.warning(
                    "(%s) *PacketControl* RATE DROP! Stream ID:, %s TGID: %s",
                    system_name,
                    int_id(stream_id),
                    int_id(dst_id),
                )
                pb = getattr(src_proto, "proxy_bad_peer", None)
                if callable(pb):
                    pb()
                return False

            if st["lastData"] and st["lastData"] == data and seq > 1:
                st["loss"] += 1
                logger.debug(
                    "(%s) *PacketControl* last packet is a complete duplicate of the previous one, disgarding. Stream ID:, %s TGID: %s, LOSS: %.2f%%",
                    system_name,
                    int_id(stream_id),
                    int_id(dst_id),
                    ((st["loss"] / st["packets"]) * 100) if st.get("packets") else 0.0,
                )
                return False
            if seq and seq == st["lastSeq"]:
                st["loss"] += 1
                logger.debug(
                    "(%s) *PacketControl* Duplicate sequence number %s, disgarding. Stream ID:, %s TGID: %s, LOSS: %.2f%%",
                    system_name,
                    seq,
                    int_id(stream_id),
                    int_id(dst_id),
                    ((st["loss"] / st["packets"]) * 100) if st.get("packets") else 0.0,
                )
                return False
            if seq and st["lastSeq"] and (seq != 1) and (seq < st["lastSeq"]):
                st["loss"] += 1
                logger.debug(
                    "(%s) *PacketControl* Out of order packet - last SEQ: %s, this SEQ: %s,  disgarding. Stream ID:, %s TGID: %s, LOSS: %.2f%%",
                    system_name,
                    st["lastSeq"],
                    seq,
                    int_id(stream_id),
                    int_id(dst_id),
                    ((st["loss"] / st["packets"]) * 100) if st.get("packets") else 0.0,
                )
                return False
            if seq > 0 and _pkt_crc in st["crcs"]:
                st["loss"] += 1
                logger.debug(
                    "(%s) *PacketControl* DMR packet payload with hash: %s seen before in this stream, disgarding. Stream ID:, %s TGID: %s: SEQ:%s PACKETS: %s, LOSS: %.2f%% ",
                    system_name,
                    _pkt_crc,
                    int_id(stream_id),
                    int_id(dst_id),
                    seq,
                    st["packets"],
                    ((st["loss"] / st["packets"]) * 100) if st.get("packets") else 0.0,
                )
                return False
            if seq and st["lastSeq"] and seq > (st["lastSeq"] + 1):
                st["loss"] += 1
                logger.debug(
                    "(%s) *PacketControl* Missed packet(s) - last SEQ: %s, this SEQ: %s. Stream ID:, %s TGID: %s , LOSS: %.2f%%",
                    system_name,
                    st["lastSeq"],
                    seq,
                    int_id(stream_id),
                    int_id(dst_id),
                    ((st["loss"] / st["packets"]) * 100) if st.get("packets") else 0.0,
                )
            st["lastSeq"] = seq
            st["lastData"] = data

        # Canonical START,RX for monitor CTABLE (OpenBridge / Linked / Active QSO) after loop win; INGRESS was debug-only.
        if _do_report:
            if not st.get("_monitor_canonical_rx"):
                self._send_routing_event(
                    "GROUP VOICE,START,RX,{},{},{},{},{},{}".format(
                        system_name, int_id(stream_id), int_id(peer_id), int_id(rf_src), slot, int_id(dst_id)
                    )
                )
                st["_monitor_canonical_rx"] = True

        st = status[stream_id]
        st["crcs"].add(_pkt_crc)
        st["LAST"] = pkt_time

        if self._config.get("GLOBAL", {}).get("GEN_STAT_BRIDGES"):
            _di = int_id(dst_id)
            _bk = str(_di)
            if _di >= 5 and _di != 9 and not store_has_table(self._subscription_store, _bk):
                logger.debug("(%s) Bridge for STAT TG %s does not exist. Creating", system_name, _di)
                self.ensure_stat_relay(dst_id)
                self.apply_static_tg_to_bridge(_di)
        return True

    def _send_data_to_obp(
        self,
        source_system: str,
        target: str,
        data: bytes,
        dmrpkt: bytes,
        pkt_time: float,
        stream_id: bytes,
        dst_id: bytes,
        peer_id: bytes,
        rf_src: bytes,
        bits: int,
        slot: int,
        hops: bytes = b"",
        ber: bytes = b"\x00",
        rssi: bytes = b"\x00",
        source_server: bytes = b"\x00\x00\x00\x00",
        source_rptr: bytes = b"\x00\x00\x00\x00",
    ) -> None:
        """Legacy sendDataToOBP: forward a unit-data packet to an OPENBRIDGE target."""
        systems_cfg = self._config.get("SYSTEMS", {})
        _target_system = systems_cfg.get(target, {})
        if _target_system.get("ENHANCED_OBP") and self._obp_session(target).keepalive_stale(pkt_time):
            return
        protocols = self._get_protocols() if self._get_protocols else {}
        target_proto = protocols.get(target)
        if not target_proto:
            return
        _target_status = getattr(target_proto, "STATUS", {})
        if stream_id not in _target_status:
            _target_status[stream_id] = {
                "START": pkt_time,
                "CONTENTION": False,
                "RFS": rf_src,
                "TGID": dst_id,
                "RX_PEER": peer_id,
                "packets": 0,
            }
        _target_status[stream_id]["LAST"] = pkt_time
        _tmp_bits = bits ^ (1 << 7) if slot == 2 else bits
        _tmp_data = b"".join([data[:15], _tmp_bits.to_bytes(1, "big"), data[16:20], dmrpkt])
        try:
            self._send_to_system(target, _tmp_data, _hops=hops, _ber=ber, _rssi=rssi, _source_server=source_server, _source_rptr=source_rptr)
        except Exception as exc:
            logger.warning("(%s) send_data_to_obp %s failed: %s", source_system, target, exc)
            return
        logger.debug("(%s) UNIT Data Bridged to OBP System: %s DST_ID: %s", source_system, target, int_id(dst_id))
        _dtype_vseq = data[15] & 0xF if len(data) > 15 else 0
        if unit_data_reportable(_dtype_vseq):
            self._send_routing_event(
                "UNIT DATA,DATA,TX,{},{},{},{},{},{}".format(
                    target, int_id(stream_id), int_id(peer_id), int_id(rf_src), 1, int_id(dst_id),
                )
            )
