# ADN DMR Peer Server - bridge HBP forward path
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

"""HBP ingress packet control and unit-data forward (no Twisted imports)."""

from __future__ import annotations

import logging
from hashlib import blake2b

from ...domain import bytes_4, int_id
from .helpers import (
    group_voice_tg_ingress_collision,
    hbp_ingress_downlink_session_blocks_tx,
    hbp_ingress_new_stream_collision,
    master_per_peer_slot_contention,
    tg_has_active_conversation,
    unit_data_reportable,
)
from .peer_downlink_index import count_connected_peers

logger = logging.getLogger(__name__)


class HbpForwardMixin:
    """routerHBP group voice ingress controls and sendDataToHBP."""

    def _ingress_drop_log_cache(self) -> set[tuple]:
        cache = getattr(self, "_ingress_drop_logged", None)
        if cache is None:
            cache = set()
            self._ingress_drop_logged = cache
        return cache

    def _ingress_drop_key(
        self,
        kind: str,
        system_name: str,
        peer_id: bytes,
        dst_id: bytes,
        stream_id: bytes,
        *,
        slot: int | None = None,
    ) -> tuple:
        key: tuple = (
            kind,
            system_name,
            bytes_4(int_id(peer_id)),
            dst_id,
            stream_id,
        )
        if slot is not None:
            return (*key, int(slot))
        return key

    def _log_ingress_warning_once(
        self,
        key: tuple,
        msg: str,
        *args: object,
    ) -> None:
        cache = self._ingress_drop_log_cache()
        if key in cache:
            return
        cache.add(key)
        logger.warning(msg, *args)

    def _silent_activation_log_key(
        self,
        system_name: str,
        peer_id: bytes,
        dst_id: bytes,
    ) -> tuple:
        return ("silent_activation", system_name, bytes_4(int_id(peer_id)), dst_id)

    def _log_ingress_info_once(
        self,
        key: tuple,
        msg: str,
        *args: object,
    ) -> None:
        cache = self._ingress_drop_log_cache()
        if key in cache:
            return
        cache.add(key)
        logger.info(msg, *args)

    def _clear_silent_activation_log(
        self,
        system_name: str,
        peer_id: bytes,
        dst_id: bytes,
    ) -> None:
        self._ingress_drop_log_cache().discard(
            self._silent_activation_log_key(system_name, peer_id, dst_id),
        )

    def _clear_ingress_drop_log(
        self,
        system_name: str,
        peer_id: bytes,
        dst_id: bytes,
        stream_id: bytes,
        slot: int,
    ) -> None:
        cache = self._ingress_drop_log_cache()
        pk = bytes_4(int_id(peer_id))
        tg = dst_id
        sid = stream_id
        sl = int(slot)
        for kind in ("slot_collision", "tg_busy", "downlink_tx"):
            cache.discard((kind, system_name, pk, tg, sid))
            cache.discard((kind, system_name, pk, tg, sid, sl))

    def _hbp_group_voice_ingress_controls(
        self,
        system_name: str,
        peer_id: bytes,
        rf_src: bytes,
        dst_id: bytes,
        seq: int,
        slot: int,
        stream_id: bytes,
        data: bytes,
        pkt_time: float,
    ) -> bool:
        """Legacy routerHBP group/vcsbk packet control (~3270-3399).

        Returns True when the packet may proceed to bridge routing; False when dropped.
        Uses ingress ``pkt_time`` (UDP receive time) for rate/timeout parity with legacy.
        """
        protocols = self._get_protocols() if self._get_protocols else {}
        src_proto = protocols.get(system_name)
        if not src_proto:
            return True
        systems_cfg = self._config.get("SYSTEMS", {})
        _slot_st = getattr(src_proto, "STATUS", {}).get(slot, {})
        _is_new_stream = stream_id != _slot_st.get("RX_STREAM_ID")
        if _is_new_stream:
            _slot_st.pop("_suppress_uplink", None)
            _slot_st.pop("_silent_activation_tg", None)
            sys_cfg = systems_cfg.get(system_name, {})
            peers = getattr(src_proto, "_peers", None) or sys_cfg.get("PEERS", {})
            connected = count_connected_peers(peers) if isinstance(peers, dict) else 0
            per_peer = master_per_peer_slot_contention(
                self._config, system_name, sys_cfg, connected_count=connected,
            )
            if hbp_ingress_new_stream_collision(
                _slot_st, peer_id, rf_src, stream_id, pkt_time, per_peer=per_peer,
            ):
                self._log_ingress_warning_once(
                    self._ingress_drop_key(
                        "slot_collision", system_name, peer_id, dst_id, stream_id, slot=slot,
                    ),
                    "(%s) Packet received with STREAM ID: %s <FROM> SUB: %s PEER: %s <TO> TGID %s, SLOT %s collided with existing call",
                    system_name, int_id(stream_id), int_id(rf_src), int_id(peer_id), int_id(dst_id), slot,
                )
                return False
            if group_voice_tg_ingress_collision(
                protocols, systems_cfg, dst_id, stream_id, rf_src, pkt_time,
            ):
                # TX onto a TG with an active (in-progress) QSO is not rejected.
                # The TG is activated dynamically, the user's uplink audio is suppressed
                # (not forwarded to the network), and the downlink of the active QSO is
                # delivered to the user. Only a TG that is merely in GROUP_HANGTIME
                # (no live stream) is rejected as busy (legacy parity).
                if tg_has_active_conversation(
                    protocols, systems_cfg, dst_id, stream_id, rf_src, pkt_time,
                ):
                    self._log_ingress_info_once(
                        self._silent_activation_log_key(system_name, peer_id, dst_id),
                        "(%s) TG %s has active QSO — activating dynamic TG silently for peer %s (uplink suppressed)",
                        system_name, int_id(dst_id), int_id(peer_id),
                    )
                    _slot_st["_suppress_uplink"] = True
                    _slot_st["_silent_activation_tg"] = int_id(dst_id)
                    # Bind stream early so voice frames do not re-enter the new-stream
                    # path and spam silent-activation logs (udp_hbp parity).
                    _slot_st["RX_STREAM_ID"] = stream_id
                else:
                    self._log_ingress_warning_once(
                        self._ingress_drop_key(
                            "tg_busy", system_name, peer_id, dst_id, stream_id,
                        ),
                        "(%s) TG %s busy — dropping stream %s from peer %s",
                        system_name, int_id(dst_id), int_id(stream_id), int_id(peer_id),
                    )
                    return False
            from .downlink import normalize_ua_voice_slot

            peer = peers.get(peer_id) if isinstance(peers, dict) else None
            if peer is None and isinstance(peers, dict):
                peer = peers.get(bytes_4(int_id(peer_id)))
            voice_slot = normalize_ua_voice_slot(peer, slot) if isinstance(peer, dict) else slot
            peer_slots = getattr(src_proto, "_peer_voice_slots", {}).get(bytes_4(int_id(peer_id)))
            if hbp_ingress_downlink_session_blocks_tx(
                voice_slot, dst_id, peer_slots,
            ):
                self._log_ingress_warning_once(
                    self._ingress_drop_key(
                        "downlink_tx", system_name, peer_id, dst_id, stream_id, slot=voice_slot,
                    ),
                    "(%s) Packet dropped: peer %s already receiving on TG %s slot %s",
                    system_name, int_id(peer_id), int_id(dst_id), voice_slot,
                )
                return False
            self._clear_ingress_drop_log(system_name, peer_id, dst_id, stream_id, slot)
            _slot_st["packets"] = 0
            _slot_st["loss"] = 0
            _slot_st["crcs"] = set()
            _slot_st["LOOPLOG"] = False
            _slot_st.pop("_bcsq", None)
            _slot_st["lastSeq"] = False
            _slot_st["lastData"] = False
            _slot_st["RX_START"] = pkt_time
        if _slot_st.get("lastData") and _slot_st["lastData"] == data and seq > 1:
            _slot_st["loss"] = _slot_st.get("loss", 0) + 1
            logger.debug(
                "(%s) *PacketControl* last packet is a complete duplicate, discarding. Stream ID: %s TGID: %s",
                system_name, int_id(stream_id), int_id(dst_id),
            )
            return False
        _slot_st["packets"] = _slot_st.get("packets", 0) + 1
        _pkts = _slot_st["packets"]
        _rx_start = _slot_st.get("RX_START", pkt_time)
        if _pkts > 18 and _rx_start < pkt_time:
            _rate = _pkts / (pkt_time - _rx_start)
            if _rate > 25:
                logger.warning(
                    "(%s) *PacketControl* RATE DROP! Stream ID: %s TGID: %s",
                    system_name, int_id(stream_id), int_id(dst_id),
                )
                _slot_st["LAST"] = pkt_time
                return False
        if _rx_start + 180 < pkt_time:
            if not _slot_st.get("LOOPLOG"):
                logger.info(
                    "(%s) HBP *SOURCE TIMEOUT* STREAM ID: %s, TG: %s, TS: %s, IGNORE THIS SOURCE",
                    system_name, int_id(stream_id), int_id(dst_id), slot,
                )
                _slot_st["LOOPLOG"] = True
            _slot_st["LAST"] = pkt_time
            return False
        for other_name, proto in protocols.items():
            if other_name == system_name:
                continue
            omode = systems_cfg.get(other_name, {}).get("MODE")
            ostatus = getattr(proto, "STATUS", None)
            if not ostatus:
                continue
            if omode != "OPENBRIDGE":
                for _sysslot in ostatus:
                    ss = ostatus.get(_sysslot)
                    if isinstance(ss, dict) and stream_id == ss.get("RX_STREAM_ID"):
                        if not _slot_st.get("LOOPLOG"):
                            logger.debug(
                                "(%s) HBP *LoopControl* FIRST HBP: %s, STREAM ID: %s, TG: %s, TS: %s, IGNORE THIS SOURCE",
                                system_name, other_name, int_id(stream_id), int_id(dst_id), _sysslot,
                            )
                            _slot_st["LOOPLOG"] = True
                        _slot_st["LAST"] = pkt_time
                        return False
            else:
                obp_st = ostatus.get(stream_id)
                if (
                    isinstance(obp_st, dict)
                    and "1ST" in obp_st
                    and obp_st.get("TGID") == dst_id
                    and not obp_st.get("_fin")
                ):
                    if not _slot_st.get("LOOPLOG"):
                        logger.debug(
                            "(%s) HBP *LoopControl* FIRST OBP %s, STREAM ID: %s, TG %s, IGNORE THIS SOURCE",
                            system_name, other_name, int_id(stream_id), int_id(dst_id),
                        )
                        _slot_st["LOOPLOG"] = True
                    _slot_st["LAST"] = pkt_time
                    if (
                        systems_cfg.get(system_name, {}).get("ENHANCED_OBP")
                        and "_bcsq" not in _slot_st
                    ):
                        if hasattr(src_proto, "_obp_send_bcsq"):
                            src_proto._obp_send_bcsq(dst_id, stream_id)
                        _slot_st["_bcsq"] = True
                    return False
        if seq and seq == _slot_st.get("lastSeq"):
            _slot_st["loss"] = _slot_st.get("loss", 0) + 1
            return False
        if seq and _slot_st.get("lastSeq") and seq != 1 and seq < _slot_st.get("lastSeq", 0):
            _slot_st["loss"] = _slot_st.get("loss", 0) + 1
            return False
        _h = blake2b(digest_size=16)
        _h.update(data)
        _pkt_crc = _h.digest()
        if seq > 0 and "crcs" in _slot_st and _pkt_crc in _slot_st["crcs"]:
            _slot_st["loss"] = _slot_st.get("loss", 0) + 1
            return False
        if seq and _slot_st.get("lastSeq") and seq > (_slot_st.get("lastSeq", 0) + 1):
            _slot_st["loss"] = _slot_st.get("loss", 0) + 1
        _slot_st["lastSeq"] = seq
        _slot_st["lastData"] = data
        if "crcs" in _slot_st:
            _slot_st["crcs"].add(_pkt_crc)
        return True


    def _send_data_to_hbp(
        self,
        source_system: str,
        d_system: str,
        d_slot: int,
        dst_id: bytes,
        tmp_bits: int,
        data: bytes,
        dmrpkt: bytes,
        rf_src: bytes,
        stream_id: bytes,
        peer_id: bytes,
        d_peer_id: bytes | None = None,
    ) -> None:
        """Legacy sendDataToHBP: forward a unit-data packet to an HBP (MASTER/PEER) target.

        ``d_peer_id`` (the exact hotspot the destination was last heard on, from SUB_MAP
        or the 6/7-digit peer-ID match) lets this go directly to that one peer via
        ``send_peer`` instead of ``send_system``/``send_peers`` broadcasting the private
        DATA to every other hotspot connected to ``d_system`` — the same cross-talk this
        server already avoids for private voice REPEAT (``_pvt_repeat_targets``). Falls
        back to the system-wide broadcast when the target peer isn't known or connected
        (unchanged legacy parity for that case)."""
        _tmp_data = b"".join([data[:15], tmp_bits.to_bytes(1, "big"), data[16:20], dmrpkt])
        try:
            if d_peer_id is not None:
                protocols = self._get_protocols() if self._get_protocols else {}
                proto = protocols.get(d_system)
                if proto is not None and d_peer_id in getattr(proto, "_peers", {}):
                    proto.send_peer(d_peer_id, _tmp_data)
                else:
                    self._send_to_system(d_system, _tmp_data)
            else:
                self._send_to_system(d_system, _tmp_data)
        except Exception as exc:
            logger.warning("(%s) send_data_to_hbp %s failed: %s", source_system, d_system, exc)
            return
        logger.info("(%s) UNIT Data Bridged to HBP System: %s DST_ID: %s", source_system, d_system, int_id(dst_id))
        if int_id(dst_id) == 900999 and len(dmrpkt) == 33:
            logger.info(
                "(%s) UNIT trace -> %s stream=%s payload33=%s",
                source_system, d_system, int_id(stream_id), dmrpkt.hex(),
            )
        _dtype_vseq = data[15] & 0xF if len(data) > 15 else 0
        if unit_data_reportable(_dtype_vseq):
            self._send_routing_event(
                "UNIT DATA,DATA,TX,{},{},{},{},{},{}".format(
                    d_system, int_id(stream_id), int_id(peer_id), int_id(rf_src), 1, int_id(dst_id),
                )
            )
