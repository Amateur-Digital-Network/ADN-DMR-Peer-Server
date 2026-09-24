# ADN DMR Peer Server - bridge LC / Talker Alias
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

"""Embedded LC rewrite and Talker Alias bridge coordination (no Twisted)."""

from __future__ import annotations

import logging
from typing import Any

from bitarray import bitarray

from ...domain import int_id
from ...domain.dmr import bptc
from ...domain.dmr.const import LC_OPT_G, LC_OPT_U
from ...domain.talker_alias import DMRA_BLOCK_COUNT
from ..talker_alias_use_cases import passthrough_complete, talker_alias_settings
from .helpers import EMB_LC_SLICE

logger = logging.getLogger(__name__)


# Distinct destination LCs kept encoded; a call start needs one per (TG, source radio).
_LC_SET_CACHE_MAX = 512


class LcTaMixin:
    """Talker Alias DMRA relay and embedded LC overlay on forward legs."""

    _lc_set_cache: dict[bytes, tuple[bitarray, bitarray, dict[int, Any]]]

    def _encode_lc_set(self, dst_lc: bytes) -> tuple[bitarray, bitarray, dict[int, Any]]:
        """Header LC, terminator LC and embedded LC for one destination LC.

        About 250us of BPTC/RS work, done once per LC instead of once per target
        leg: every leg opened for the same TG and radio gets the same encoding.
        The results are shared between legs, and every reader only slices them.
        """
        cache = self._lc_set_cache
        if not isinstance(dst_lc, bytes):  # unhashable (bytearray): encode, don't keep
            return (
                bptc.encode_header_lc(dst_lc),
                bptc.encode_terminator_lc(dst_lc),
                self._encode_emblc(dst_lc),
            )
        codes = cache.get(dst_lc)
        if codes is None:
            codes = (
                bptc.encode_header_lc(dst_lc),
                bptc.encode_terminator_lc(dst_lc),
                self._encode_emblc(dst_lc),
            )
            if len(cache) >= _LC_SET_CACHE_MAX:
                del cache[next(iter(cache))]  # oldest first, not the whole cache
            cache[dst_lc] = codes
        return codes

    def _get_stream_dmra_blocks(self, source_system: str, stream_id: bytes) -> dict[int, bytes] | None:
        if not self._get_dmra_blocks:
            return None
        return self._get_dmra_blocks(source_system, stream_id)

    def _both_ta_key(self, source_system: str, stream_id: bytes) -> tuple[str, bytes]:
        return (source_system, stream_id)

    def _ta_capable_source(self, source_system: str) -> bool:
        """True when the source leg can buffer TA (MASTER DMRA/voice or OBP voice)."""
        return self._config.get("SYSTEMS", {}).get(source_system, {}).get("MODE") in (
            "MASTER",
            "OPENBRIDGE",
        )

    def _master_ta_wait_source(self, source_system: str) -> bool:
        """``both`` mode waits for hotspot TA only on HBP MASTER sources (not OBP)."""
        return self._config.get("SYSTEMS", {}).get(source_system, {}).get("MODE") == "MASTER"

    def _cancel_both_ta_wait(self, source_system: str, stream_id: bytes) -> None:
        wait = self._both_ta_wait.pop(self._both_ta_key(source_system, stream_id), None)
        if wait and wait.get("timer") and getattr(wait["timer"], "cancel", None):
            try:
                wait["timer"].cancel()
            except Exception:
                pass

    def _register_both_ta_wait(
        self,
        source_system: str,
        target_system: str,
        rf_src: bytes,
        stream_id: bytes,
        source_peer: bytes,
        wire_slot: int,
        tgid: int,
    ) -> None:
        """Defer DMRA UDP + embed inject until MMDVM fragments arrive (both mode)."""
        if not self._call_later:
            return
        key = self._both_ta_key(source_system, stream_id)
        wait = self._both_ta_wait.setdefault(
            key,
            {"rf_src": rf_src, "peer": source_peer, "targets": set()},
        )
        wait["rf_src"] = rf_src
        wait["peer"] = source_peer
        wait["wire_slot"] = int(wire_slot)
        wait["tgid"] = int(tgid)
        wait["targets"].add(target_system)
        if wait.get("timer"):
            return
        # Wait long enough to detect a slow source TA (MMDVM emits TA ~1 s in) before
        # falling back to inject; passthrough is applied earlier via on_dmra_fragment_stored.
        wait["timer"] = self._call_later(2.0, self._finalize_both_ta, source_system, stream_id)

    def _finalize_both_ta(self, source_system: str, stream_id: bytes) -> None:
        key = self._both_ta_key(source_system, stream_id)
        wait = self._both_ta_wait.pop(key, None)
        if not wait:
            return
        rf_src = wait["rf_src"]
        peer = wait["peer"]
        blocks = self._get_stream_dmra_blocks(source_system, stream_id)
        # No source TA within the window: fall back to inject (template).
        fallback_inject = not (blocks and passthrough_complete(blocks))
        wire_slot = int(wait.get("wire_slot", 2))
        tgid = int(wait.get("tgid", 0))
        for target_system in wait["targets"]:
            if not self._talker_alias.should_send_on_vhead(target_system, stream_id):
                continue
            self._send_talker_alias_to_target(
                source_system, target_system, rf_src, stream_id, peer,
                wire_slot=wire_slot, tgid=tgid,
                force=True, fallback_inject=fallback_inject,
            )
        self._apply_both_ta_embed(source_system, rf_src, stream_id, force_inject=fallback_inject)

    def _apply_both_ta_embed(
        self,
        source_system: str,
        rf_src: bytes,
        stream_id: bytes,
        *,
        force_inject: bool = False,
    ) -> None:
        """Install embed LC state once the TA decision is known (passthrough or inject)."""
        if not self._get_protocols:
            return
        for proto in self._get_protocols().values():
            status = getattr(proto, "STATUS", None)
            if not isinstance(status, dict):
                continue
            for st in status.values():
                if not isinstance(st, dict):
                    continue
                if st.get("TX_TA_ON"):
                    continue
                if st.get("_ta_embed_kind") == "passthrough" and not force_inject:
                    continue
                if st.get("TX_STREAM_ID") == stream_id and st.get("TX_RFS") == rf_src:
                    target = st.get("_ta_target_system", source_system)
                    self._init_talker_alias_embed(
                        st, source_system, target, rf_src, stream_id, force_inject=force_inject,
                    )
                elif st.get("REP_STREAM_ID") == stream_id:
                    self._init_talker_alias_embed(
                        st, source_system, source_system, rf_src, stream_id, force_inject=force_inject,
                    )

    def on_dmra_fragment_stored(
        self,
        source_system: str,
        peer_id: bytes,
        rf_src: bytes,
        stream_id: bytes,
    ) -> None:
        """Source TA may complete after VHEAD (DMRA UDP or decoded from voice).

        Once the buffer is complete, overlay the source TA on the outgoing embedded LC
        (passthrough/both). For `both` with a pending wait, also relay DMRA now and cancel
        the inject fallback.
        """
        if talker_alias_settings(self._config, source_system)["mode"] not in ("both", "passthrough"):
            return
        blocks = self._get_stream_dmra_blocks(source_system, stream_id)
        if not blocks or not passthrough_complete(blocks):
            return
        relay_key = self._both_ta_key(source_system, stream_id)
        already_relayed = relay_key in self._passthrough_relayed
        wait = self._both_ta_wait.get(relay_key)
        if wait and not already_relayed:
            wait["peer"] = peer_id
            self._cancel_both_ta_wait(source_system, stream_id)
            for target_system in wait["targets"]:
                self._send_talker_alias_to_target(
                    source_system, target_system, rf_src, stream_id, peer_id,
                    wire_slot=int(wait.get("wire_slot", 2)),
                    tgid=int(wait.get("tgid", 0)),
                    force=True,
                )
        self._apply_both_ta_embed(source_system, rf_src, stream_id)
        if not already_relayed:
            self._relay_passthrough_dmra(source_system, peer_id, rf_src, stream_id)
            self._passthrough_relayed.add(relay_key)

    def _relay_passthrough_dmra(
        self,
        source_system: str,
        peer_id: bytes,
        rf_src: bytes,
        stream_id: bytes,
    ) -> None:
        """After the source TA buffer is complete, relay DMRA to bridge/repeat targets."""
        blocks = self._get_stream_dmra_blocks(source_system, stream_id)
        if not blocks or not passthrough_complete(blocks):
            return
        if self._get_protocols:
            for proto in self._get_protocols().values():
                status = getattr(proto, "STATUS", None)
                if not isinstance(status, dict):
                    continue
                for slot_key, st in status.items():
                    if not isinstance(st, dict):
                        continue
                    if st.get("TX_STREAM_ID") != stream_id or st.get("TX_RFS") != rf_src:
                        continue
                    target = st.get("_ta_target_system")
                    if not isinstance(target, str) or not target:
                        continue
                    tx_peer = st.get("TX_PEER", peer_id)
                    try:
                        wire_slot = int(slot_key)
                    except (TypeError, ValueError):
                        wire_slot = 2
                    tgid = int_id(st.get("TX_TGID", b"\x00\x00\x00"))
                    if tgid <= 0:
                        continue
                    self._send_talker_alias_to_target(
                        source_system, target, rf_src, stream_id, tx_peer,
                        wire_slot=wire_slot, tgid=tgid, force=True,
                    )
        src_cfg = self._config.get("SYSTEMS", {}).get(source_system, {})
        if src_cfg.get("MODE") == "MASTER" and src_cfg.get("REPEAT", True):
            wire_slot, tgid = self._local_repeat_route(source_system, stream_id, rf_src)
            if tgid > 0:
                self.send_talker_alias_local_repeat(
                    source_system, peer_id, rf_src, stream_id, wire_slot, tgid,
                )

    def _local_repeat_route(
        self, system_name: str, stream_id: bytes, rf_src: bytes,
    ) -> tuple[int, int]:
        if not self._get_protocols:
            return 2, 0
        proto = self._get_protocols().get(system_name)
        status = getattr(proto, "STATUS", None) if proto else None
        if not isinstance(status, dict):
            return 2, 0
        for slot_key, st in status.items():
            if not isinstance(st, dict):
                continue
            if st.get("REP_STREAM_ID") == stream_id or (
                st.get("RX_STREAM_ID") == stream_id and st.get("RX_RFS") == rf_src
            ):
                try:
                    wire_slot = int(slot_key)
                except (TypeError, ValueError):
                    wire_slot = 2
                tgid = int_id(st.get("REP_TGID") or st.get("RX_TGID") or st.get("TX_TGID") or b"\x00\x00\x00")
                if tgid > 0:
                    return wire_slot, tgid
        return 2, 0

    def _dispatch_talker_alias_on_bridge_open(
        self,
        target_st: dict[str, Any],
        source_system: str,
        target_system: str,
        rf_src: bytes,
        stream_id: bytes,
        source_peer: bytes,
        wire_slot: int,
        tgid: int,
    ) -> None:
        """Prepare TA on a new bridged HBP leg (VHEAD or first burst after hangtime)."""
        settings = talker_alias_settings(self._config, source_system)
        if not settings["enabled"]:
            return
        blocks = self._get_stream_dmra_blocks(source_system, stream_id)
        have_source_ta = bool(blocks and passthrough_complete(blocks))
        if settings["mode"] == "both" and self._master_ta_wait_source(source_system) and not have_source_ta:
            self._register_both_ta_wait(
                source_system, target_system, rf_src, stream_id, source_peer,
                wire_slot, tgid,
            )
            return
        self._init_talker_alias_embed(
            target_st,
            source_system,
            target_system,
            rf_src,
            stream_id,
        )
        self._send_talker_alias_to_target(
            source_system, target_system, rf_src, stream_id, source_peer,
            wire_slot=wire_slot, tgid=tgid,
        )

    def _send_talker_alias_to_target(
        self,
        source_system: str,
        target_system: str,
        rf_src: bytes,
        stream_id: bytes,
        source_peer: bytes,
        *,
        wire_slot: int | None = None,
        tgid: int | None = None,
        force: bool = False,
        fallback_inject: bool = False,
        repeat_vhead: bool = False,
    ) -> None:
        """Emit DMRA to an HBP target on VHEAD (once per target stream)."""
        if not self._send_dmra_to_system:
            return
        settings = talker_alias_settings(self._config, source_system)
        if not settings["enabled"] or not settings["send_dmra"]:
            # Embedded TA in DMRD B–E still runs via _rewrite_embed_lc; this gate is
            # standalone DMRA UDP only (legacy parity default: off).
            return
        tgt_mode = self._config.get("SYSTEMS", {}).get(target_system, {}).get("MODE")
        if tgt_mode not in ("MASTER", "PEER"):
            return
        blocks = self._get_stream_dmra_blocks(source_system, stream_id)
        have_passthrough = bool(blocks and passthrough_complete(blocks))
        if have_passthrough:
            if force:
                if not self._talker_alias.should_resend_passthrough_dmra(target_system, stream_id):
                    return
            elif not repeat_vhead and not self._talker_alias.should_send_on_vhead(target_system, stream_id):
                return
        elif not repeat_vhead and not self._talker_alias.should_send_on_vhead(target_system, stream_id):
            return
        if not have_passthrough:
            # Legacy resolve_ta (both): inject at VHEAD when the buffer is still empty.
            fallback_inject = True
        packets = self._talker_alias.packets_for_stream(
            source_system,
            rf_src,
            stream_id,
            self._get_dmra_blocks,
            target_system=target_system,
            fallback_inject=fallback_inject,
        )
        if not packets:
            return
        if wire_slot is None or tgid is None:
            logger.warning(
                "(%s) *TALKER ALIAS* stream %s DMRA not sent: missing slot/tgid",
                target_system,
                int_id(stream_id),
            )
            return
        exclude = source_peer if target_system == source_system else None
        try:
            peer_count = self._send_dmra_to_system(
                target_system,
                packets,
                exclude_peer=exclude,
                slot=wire_slot,
                tgid=tgid,
            )
        except Exception as e:
            logger.warning("(ROUTER) send_dmra_to_system %s failed: %s", target_system, e)
            return
        self._talker_alias.mark_dmra_sent(
            target_system,
            stream_id,
            kind="passthrough" if have_passthrough else "inject",
        )
        sid = int_id(stream_id)
        if peer_count:
            logger.debug(
                "(%s) *TALKER ALIAS* stream %s sent %d DMRA block(s) to %d peer(s) TG %s slot %s",
                target_system, sid, len(packets), peer_count, tgid, wire_slot,
            )
        elif exclude:
            logger.debug(
                "(%s) *TALKER ALIAS* stream %s no DMRA sent (source peer %s excluded on repeat)",
                target_system, sid, int_id(exclude),
            )

    def send_talker_alias_local_repeat(
        self,
        system_name: str,
        source_peer: bytes,
        rf_src: bytes,
        stream_id: bytes,
        wire_slot: int,
        tgid: int,
    ) -> None:
        """Inject/pass-through TA to other peers on this MASTER (REPEAT path)."""
        self._send_talker_alias_to_target(
            system_name, system_name, rf_src, stream_id, source_peer,
            wire_slot=wire_slot, tgid=tgid,
            repeat_vhead=True,
        )

    def prepare_talker_alias_local_repeat(
        self,
        system_name: str,
        source_peer: bytes,
        rf_src: bytes,
        dst_id: bytes,
        slot: int,
        stream_id: bytes,
        call_type: str = "group",
    ) -> None:
        """REPEAT on VHEAD: standalone DMRA plus embedded TA state for downlink DMRD.

        WPSD/MMDVMHost ignores standalone DMRA UDP; it only displays Talker Alias decoded
        from embedded LC inside repeated voice bursts (B–E). Private (unit) calls use the
        same passthrough/inject policy as group calls -- only the LC FLCO opt byte differs
        (LC_OPT_U vs LC_OPT_G), since the destination is a subscriber ID, not a talkgroup.
        """
        settings = talker_alias_settings(self._config, system_name)
        if settings["enabled"] and self._get_protocols:
            proto = self._get_protocols().get(system_name)
            status = getattr(proto, "STATUS", None) if proto else None
            if isinstance(status, dict) and slot in status:
                st = status[slot]
                if not isinstance(st, dict):
                    st = {}
                    status[slot] = st
                if st.get("REP_STREAM_ID") != stream_id:
                    lc_opt = LC_OPT_U if call_type == "unit" else LC_OPT_G
                    dst_lc = lc_opt + dst_id + rf_src
                    st["REP_STREAM_ID"] = stream_id
                    st["REP_EMB_LC"] = self._encode_emblc(dst_lc)
                    self._init_talker_alias_embed(
                        st, system_name, system_name, rf_src, stream_id,
                    )
        self.send_talker_alias_local_repeat(
            system_name, source_peer, rf_src, stream_id, slot, int_id(dst_id),
        )

    def rewrite_repeat_voice_burst(
        self,
        system_name: str,
        slot: int,
        stream_id: bytes,
        dtype_vseq: int,
        dmrpkt: bytes,
    ) -> bytes:
        """Overlay Talker Alias on embedded LC for REPEAT copies (voice bursts B–E)."""
        if dtype_vseq not in (1, 2, 3, 4) or len(dmrpkt) < 33:
            return dmrpkt
        if not self._get_protocols:
            return dmrpkt
        proto = self._get_protocols().get(system_name)
        status = getattr(proto, "STATUS", None) if proto else None
        if not isinstance(status, dict):
            return dmrpkt
        st = status.get(slot)
        if not isinstance(st, dict) or st.get("REP_STREAM_ID") != stream_id:
            return dmrpkt
        if "REP_EMB_LC" not in st or st.get("TX_TA_EMB") is None:
            return dmrpkt
        dmrbits = bitarray(endian="big")
        dmrbits.frombytes(dmrpkt)
        self._rewrite_embed_lc(dmrbits, st, dtype_vseq, "REP_EMB_LC")
        return dmrbits.tobytes()

    def clear_talker_alias_stream(self, system_name: str, stream_id: bytes) -> None:
        """Release per-stream TA dedupe state after VTERM."""
        self._cancel_both_ta_wait(system_name, stream_id)
        self._passthrough_relayed.discard(self._both_ta_key(system_name, stream_id))
        self._talker_alias.clear_stream(system_name, stream_id)
        if not self._get_protocols:
            return
        proto = self._get_protocols().get(system_name)
        if proto is not None and hasattr(proto, "clear_ta_stream_buffer"):
            proto.clear_ta_stream_buffer(stream_id)
        if proto is None:
            return
        status = getattr(proto, "STATUS", None)
        if not isinstance(status, dict):
            return
        for slot in (1, 2):
            st = status.get(slot)
            if not isinstance(st, dict):
                continue
            if st.get("TX_STREAM_ID") == stream_id:
                self._clear_talker_alias_embed(st)
            if st.get("REP_STREAM_ID") == stream_id:
                st.pop("REP_STREAM_ID", None)
                st.pop("REP_EMB_LC", None)
                self._clear_talker_alias_embed(st)

    def _obp_talker_alias_target(self, target_system: str) -> bool:
        """OpenBridge mesh legs carry group LC only — no DMRA or embedded Talker Alias."""
        return self._config.get("SYSTEMS", {}).get(target_system, {}).get("MODE") == "OPENBRIDGE"

    def _init_talker_alias_embed(
        self,
        st: dict[str, Any],
        source_system: str,
        target_system: str,
        rf_src: bytes,
        stream_id: bytes,
        *,
        force_inject: bool = False,
    ) -> None:
        """Prepare per-stream embedded TA state for DMRD voice injection.

        The group LC is always rewritten for the destination TG by ``_rewrite_embed_lc``;
        here we only set ``TX_TA_EMB`` when a Talker Alias should be overlaid. The source
        TA (passthrough/both) is re-encoded from its decoded DMRA/voice blocks once they
        arrive; the template is used for ``inject`` and the ``both`` fallback. If no TA is
        available yet (e.g. at VHEAD, before the source TA has been decoded), TX_TA_EMB is
        left unset and only the destination group LC is emitted until it becomes available.

        OpenBridge targets never receive embedded TA (mesh peers do not decode it).
        """
        if self._obp_talker_alias_target(target_system):
            return
        st["_ta_source_system"] = source_system
        st["_ta_target_system"] = target_system
        settings = talker_alias_settings(self._config, source_system)
        if not settings["enabled"]:
            return
        if settings["mode"] == "both" and not self._ta_capable_source(source_system):
            force_inject = True
        st.pop("TX_TA_EMB", None)
        st.pop("TX_TA_PHASE", None)
        st.pop("TX_TA_ON", None)
        st.pop("_ta_last_embed_burst", None)
        st.pop("_ta_last_embed_frag", None)
        emblcs = self._talker_alias.embedded_emblc_for_stream(
            source_system,
            rf_src,
            stream_id,
            self._get_dmra_blocks,
            target_system=target_system,
            fallback_inject=force_inject,
        )
        if emblcs:
            st["TX_TA_EMB"], st["TX_TA_BLOCK_COUNT"] = emblcs
            st["TX_TA_PHASE"] = 0
            # First B1–B4 cycle carries group LC; TA on the next cycle.
            st["TX_TA_ON"] = False
            blocks = self._get_stream_dmra_blocks(source_system, stream_id)
            if blocks and passthrough_complete(blocks) and not force_inject:
                st["_ta_embed_kind"] = "passthrough"
            else:
                st["_ta_embed_kind"] = "inject"

    def _rewrite_embed_lc(
        self,
        dmrbits: bitarray,
        st: dict[str, Any],
        dtype_vseq: int,
        emb_key: str,
    ) -> None:
        """Replace embedded LC on voice bursts B–E (legacy bridge.py parity).

        Superframes always carry the **destination** LC (``emb_key`` — group LC re-encoded
        for the rewritten TGID, or unit LC for private calls) — this is required for the voice to be accepted
        by the receiving MMDVM (a mismatched embedded LC causes packet loss). When a
        Talker Alias is available (``TX_TA_EMB``: injected template or the source TA
        re-encoded from its DMRA/voice blocks) it is overlaid on alternate superframes.
        """
        if dtype_vseq not in (1, 2, 3, 4):
            return
        dmrpkt = dmrbits.tobytes()
        burst_key = (dtype_vseq, dmrpkt)
        # Duplicated uplink bursts (same B–E payload) must not advance the TA phase
        # machine; REPEAT runs before ingress duplicate drops (legacy lastData parity).
        if burst_key == st.get("_ta_last_embed_burst"):
            last_frag = st.get("_ta_last_embed_frag")
            if last_frag is not None:
                dmrbits[EMB_LC_SLICE] = last_frag
            return
        ta_emb = st.get("TX_TA_EMB")
        if ta_emb is not None and st.get("TX_TA_ON"):
            phase = st.get("TX_TA_PHASE", 0)
            block_count = st.get("TX_TA_BLOCK_COUNT", DMRA_BLOCK_COUNT)
            frag = ta_emb[phase][dtype_vseq]
            if dtype_vseq == 4:
                st["TX_TA_ON"] = False
                st["TX_TA_PHASE"] = (phase + 1) % block_count
        else:
            frag = st[emb_key][dtype_vseq]
            if dtype_vseq == 4 and ta_emb is not None:
                st["TX_TA_ON"] = True
        dmrbits[EMB_LC_SLICE] = frag
        st["_ta_last_embed_burst"] = burst_key
        st["_ta_last_embed_frag"] = frag

    def _clear_talker_alias_embed(self, st: dict[str, Any]) -> None:
        st.pop("TX_TA_EMB", None)
        st.pop("TX_TA_PHASE", None)
        st.pop("TX_TA_BLOCK_COUNT", None)
        st.pop("TX_TA_ON", None)
        st.pop("_ta_embed_kind", None)
        st.pop("_ta_last_embed_burst", None)
        st.pop("_ta_last_embed_frag", None)
