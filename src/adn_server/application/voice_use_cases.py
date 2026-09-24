# ADN DMR Peer Server - voice use cases
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

"""Voice/AMBE/TTS: scheduled announcements, TTS announcements, playback. Orchestrates VoiceProvider."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from ..domain import HBPF_SLT_VHEAD, HBPF_SLT_VTERM, bytes_3, bytes_4, int_id
from .ports import VoiceProvider
from .routing.helpers import slot_voice_held_by_other_stream
from .server_voice import (
    announcement_item_source_bytes,
    server_voice_rf_src_bytes,
)

logger = logging.getLogger(__name__)

_FRAME_INTERVAL = 0.058
_ANNOUNCEMENT_EXCLUDED = ("ECHO", "D-APRS")
_BROADCAST_GAP = 1.5


@dataclass
class _PromptRun:
    """State of one prompt, shared by its worker thread and the reactor.

    Only the reactor writes it; the thread reads ``stopped`` between frames.
    """

    stopped: bool = False
    sent: int = 0
    stream_id: bytes | None = None


class VoiceUseCases:
    """Use cases for voice announcements and TTS."""

    def __init__(
        self,
        voice_provider: VoiceProvider,
        config: dict[str, Any],
        get_protocols: Callable[[], dict[str, Any]] | None = None,
        call_from_reactor: Callable[..., None] | None = None,
        audio_path: str | None = None,
        routing_table_for_report: Callable[[], dict[str, list[dict[str, Any]]]] | None = None,
        call_later: Callable[..., Any] | None = None,
        start_looping_call: Callable[[Callable[[], None], float, bool], Any] | None = None,
        defer_to_thread: Callable[..., Any] | None = None,
        inject_announcement_ptt: Callable[[bytes, float], bool | None] | None = None,
        send_routing_event: Callable[[str], None] | None = None,
        announcement_ptt_system: str | None = None,
    ) -> None:
        self._voice = voice_provider
        self._config = config
        self._get_protocols = get_protocols
        self._call_from_reactor = call_from_reactor
        self._audio_path = audio_path or ""
        self._routing_table_for_report = routing_table_for_report
        self._call_later = call_later
        self._start_looping_call = start_looping_call
        self._defer_to_thread = defer_to_thread
        self._inject_announcement_ptt = inject_announcement_ptt
        self._send_routing_event = send_routing_event
        self._announcement_ptt_system = announcement_ptt_system
        self._voice_report_state: dict[str, Any] | None = None
        self._ann_tasks: dict[int, Any] = {}
        self._tts_tasks: dict[int, Any] = {}
        self._announcement_running: dict[int, bool] = {}
        self._tts_running: dict[int, bool] = {}
        self._announcement_last_hour: dict[int, int] = {}
        self._tts_last_hour: dict[int, int] = {}
        self._broadcast_queue: list[dict[str, Any]] = []
        self._broadcast_active_tgs: set[str] = set()

    def _server_source_id(self) -> bytes:
        return server_voice_rf_src_bytes(self._config)

    def get_ambe_words(self, languages: str, audio_path: str) -> dict[str, dict[str, Any]]:
        """Load AMBE words for given languages (legacy readAMBE.readfiles)."""
        return self._voice.get_ambe_words(languages, audio_path)

    def pkt_gen(self, rf_src: bytes, dst_id: bytes, peer: bytes, slot: int, phrase: list[Any]) -> Any:
        """Generate HBP voice packets for phrase (legacy mk_voice.pkt_gen)."""
        return self._voice.pkt_gen(rf_src, dst_id, peer, slot, phrase)

    def _global_server_id_bytes(self) -> bytes:
        server_id = self._config.get("GLOBAL", {}).get("SERVER_ID", b"\x00\x00\x00\x00")
        return bytes_4(int_id(server_id))

    def _active_bridge_slots_for_tg(self, tg: int, system: str) -> set[int]:
        bridges = self._routing_table_for_report() if self._routing_table_for_report else {}
        entries = bridges.get(str(tg), [])
        if not isinstance(entries, list):
            return set()
        out: set[int] = set()
        for be in entries:
            if not isinstance(be, dict):
                continue
            if be.get("SYSTEM") != system or not be.get("ACTIVE"):
                continue
            ts = be.get("TS")
            if ts is not None:
                out.add(int(ts))
        return out

    def _inject_ptt_slot_busy(
        self,
        slot: dict[str, Any],
        tg: int,
        sys_cfg: dict[str, Any],
        wire_ts: int,
    ) -> bool:
        """True when MASTER slot cannot accept synthetic PTT for ``tg``."""
        from .routing.helpers import master_dynamic_tg_slots, master_slot_holds_server_broadcast
        from .server_voice import all_server_voice_ids

        if master_slot_holds_server_broadcast(
            slot,
            time.time(),
            server_voice_rf_srcs=all_server_voice_ids(self._config),
        ):
            return False
        # External QSO on RX while slot is in hangtime — defer announcement inject.
        if slot.get("RX_TYPE") != HBPF_SLT_VTERM and slot.get("TX_TYPE") == HBPF_SLT_VTERM:
            return True
        if wire_ts in master_dynamic_tg_slots(sys_cfg, int(tg)):
            return False
        ptt_system = self._announcement_ptt_system or ""
        if wire_ts in self._active_bridge_slots_for_tg(tg, ptt_system):
            return False
        if slot.get("RX_TYPE") == HBPF_SLT_VTERM and slot.get("TX_TYPE") == HBPF_SLT_VTERM:
            return False
        return True

    def _inject_ptt_slot_order(self, tg: int, sys_cfg: dict[str, Any]) -> list[int]:
        """Prefer the RF slot where ``tg`` is already a dynamic UA session."""
        from .routing.helpers import master_dynamic_tg_slots

        dynamic = sorted(master_dynamic_tg_slots(sys_cfg, int(tg)), reverse=True)
        preferred = list(dynamic)
        if self._announcement_ptt_system:
            for ts in sorted(
                self._active_bridge_slots_for_tg(tg, self._announcement_ptt_system),
                reverse=True,
            ):
                if ts not in preferred:
                    preferred.append(ts)
        ordered: list[int] = []
        for ts in [*preferred, 2, 1]:
            if ts not in ordered:
                ordered.append(ts)
        return ordered

    def _build_inject_ptt_targets(
        self, label: str, tg: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """Synthetic PTT on proxy MASTER: routing creates the UA bridge on inject."""
        targets: list[dict[str, Any]] = []
        busy_count = 0
        ptt_system = self._announcement_ptt_system
        if not ptt_system or not self._inject_announcement_ptt:
            return targets, busy_count
        protocols = self._get_protocols() if self._get_protocols else {}
        systems_cfg = self._config.get("SYSTEMS", {})
        if ptt_system not in protocols or ptt_system not in systems_cfg:
            return targets, busy_count
        if systems_cfg[ptt_system].get("MODE") != "MASTER":
            return targets, busy_count
        sys_obj = protocols[ptt_system]
        status = getattr(sys_obj, "STATUS", None)
        if not status:
            return targets, busy_count
        sys_cfg = systems_cfg[ptt_system]
        for ts in self._inject_ptt_slot_order(tg, sys_cfg):
            slot_index = 2 if ts == 2 else 1
            slot = status.get(slot_index)
            if not slot:
                continue
            if self._inject_ptt_slot_busy(slot, tg, sys_cfg, ts):
                logger.debug("(%s) System %s TS%s busy (QSO active), skipping", label, ptt_system, ts)
                busy_count += 1
                continue
            targets.append({"sys_obj": sys_obj, "name": ptt_system, "slot": slot, "ts": ts})
            break
        return targets, busy_count

    def _build_announcement_targets(
        self, tg_int: int, tg_str: str, label: str
    ) -> tuple[list[dict[str, Any]], int]:
        """MASTER targets for announcement/TTS broadcast.

        Inject path: synthetic PTT on the TG's dynamic UA slot when applicable.
        Legacy path: MASTER systems with an ACTIVE bridge row for the TG.
        """
        if self._inject_announcement_ptt:
            return self._build_inject_ptt_targets(label, tg_int)
        targets: list[dict[str, Any]] = []
        busy_count = 0
        protocols = self._get_protocols() if self._get_protocols else {}
        systems_cfg = self._config.get("SYSTEMS", {})
        bridges = self._routing_table_for_report() if self._routing_table_for_report else {}
        bridge_entries = bridges.get(tg_str, [])
        for sys_name in list(protocols.keys()):
            if sys_name in _ANNOUNCEMENT_EXCLUDED or any(
                sys_name.startswith(ex + "-") for ex in _ANNOUNCEMENT_EXCLUDED
            ):
                continue
            if sys_name not in systems_cfg or systems_cfg[sys_name].get("MODE") != "MASTER":
                continue
            if not systems_cfg[sys_name].get("PEERS"):
                continue
            has_peers = any(
                systems_cfg[sys_name]["PEERS"].get(pid, {}).get("CALLSIGN")
                for pid in systems_cfg[sys_name]["PEERS"]
            )
            if not has_peers or sys_name not in protocols:
                continue
            sys_obj = protocols[sys_name]
            if not getattr(sys_obj, "STATUS", None):
                continue
            active_slots = [
                be["TS"] for be in bridge_entries
                if be.get("SYSTEM") == sys_name and be.get("ACTIVE") and be.get("TS") is not None
            ]
            active_slots = list(dict.fromkeys(active_slots))
            if not active_slots:
                continue
            for ts in active_slots:
                slot_index = 2 if ts == 2 else 1
                slot = sys_obj.STATUS.get(slot_index)
                if not slot:
                    continue
                rx_type = slot.get("RX_TYPE")
                tx_type = slot.get("TX_TYPE")
                if (rx_type != HBPF_SLT_VTERM) or (tx_type != HBPF_SLT_VTERM):
                    logger.debug("(%s) System %s TS%s busy (QSO active), skipping", label, sys_name, ts)
                    busy_count += 1
                    continue
                targets.append({"sys_obj": sys_obj, "name": sys_name, "slot": slot, "ts": ts})
        return targets, busy_count

    def _announcement_packet_peer(
        self,
        tg: int,
        targets: list[dict[str, Any]],
        server_id: bytes,
    ) -> bytes:
        """DMRD peer field: always GLOBAL SERVER_ID on inject (never a hotspot radio id)."""
        del tg, targets
        if self._inject_announcement_ptt:
            return self._global_server_id_bytes()
        return bytes_4(int_id(server_id))

    def _send_filtered_by_tg(
        self, sys_obj: Any, pkt: bytes, tg: int, ts: int, bridges: dict[str, list[dict[str, Any]]]
    ) -> int:
        """Return -1 if sent, 0 if TG/TS not active (legacy _sendFilteredByTG)."""
        tg_str = str(tg)
        for be in bridges.get(tg_str, []):
            if be.get("SYSTEM") == getattr(sys_obj, "_system", None) and be.get("TS") == ts and be.get("ACTIVE"):
                sys_obj.send_system(pkt)
                return -1
        return 0

    def _emit_announcement_voice_event(
        self,
        action: str,
        trx: str,
        system: str,
        stream_id: bytes,
        slot: int,
        tg: int,
        rf_src: int,
        duration: float | None = None,
    ) -> None:
        if not self._send_routing_event:
            return
        parts = [
            "GROUP VOICE",
            action,
            trx,
            system,
            str(int_id(stream_id)),
            str(rf_src),
            str(rf_src),
            str(slot),
            str(tg),
        ]
        if duration is not None:
            parts.append(f"{duration:.2f}")
        parts.append("1")
        self._send_routing_event(",".join(parts))

    def _maybe_begin_legacy_voice_report(
        self,
        targets: list[dict[str, Any]],
        pkts_by_ts: dict[int, list[bytes]],
        tg: int,
    ) -> None:
        # Inject: routing emits START,RX (SERVER_ID) + OBP TX; same-MASTER downlink has no
        # bridge leg, so emit START,TX on the proxy MASTER for monitor fan-out (SYSTEM-N).
        self._begin_legacy_voice_report(targets, pkts_by_ts, tg)

    def _maybe_end_legacy_voice_report(self) -> None:
        self._end_legacy_voice_report()

    def _begin_legacy_voice_report(
        self,
        targets: list[dict[str, Any]],
        pkts_by_ts: dict[int, list[bytes]],
        tg: int,
    ) -> None:
        if not self._send_routing_event or not targets:
            return
        wire_ts = targets[0]["ts"]
        pkts = pkts_by_ts.get(wire_ts) or []
        if not pkts:
            return
        stream_id = pkts[0][16:20]
        rf_src = int_id(pkts[0][5:8])
        systems: list[tuple[str, int]] = []
        seen: set[tuple[str, int]] = set()
        for t in targets:
            key = (t["name"], t["ts"])
            if key in seen:
                continue
            seen.add(key)
            systems.append(key)
            self._emit_announcement_voice_event(
                "START", "TX", t["name"], stream_id, t["ts"], tg, rf_src
            )
        self._voice_report_state = {
            "stream_id": stream_id,
            "rf_src": pkts[0][5:8],
            "start": time.time(),
            "tg": tg,
            "systems": systems,
        }

    def _end_legacy_voice_report(self) -> None:
        state = self._voice_report_state
        self._voice_report_state = None
        if not state or not self._send_routing_event:
            return
        duration = time.time() - float(state["start"])
        stream_id = state["stream_id"]
        tg = int(state["tg"])
        for name, ts in state["systems"]:
            self._emit_announcement_voice_event(
                "END", "TX", name, stream_id, ts, tg, int_id(state["rf_src"]), duration
            )

    def _send_announcement_packets(
        self,
        targets: list[dict[str, Any]],
        pkts_by_ts: dict[int, list[bytes]],
        pkt_idx: int,
        source_id: bytes,
        dst_id: bytes,
        tg: int,
        label: str,
    ) -> None:
        """One frame: synthetic PTT via routing, or legacy per-MASTER send_system."""
        if self._inject_announcement_ptt:
            wire_ts = targets[0]["ts"] if targets else 2
            pkt = pkts_by_ts[wire_ts][pkt_idx]
            if self._inject_announcement_ptt(pkt, time.time()) is False:
                logger.warning("(%s) Routing rejected announcement frame %s", label, pkt_idx)
            return
        bridges = self._routing_table_for_report() if self._routing_table_for_report else {}
        now = time.time()
        for t in targets:
            try:
                sys_obj = t["sys_obj"]
                slot = t["slot"]
                t_ts = t["ts"]
                pkt = pkts_by_ts[t_ts][pkt_idx]
                stream_id = pkt[16:20]
                if stream_id not in sys_obj.STATUS:
                    sys_obj.STATUS[stream_id] = {
                        "START": now,
                        "CONTENTION": False,
                        "RFS": source_id,
                        "TGID": dst_id,
                        "LAST": now,
                    }
                    slot["TX_TGID"] = dst_id
                    slot["TX_RFS"] = source_id
                else:
                    sys_obj.STATUS[stream_id]["LAST"] = now
                slot["TX_TIME"] = now
                self._send_filtered_by_tg(sys_obj, pkt, tg, t_ts, bridges)
            except Exception as e:
                logger.error(
                    "(%s) Error sending packet %s to %s/TS%s: %s",
                    label,
                    pkt_idx,
                    t.get("name"),
                    t.get("ts"),
                    e,
                )

    def _mark_slots_busy(
        self,
        targets: list[dict[str, Any]],
        source_id: bytes | None = None,
    ) -> None:
        """Mark target slots busy (TX_TYPE=VHEAD) to prevent TS conflict."""
        server_rfs = source_id if source_id is not None else self._server_source_id()
        now = time.time()
        for t in targets:
            try:
                slot = t.get("slot")
                if slot is not None:
                    slot["TX_TYPE"] = HBPF_SLT_VHEAD
                    slot["TX_TIME"] = now
                    slot["TX_RFS"] = server_rfs
            except (KeyError, TypeError):
                pass

    def _mark_slots_free(self, targets: list[dict[str, Any]]) -> None:
        """Mark target slots free (TX_TYPE=VTERM) when broadcast done."""
        for t in targets:
            try:
                slot = t.get("slot")
                if slot is not None:
                    slot["TX_TYPE"] = HBPF_SLT_VTERM
            except (KeyError, TypeError):
                pass

    def _enqueue_broadcast(
        self, _type: str, targets: list[dict[str, Any]], pkts_by_ts: dict[int, list[bytes]],
        source_id: bytes, dst_id: bytes, tg: int, num: int, label: str,
    ) -> None:
        _tg_key = str(tg)
        if _tg_key in self._broadcast_active_tgs:
            self._broadcast_queue.append({
                'type': _type, 'targets': targets, 'pkts_by_ts': pkts_by_ts,
                'source_id': source_id, 'dst_id': dst_id, 'tg': tg, 'num': num, 'label': label,
            })
            _pos = len(self._broadcast_queue)
            logger.info('(%s) Same TG %s still on air; deferring next playback (pending %s)', label, tg, _pos)
        else:
            self._broadcast_active_tgs.add(_tg_key)
            self._mark_slots_busy(targets, source_id)
            logger.info('(%s) Starting broadcast immediately for TG %s (active TGs: %s)', label, tg, len(self._broadcast_active_tgs))
            if self._call_later:
                if _type == 'ann':
                    self._call_later(0.5, self._announcement_send_broadcast, targets, pkts_by_ts, 0, source_id, dst_id, tg, num, label, None)
                elif _type == 'tts':
                    self._call_later(0.5, self._tts_send_broadcast, targets, pkts_by_ts, 0, source_id, dst_id, tg, num, label, None)

    def _start_next_broadcast(self) -> None:
        if not self._broadcast_queue:
            return
        _next = None
        for i, _item in enumerate(self._broadcast_queue):
            _tg_key = str(_item['tg'])
            if _tg_key not in self._broadcast_active_tgs:
                _next = self._broadcast_queue.pop(i)
                break
        if not _next:
            return
        _type = _next['type']
        _label = _next['label']
        _tg_key = str(_next['tg'])
        self._broadcast_active_tgs.add(_tg_key)
        self._mark_slots_busy(_next['targets'], _next['source_id'])
        logger.info('(%s) Starting deferred same-TG playback for TG %s (%s pending, %s active TG(s))', _label, _next['tg'], len(self._broadcast_queue), len(self._broadcast_active_tgs))
        if self._call_later:
            if _type == 'ann':
                self._call_later(0.5, self._announcement_send_broadcast, _next['targets'], _next['pkts_by_ts'], 0, _next['source_id'], _next['dst_id'], _next['tg'], _next['num'], _label, None)
            elif _type == 'tts':
                self._call_later(0.5, self._tts_send_broadcast, _next['targets'], _next['pkts_by_ts'], 0, _next['source_id'], _next['dst_id'], _next['tg'], _next['num'], _label, None)

    def _broadcast_finished(self, tg: int | None = None) -> None:
        if tg is not None:
            self._broadcast_active_tgs.discard(str(tg))
        if self._broadcast_queue:
            logger.info(
                '(BROADCAST) TG %s announcement/TTS playback finished; %s same-TG deferred, %s active TG(s)',
                tg, len(self._broadcast_queue), len(self._broadcast_active_tgs),
            )
            if self._call_later:
                self._call_later(_BROADCAST_GAP, self._start_next_broadcast)
        else:
            if not self._broadcast_active_tgs:
                logger.info('(BROADCAST) All announcement/TTS playbacks finished')
            else:
                logger.info(
                    '(BROADCAST) TG %s announcement/TTS playback finished; %s other TG(s) still on air',
                    tg, len(self._broadcast_active_tgs),
                )

    def _announcement_send_broadcast(
        self,
        targets: list[dict[str, Any]],
        pkts_by_ts: dict[int, list[bytes]],
        pkt_idx: int,
        source_id: bytes,
        dst_id: bytes,
        tg: int,
        ann_idx: int,
        label: str,
        next_time: float | None = None,
    ) -> None:
        """Send one batch of packets; schedule next via call_later."""
        total = len(pkts_by_ts.get(1, []))
        if pkt_idx >= total or not targets:
            self._mark_slots_free(targets)
            self._maybe_end_legacy_voice_report()
            for t in targets:
                try:
                    obj = t.get("sys_obj")
                    if getattr(obj, "STATUS", None):
                        for sid in list(obj.STATUS.keys()):
                            if sid not in (1, 2):
                                del obj.STATUS[sid]
                except Exception as e:
                    logger.warning("(%s) slot STATUS cleanup failed: %s", label, e)
            self._announcement_running[ann_idx] = False
            if not targets:
                logger.info(
                    "(%s) Broadcast aborted at packet %s/%s: all targets removed (QSO collision)",
                    label,
                    pkt_idx,
                    total,
                )
            else:
                logger.info("(%s) Broadcast complete: %s packets sent to %s targets", label, total, len(targets))
            self._broadcast_finished(tg)
            return
        collided: list[dict[str, Any]] = []
        for t in targets:
            slot = t["slot"]
            if slot.get("RX_TYPE") != HBPF_SLT_VTERM:
                logger.info(
                    "(%s) QSO detected on %s/TS%s during broadcast (packet %s/%s), removing target",
                    label,
                    t["name"],
                    t["ts"],
                    pkt_idx,
                    total,
                )
                slot["TX_TYPE"] = HBPF_SLT_VTERM
                collided.append(t)
        for t in collided:
            targets.remove(t)
        if not targets:
            self._announcement_running[ann_idx] = False
            self._maybe_end_legacy_voice_report()
            logger.info(
                "(%s) Broadcast stopped: all targets had QSO collision at packet %s/%s",
                label,
                pkt_idx,
                total,
            )
            self._broadcast_finished(tg)
            return
        now = time.time()
        if pkt_idx == 0:
            self._maybe_begin_legacy_voice_report(targets, pkts_by_ts, tg)
        self._send_announcement_packets(targets, pkts_by_ts, pkt_idx, source_id, dst_id, tg, label)
        if next_time is None:
            next_time = now + _FRAME_INTERVAL
        else:
            next_time = next_time + _FRAME_INTERVAL
        delay = max(0.001, next_time - time.time())
        if self._call_later:
            self._call_later(
                delay,
                self._announcement_send_broadcast,
                targets,
                pkts_by_ts,
                pkt_idx + 1,
                source_id,
                dst_id,
                tg,
                ann_idx,
                label,
                next_time,
            )

    def scheduled_announcement(self, ann_idx: int = 0, _retry: int = 0) -> None:
        """Run one scheduled file announcement from ANNOUNCEMENTS[ann_idx]."""
        g = self._config.get("VOICE", {})
        announcements = g.get("ANNOUNCEMENTS") or []
        if ann_idx < 0 or ann_idx >= len(announcements):
            return
        item = announcements[ann_idx]
        if not isinstance(item, dict) or not item.get("ENABLED"):
            return
        label = "ANNOUNCEMENT-{}".format(ann_idx + 1)
        if self._announcement_running.get(ann_idx):
            if _retry == 0:
                logger.debug("(%s) Previous announcement still running, skipping", label)
            return
        mode = item.get("MODE", "interval")
        if mode == "hourly" and _retry == 0:
            now = datetime.now()
            if now.minute != 0:
                return
            if self._announcement_last_hour.get(ann_idx) == now.hour:
                return
        _tg = int(item.get("TG", 0))
        if str(_tg) in self._broadcast_active_tgs and _retry < 60:
            if _retry == 0:
                logger.debug("(%s) Same TG %s already broadcasting, deferring prep", label, _tg)
            if self._call_later:
                self._call_later(3.0 + ann_idx * 0.5, self.scheduled_announcement, ann_idx, _retry + 1)
            return
        _file = str(item.get("FILE") or "").strip()
        _lang = item.get("LANGUAGE", "en_GB")
        if not _file or not _tg:
            return
        _dst_id = bytes_3(_tg)
        _source_id = announcement_item_source_bytes(item, self._config)
        server_id = self._config.get("GLOBAL", {}).get("SERVER_ID", b"\x00\x00\x00\x00")
        if not isinstance(server_id, bytes):
            server_id = bytes_3(int(server_id))
        logger.info("(%s) Playing file: %s to TG %s (both TS, mode: %s, lang: %s)", label, _file, _tg, mode, _lang)
        try:
            _say = self.read_single_file(self._audio_path, _lang, str(_file))
        except Exception as e:
            logger.warning("(%s) Cannot read AMBE file: Audio/%s/ondemand/%s.ambe: %s", label, _lang, _file, e)
            return
        if not _say:
            logger.warning("(%s) AMBE file empty or not found: %s/ondemand/%s.ambe", label, _lang, _file)
            return
        tg_str = str(_tg)
        targets, busy_count = self._build_announcement_targets(_tg, tg_str, label)
        if not targets:
            if busy_count > 0 and _retry < 60:
                if _retry == 0:
                    logger.info(
                        "(%s) All %s target slots busy (QSO active), waiting for QSO to finish...",
                        label,
                        busy_count,
                    )
                if self._call_later:
                    self._call_later(5.0, self.scheduled_announcement, ann_idx, _retry + 1)
                return
            logger.info("(%s) No systems with active bridge for TG %s to send to", label, _tg)
            return
        if mode == "hourly":
            self._announcement_last_hour[ann_idx] = datetime.now().hour
        _say_list = [_say]
        pkt_peer = self._announcement_packet_peer(_tg, targets, server_id)
        pkts_by_ts = {
            1: list(self.pkt_gen(_source_id, _dst_id, pkt_peer, 0, _say_list)),
            2: list(self.pkt_gen(_source_id, _dst_id, pkt_peer, 1, _say_list)),
        }
        ts1_count = sum(1 for t in targets if t["ts"] == 1)
        ts2_count = sum(1 for t in targets if t["ts"] == 2)
        sys_names = ", ".join("{}/TS{}".format(t["name"], t["ts"]) for t in targets[:8])
        if len(targets) > 8:
            sys_names += ", ... +{}".format(len(targets) - 8)
        logger.info(
            "(%s) Broadcasting %s packets to %s targets (TS1:%s TS2:%s): %s",
            label, len(pkts_by_ts[1]), len(targets), ts1_count, ts2_count, sys_names,
        )
        self._announcement_running[ann_idx] = True
        self._enqueue_broadcast('ann', targets, pkts_by_ts, _source_id, _dst_id, _tg, ann_idx, label)

    def scheduled_tts_announcement(self, tts_idx: int = 0, _retry: int = 0) -> None:
        """Run one scheduled TTS announcement from TTS_ANNOUNCEMENTS[tts_idx]."""
        g = self._config.get("VOICE", {})
        tts_list = g.get("TTS_ANNOUNCEMENTS") or []
        if tts_idx < 0 or tts_idx >= len(tts_list):
            return
        item = tts_list[tts_idx]
        if not isinstance(item, dict) or not item.get("ENABLED", False):
            return
        label = "TTS-{}".format(tts_idx + 1)
        if self._tts_running.get(tts_idx):
            if _retry == 0:
                logger.debug("(%s) Previous TTS announcement still running, skipping", label)
            return
        mode = item.get("MODE", "interval")
        if mode == "hourly" and _retry == 0:
            now = datetime.now()
            if now.minute != 0:
                return
            if self._tts_last_hour.get(tts_idx) == now.hour:
                return
        _tg = int(item.get("TG", 0))
        if str(_tg) in self._broadcast_active_tgs and _retry < 60:
            if _retry == 0:
                logger.debug("(%s) Same TG %s already broadcasting, deferring TTS prep", label, _tg)
            if self._call_later:
                self._call_later(3.0 + tts_idx * 0.5, self.scheduled_tts_announcement, tts_idx, _retry + 1)
            return
        _file = str(item.get("FILE") or "").strip()
        _lang = item.get("LANGUAGE", "en_GB")
        self._tts_running[tts_idx] = True
        logger.info("(%s) Starting TTS conversion in background thread for %s", label, _file)
        if self._defer_to_thread:
            d = self._defer_to_thread(self._voice.ensure_tts_ambe, self._config, item, self._audio_path)
            d.addCallback(self._tts_conversion_done, tts_idx, _file, _tg, _lang, mode, label)
            d.addErrback(self._tts_conversion_error, tts_idx, label)
        else:
            try:
                ambe_path = self._voice.ensure_tts_ambe(self._config, item, self._audio_path)
                self._tts_conversion_done(ambe_path, tts_idx, _file, _tg, _lang, mode, label)
            except Exception as e:
                self._tts_conversion_error(e, tts_idx, label)

    def _tts_conversion_done(
        self, ambe_path: str | None, tts_idx: int, _file: str, _tg: int, _lang: str, mode: str, label: str, _retry: int = 0
    ) -> None:
        """After TTS conversion: broadcast like scheduled_announcement."""
        if not ambe_path:
            self._tts_running[tts_idx] = False
            logger.warning("(%s) No AMBE file available for TTS announcement %s", label, _file)
            return
        if str(_tg) in self._broadcast_active_tgs and _retry < 60:
            if _retry == 0:
                logger.debug("(%s) Same TG %s already broadcasting, deferring TTS packet prep", label, _tg)
            if self._call_later:
                self._call_later(3.0 + tts_idx * 0.5, self._tts_conversion_done, ambe_path, tts_idx, _file, _tg, _lang, mode, label, _retry + 1)
            return
        logger.info("(%s) Playing TTS file: %s to TG %s (both TS, mode: %s, lang: %s)", label, _file, _tg, mode, _lang)
        _dst_id = bytes_3(_tg)
        tts_list = self._config.get("VOICE", {}).get("TTS_ANNOUNCEMENTS") or []
        tts_item = tts_list[tts_idx] if 0 <= tts_idx < len(tts_list) else None
        _source_id = announcement_item_source_bytes(
            tts_item if isinstance(tts_item, dict) else None,
            self._config,
        )
        server_id = self._config.get("GLOBAL", {}).get("SERVER_ID", b"\x00\x00\x00\x00")
        if not isinstance(server_id, bytes):
            server_id = bytes_3(int(server_id))
        _file_base = _file.replace(".ambe", "")
        _say = self.read_single_file(self._audio_path, _lang, _file_base)
        if not _say:
            logger.warning("(%s) Cannot read AMBE file: %s", label, ambe_path)
            self._tts_running[tts_idx] = False
            return
        tg_str = str(_tg)
        targets, busy_count = self._build_announcement_targets(_tg, tg_str, label)
        if not targets:
            if busy_count > 0 and _retry < 60:
                if _retry == 0:
                    logger.info(
                        "(%s) All %s target slots busy (QSO active), waiting for QSO to finish...",
                        label,
                        busy_count,
                    )
                if self._call_later:
                    self._call_later(
                        5.0,
                        self._tts_conversion_done,
                        ambe_path,
                        tts_idx,
                        _file,
                        _tg,
                        _lang,
                        mode,
                        label,
                        _retry + 1,
                    )
                return
            self._tts_running[tts_idx] = False
            logger.info("(%s) No systems with active bridge for TG %s to send to", label, _tg)
            return
        if mode == "hourly":
            self._tts_last_hour[tts_idx] = datetime.now().hour
        _say_list = [_say]
        pkt_peer = self._announcement_packet_peer(_tg, targets, server_id)
        pkts_by_ts = {
            1: list(self.pkt_gen(_source_id, _dst_id, pkt_peer, 0, _say_list)),
            2: list(self.pkt_gen(_source_id, _dst_id, pkt_peer, 1, _say_list)),
        }
        logger.info("(%s) Broadcasting %s packets to %s targets", label, len(pkts_by_ts[1]), len(targets))
        self._enqueue_broadcast('tts', targets, pkts_by_ts, _source_id, _dst_id, _tg, tts_idx, label)

    def _tts_conversion_error(self, failure: Any, tts_idx: int, label: str) -> None:
        self._tts_running[tts_idx] = False
        try:
            msg = failure.getErrorMessage()
        except Exception as e:
            logger.warning("(%s) failure.getErrorMessage unavailable: %s", label, e)
            msg = str(failure)
        logger.error("(%s) TTS conversion error: %s", label, msg)

    def _tts_send_broadcast(
        self,
        targets: list[dict[str, Any]],
        pkts_by_ts: dict[int, list[bytes]],
        pkt_idx: int,
        source_id: bytes,
        dst_id: bytes,
        tg: int,
        tts_idx: int,
        label: str,
        next_time: float | None = None,
    ) -> None:
        """Same as _announcement_send_broadcast but clears _tts_running."""
        total = len(pkts_by_ts.get(1, []))
        if pkt_idx >= total or not targets:
            self._mark_slots_free(targets)
            self._maybe_end_legacy_voice_report()
            for t in targets:
                try:
                    obj = t.get("sys_obj")
                    if getattr(obj, "STATUS", None):
                        for sid in list(obj.STATUS.keys()):
                            if sid not in (1, 2):
                                del obj.STATUS[sid]
                except Exception as e:
                    logger.warning("(%s) slot STATUS cleanup failed: %s", label, e)
            self._tts_running[tts_idx] = False
            if not targets:
                logger.info(
                    "(%s) Broadcast aborted at packet %s/%s: all targets removed (QSO collision)",
                    label,
                    pkt_idx,
                    total,
                )
            else:
                logger.info("(%s) Broadcast complete: %s packets sent to %s targets", label, total, len(targets))
            self._broadcast_finished(tg)
            return
        collided: list[dict[str, Any]] = []
        for t in targets:
            slot = t["slot"]
            if slot.get("RX_TYPE") != HBPF_SLT_VTERM:
                logger.info(
                    "(%s) QSO detected on %s/TS%s during broadcast (packet %s/%s), removing target",
                    label,
                    t["name"],
                    t["ts"],
                    pkt_idx,
                    total,
                )
                slot["TX_TYPE"] = HBPF_SLT_VTERM
                collided.append(t)
        for t in collided:
            targets.remove(t)
        if not targets:
            self._tts_running[tts_idx] = False
            self._maybe_end_legacy_voice_report()
            logger.info(
                "(%s) Broadcast stopped: all targets had QSO collision at packet %s/%s",
                label,
                pkt_idx,
                total,
            )
            self._broadcast_finished(tg)
            return
        now = time.time()
        if pkt_idx == 0:
            self._maybe_begin_legacy_voice_report(targets, pkts_by_ts, tg)
        self._send_announcement_packets(targets, pkts_by_ts, pkt_idx, source_id, dst_id, tg, label)
        if next_time is None:
            next_time = now + _FRAME_INTERVAL
        else:
            next_time = next_time + _FRAME_INTERVAL
        delay = max(0.001, next_time - time.time())
        if self._call_later:
            self._call_later(
                delay,
                self._tts_send_broadcast,
                targets,
                pkts_by_ts,
                pkt_idx + 1,
                source_id,
                dst_id,
                tg,
                tts_idx,
                label,
                next_time,
            )

    def read_single_file(self, audio_path: str, lang: str, file_number: str) -> list:
        """Read one AMBE file (e.g. ondemand/{file_number}.ambe). Legacy readSingleFile."""
        return self._voice.read_single_file(audio_path, lang, file_number)

    def play_on_slot(
        self, protocol: Any, system: str, speech: Any, source_id: bytes, dst_id: bytes
    ) -> int:
        """Play a prompt on TS2 of an HBP system from a worker thread; frames sent.

        The thread only paces the frames: each one is sent on the reactor, which
        holds the slot while the prompt plays the way scheduled broadcasts do
        (TX_TYPE=VHEAD), so routed group voice finds it busy instead of going out
        as a second stream on the same slot. A radio keying up, or a call already
        on the slot, stops the prompt; the slot is released when it ends.
        """
        run = _PromptRun()
        _next_time = time.time()
        for pkt in speech:
            if run.stopped:
                break
            _next_time += _FRAME_INTERVAL
            delay = _next_time - time.time()
            if delay > 0.001:
                time.sleep(delay)
            self._call_from_reactor(self._prompt_frame, protocol, system, pkt, source_id, dst_id, run)
        self._call_from_reactor(self._prompt_end, protocol, run)
        return run.sent

    def _prompt_frame(
        self, protocol: Any, system: str, pkt: bytes, source_id: bytes, dst_id: bytes, run: _PromptRun
    ) -> None:
        """Reactor side of ``play_on_slot``: one frame, unless the slot is taken."""
        if run.stopped:
            return
        slot = protocol.STATUS.get(2) if getattr(protocol, "STATUS", None) else None
        if not slot:
            run.stopped = True
            return
        stream_id = pkt[16:20]
        now = time.time()
        if slot_voice_held_by_other_stream(slot, stream_id, now):
            run.stopped = True
            logger.info("(%s) Voice on TS2, stopping server prompt after %s frames", system, run.sent)
            return
        slot["TX_TYPE"] = HBPF_SLT_VHEAD
        slot["TX_STREAM_ID"] = stream_id
        slot["TX_RFS"] = source_id
        slot["TX_TIME"] = now
        run.stream_id = stream_id
        protocol.send_voice_packet(pkt, source_id, dst_id, slot)
        run.sent += 1

    def _prompt_end(self, protocol: Any, run: _PromptRun) -> None:
        """Reactor side of ``play_on_slot``: free the slot if the prompt still holds it."""
        slot = protocol.STATUS.get(2) if getattr(protocol, "STATUS", None) else None
        if slot and run.stream_id is not None and slot.get("TX_STREAM_ID") == run.stream_id:
            slot["TX_TYPE"] = HBPF_SLT_VTERM

    def play_file_on_request(self, file_number: str, system: str) -> None:
        """Play AMBE file on request (legacy playFileOnRequest). TG 9991-9999 triggers this."""
        if not self._get_protocols or not self._call_from_reactor or not self._audio_path:
            return
        protocol = self._get_protocols().get(system)
        if not protocol or not getattr(protocol, "STATUS", None):
            return
        sys_cfg = self._config.get("SYSTEMS", {}).get(system, {})
        lang = sys_cfg.get("ANNOUNCEMENT_LANGUAGE", "en_GB")
        pairs = self.read_single_file(self._audio_path, lang, file_number)
        if not pairs:
            logger.warning("(%s) AMBE file not found or empty: %s/ondemand/%s.ambe", system, lang, file_number)
            return
        logger.info("(%s) Playing on-demand AMBE file: %s (ID: %s)", system, file_number, file_number)
        time.sleep(1)
        _say = [pairs]
        _source_id = self._server_source_id()
        speech = self.pkt_gen(_source_id, bytes_3(9), bytes_4(9), 1, _say)
        time.sleep(1)
        if not protocol.STATUS.get(2):
            return
        _pkt_count = self.play_on_slot(protocol, system, speech, _source_id, bytes_3(9))
        logger.info("(%s) On-demand playback complete: %s (%d packets)", system, file_number, _pkt_count)

    def disconnected_voice(self, system: str) -> None:
        """Send 'disconnected' / 'linked to reflector' voice (legacy disconnectedVoice). Run from thread."""
        if not self._get_protocols or not self._call_from_reactor or not self._audio_path:
            return
        protocol = self._get_protocols().get(system)
        if not protocol or not getattr(protocol, "STATUS", None):
            return
        sys_cfg = self._config.get("SYSTEMS", {}).get(system, {})
        _lang = sys_cfg.get("ANNOUNCEMENT_LANGUAGE", "en_GB")
        words_by_lang = self.get_ambe_words(_lang, self._audio_path)
        if _lang not in words_by_lang:
            return
        words = words_by_lang[_lang]
        silence = words.get("silence")
        if not silence:
            return
        _say = [silence, silence]
        default_refl = int(sys_cfg.get("DEFAULT_REFLECTOR", 0))
        if default_refl > 0:
            _say.append(silence)
            _say.append(words.get("linkedto") or silence)
            _say.append(silence)
            _say.append(words.get("to") or silence)
            _say.append(silence)
            _say.append(silence)
            for digit in str(default_refl):
                _say.append(words.get(digit) or silence)
                _say.append(silence)
        else:
            _say.append(words.get("notlinked") or silence)
        _say.append(silence)
        _source_id = self._server_source_id()
        speech = self.pkt_gen(_source_id, bytes_3(9), bytes_4(9), 1, _say)
        time.sleep(1)
        if not protocol.STATUS.get(2):
            return
        logger.debug("(%s) Sending disconnected voice", system)
        self.play_on_slot(protocol, system, speech, _source_id, bytes_3(9))
        logger.debug("(%s) disconnected voice thread end", system)

    def apply_voice_config(self) -> None:
        """Start/stop announcement and TTS LoopingCalls from ``config["VOICE"]``."""
        g = self._config.get("VOICE", {})
        if not self._start_looping_call:
            return
        announcements = g.get("ANNOUNCEMENTS") or []
        if not isinstance(announcements, list):
            announcements = []
        for ann_idx in list(self._ann_tasks.keys()):
            if ann_idx >= len(announcements) or not (isinstance(announcements[ann_idx], dict) and announcements[ann_idx].get("ENABLED")):
                try:
                    if getattr(self._ann_tasks[ann_idx], "running", False):
                        self._ann_tasks[ann_idx].stop()
                except Exception as e:
                    logger.warning("(VOICE-RELOAD) stop announcement task %s failed: %s", ann_idx + 1, e)
                del self._ann_tasks[ann_idx]
                logger.info("(VOICE-RELOAD) ANNOUNCEMENT-%s stopped", ann_idx + 1)
        for ann_idx, item in enumerate(announcements):
            if not isinstance(item, dict) or not item.get("ENABLED"):
                continue
            label = "ANNOUNCEMENT-{}".format(ann_idx + 1)
            if ann_idx in self._ann_tasks:
                try:
                    if getattr(self._ann_tasks[ann_idx], "running", False):
                        self._ann_tasks[ann_idx].stop()
                except Exception as e:
                    logger.warning("(VOICE-RELOAD) stop %s failed: %s", label, e)
                del self._ann_tasks[ann_idx]
                logger.info("(VOICE-RELOAD) %s stopped", label)
            mode = item.get("MODE", "interval")
            interval = 30.0 if mode == "hourly" else float(item.get("INTERVAL", 60))
            lc = self._start_looping_call(lambda ai=ann_idx: self.scheduled_announcement(ai), interval, False)
            self._ann_tasks[ann_idx] = lc
            logger.info(
                "(VOICE-RELOAD) %s enabled - mode: %s, file: %s, TG: %s",
                label, mode, item.get("FILE"), item.get("TG"),
            )
        tts_list = g.get("TTS_ANNOUNCEMENTS") or []
        if not isinstance(tts_list, list):
            tts_list = []
        for tts_idx in list(self._tts_tasks.keys()):
            if tts_idx >= len(tts_list) or not (isinstance(tts_list[tts_idx], dict) and tts_list[tts_idx].get("ENABLED")):
                try:
                    if getattr(self._tts_tasks[tts_idx], "running", False):
                        self._tts_tasks[tts_idx].stop()
                except Exception as e:
                    logger.warning("(VOICE-RELOAD) stop TTS task %s failed: %s", tts_idx + 1, e)
                del self._tts_tasks[tts_idx]
                logger.info("(VOICE-RELOAD) TTS-%s stopped", tts_idx + 1)
        for tts_idx, item in enumerate(tts_list):
            if not isinstance(item, dict) or not item.get("ENABLED"):
                continue
            label = "TTS-{}".format(tts_idx + 1)
            if tts_idx in self._tts_tasks:
                try:
                    if getattr(self._tts_tasks[tts_idx], "running", False):
                        self._tts_tasks[tts_idx].stop()
                except Exception as e:
                    logger.warning("(VOICE-RELOAD) stop %s failed: %s", label, e)
                del self._tts_tasks[tts_idx]
                logger.info("(VOICE-RELOAD) %s stopped", label)
            mode = item.get("MODE", "interval")
            interval = 30.0 if mode == "hourly" else float(item.get("INTERVAL", 60))
            lc = self._start_looping_call(lambda ti=tts_idx: self.scheduled_tts_announcement(ti), interval, False)
            self._tts_tasks[tts_idx] = lc
            logger.info(
                "(VOICE-RELOAD) %s enabled - mode: %s, file: %s, TG: %s",
                label, mode, item.get("FILE"), item.get("TG"),
            )
