# ADN DMR Peer Server - bridge table management
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

"""BRIDGES table lifecycle: create, static TG, OPTIONS refresh (no Twisted)."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from ...domain import bytes_3, bytes_4, int_id
from ...domain.config_coerce import coerce_bool, parse_options_single
from ...domain.dynamic_tg import DynamicTgEntry
from ...domain.hbp_protocol import normalize_fixed_width_ascii
from ..proxy.deployment import is_proxy_inject_only

logger = logging.getLogger(__name__)


class SubscriptionTableMixin:
    """ensure_dynamic_relay, stat/static TG, OPTIONS refresh (RPTO / startup / dmrd)."""

    def sync_restored_dynamic_tgs(
        self,
        peer_id: bytes,
        system_name: str,
        sys_cfg: dict[str, Any],
        entries: list[DynamicTgEntry],
        *,
        now: float,
    ) -> None:
        """After DB restore: bridge timers and missing relay tables (routing concern)."""
        from .dynamic_tg_restore import sync_restored_dynamic_bridges

        sync_restored_dynamic_bridges(
            entries,
            system_name=system_name,
            peer_id=peer_id,
            sys_cfg=sys_cfg,
            sub_store=self._subscription_store,
            ensure_dynamic_relay=self.ensure_dynamic_relay,
            ua_timer_minutes_for_peer=self._ua_timer_minutes_for_peer,
            now=now,
        )

    def ensure_dynamic_relay(
        self,
        _tgid: bytes | int,
        _sourcesystem: str,
        _slot: int,
        _tmout: float,
    ) -> None:
        """Legacy ensure_dynamic_relay: create bridge for TG with entries per MASTER (source ACTIVE on its slot) and OBP."""
        tgid_int = int_id(_tgid) if not isinstance(_tgid, int) else _tgid
        _tgid_s = str(tgid_int)
        _tgid_b = _tgid if isinstance(_tgid, bytes) and len(_tgid) >= 3 else bytes_3(tgid_int)
        if _tgid_s in ("9990", "9991", "9992", "9993", "9994", "9995", "9996", "9997", "9998", "9999"):
            _tmout = 1.0 / 6.0
        from ..subscription.subscription_table_ops import ensure_dynamic_relay_store

        ensure_dynamic_relay_store(
            self._subscription_store,
            tgid_int,
            _sourcesystem,
            _slot,
            float(_tmout),
            self._config.get("SYSTEMS", {}),
            time.time(),
        )

    def make_default_reflector(self, reflector: int, _tmout: float, system: str) -> None:
        """Legacy make_default_reflector: ensure #reflector bridge exists and set system TS2 to ACTIVE/OFF."""
        from ..subscription.subscription_table_ops import make_default_reflector_store

        make_default_reflector_store(
            self._subscription_store,
            reflector,
            float(_tmout),
            system,
            self._config.get("SYSTEMS", {}),
            time.time(),
        )

    def make_static_tg(self, tg: int, ts: int, _tmout: float, system: str) -> None:
        """Legacy make_static_tg: ensure bridge for tg exists and set system/ts to ACTIVE/OFF."""
        from ..subscription.subscription_table_ops import make_static_tg_store

        single_mode = bool(
            self._config.get("SYSTEMS", {}).get(system, {}).get("SINGLE_MODE", False)
        )
        make_static_tg_store(
            self._subscription_store,
            tg,
            ts,
            float(_tmout),
            system,
            self._config.get("SYSTEMS", {}),
            time.time(),
            single_mode=single_mode,
        )
    def reset_static_tg(self, tg: int, ts: int, _tmout: float, system: str) -> None:
        """Legacy reset_static_tg: set system/ts entry to ACTIVE False, TO_TYPE ON."""
        from ..subscription.subscription_table_ops import reset_static_tg_store

        reset_static_tg_store(
            self._subscription_store,
            tg,
            ts,
            float(_tmout),
            system,
            time.time(),
        )

    def reset_all_reflector_system(self, _tmout: float, system: str) -> None:
        """Legacy reset_all_reflector_system: set system's TS2 entry to inactive in every # bridge."""
        from ..subscription.subscription_table_ops import reset_all_reflector_system_store

        reset_all_reflector_system_store(
            self._subscription_store,
            float(_tmout),
            system,
            time.time(),
        )

    def ensure_stat_relay(self, _tgid: bytes) -> None:
        """Legacy ensure_stat_relay: on-the-fly relay bridges for OBP traffic when GEN_STAT_BRIDGES is True."""
        _tgid_s = str(int_id(_tgid))
        from ..subscription.subscription_table_ops import ensure_stat_relay_store

        ensure_stat_relay_store(
            self._subscription_store,
            _tgid,
            self._config.get("SYSTEMS", {}),
            time.time(),
        )
    def deactivate_all_dynamic_relays(self, system_name: str) -> None:
        """Legacy deactivate_all_dynamic_relays: deactivate all non-STAT, non-reflector bridges for a system (TG 4000)."""
        from ..subscription.subscription_table_ops import deactivate_all_dynamic_relays_store

        deactivate_all_dynamic_relays_store(self._subscription_store, system_name)
    def apply_startup_subscriptions(self) -> None:
        """Legacy startup: set default reflectors and static TGs for each MASTER system."""
        prohibited_tgs = (0, 1, 2, 3, 4, 5, 9, 9990, 9991, 9992, 9993, 9994, 9995, 9996, 9997, 9998, 9999)
        logger.debug("(ROUTER) Setting default reflectors")
        for system, sys_cfg in self._config.get("SYSTEMS", {}).items():
            if sys_cfg.get("MODE") != "MASTER":
                continue
            default_ref = int(sys_cfg.get("DEFAULT_REFLECTOR", 0))
            if default_ref not in prohibited_tgs:
                self.make_default_reflector(default_ref, float(sys_cfg.get("DEFAULT_UA_TIMER", 10)), system)
        logger.debug("(ROUTER) setting static TGs")
        for system, sys_cfg in self._config.get("SYSTEMS", {}).items():
            if sys_cfg.get("MODE") != "MASTER":
                continue
            tmout = float(sys_cfg.get("DEFAULT_UA_TIMER", 10))
            ts1_raw = sys_cfg.get("TS1_STATIC") or ""
            ts2_raw = sys_cfg.get("TS2_STATIC") or ""
            ts1 = [s.strip() for s in ts1_raw.split(",") if s.strip()]
            ts2 = [s.strip() for s in ts2_raw.split(",") if s.strip()]
            for tg_s in ts1:
                try:
                    tg = int(tg_s)
                except ValueError:
                    continue
                if tg in prohibited_tgs:
                    continue
                self.make_static_tg(tg, 1, tmout, system)
            for tg_s in ts2:
                try:
                    tg = int(tg_s)
                except ValueError:
                    continue
                if tg in prohibited_tgs:
                    continue
                self.make_static_tg(tg, 2, tmout, system)
        for system, sys_cfg in self._config.get("SYSTEMS", {}).items():
            if sys_cfg.get("MODE") != "MASTER":
                continue
            if not sys_cfg.get("ENABLED", True):
                continue
            self.options_config_for_system(system)
        self._sync_subscription_store()

    def _first_connected_peer_options(self, system_name: str) -> bytes | str | None:
        """First connected peer OPTIONS (legacy options_config peer scan without 26s loop)."""
        protocols = self._get_protocols() if self._get_protocols else {}
        proto = protocols.get(system_name)
        peers = getattr(proto, "_peers", {}) if proto is not None else {}
        if not isinstance(peers, dict):
            return None
        for peer in peers.values():
            if isinstance(peer, dict) and peer.get("CONNECTION") == "YES" and peer.get("OPTIONS"):
                return peer["OPTIONS"]
        return None

    def _options_key_allows(self, system_name: str, parsed: dict[str, Any]) -> bool:
        """Legacy OPTIONS KEY gate (_opt_key on MASTER)."""
        sys_cfg = self._config.get("SYSTEMS", {}).get(system_name, {})
        if sys_cfg.get("_opt_key"):
            if "KEY" not in parsed:
                logger.debug(
                    "(OPTIONS) %s, options key set but no key in options string, skipping",
                    system_name,
                )
                return False
            if sys_cfg["_opt_key"] != parsed.get("KEY"):
                logger.debug(
                    "(OPTIONS) %s, options key set but key sent does not match, skipping",
                    system_name,
                )
                return False
        elif parsed.get("KEY"):
            sys_cfg["_opt_key"] = parsed["KEY"]
            logger.debug(
                "(OPTIONS) %s, _opt_key not set but key sent. Setting to sent key",
                system_name,
            )
        else:
            sys_cfg["_opt_key"] = False
        return True

    def _maybe_update_reflector_from_options(
        self, system_name: str, parsed: dict[str, Any]
    ) -> None:
        """Apply DEFAULT_REFLECTOR / DIAL changes from parsed OPTIONS (legacy options_config)."""
        prohibited_tgs = (0, 1, 2, 3, 4, 5, 9, 9990, 9991, 9992, 9993, 9994, 9995, 9996, 9997, 9998, 9999)
        sys_cfg = self._config.get("SYSTEMS", {}).get(system_name, {})
        raw_timer = parsed.get("DEFAULT_UA_TIMER", sys_cfg.get("DEFAULT_UA_TIMER", 10))
        try:
            timer_int = int(raw_timer)
            tmout = float(35791394 if timer_int == 0 else timer_int)
        except (TypeError, ValueError):
            tmout = float(sys_cfg.get("DEFAULT_UA_TIMER", 10))
        new_ref = int(parsed.get("DEFAULT_REFLECTOR", 0) or 0)
        cur_ref = int(sys_cfg.get("DEFAULT_REFLECTOR", 0) or 0)
        if new_ref == cur_ref:
            return
        if new_ref > 0:
            logger.debug("(OPTIONS) %s default reflector changed, updating", system_name)
            self.reset_all_reflector_system(tmout, system_name)
            self.make_default_reflector(new_ref, tmout, system_name)
        elif new_ref in prohibited_tgs and not bool(new_ref):
            logger.debug("(OPTIONS) %s default reflector is prohibited, ignoring change", system_name)
        else:
            logger.debug("(OPTIONS) %s default reflector disabled, updating", system_name)
            self.reset_all_reflector_system(tmout, system_name)

    def _parse_options_string(self, opt_str: bytes | str) -> dict[str, Any] | None:
        """Parse hotspot OPTIONS / RPTO payload into a normalized options dict."""
        try:
            if isinstance(opt_str, bytes):
                opt_str = opt_str.decode("utf8", errors="replace")
            opt_str = opt_str.rstrip("\x00").encode("ascii", "ignore").decode()
            opt_str = re.sub(r"['\"]", "", opt_str)
            _options: dict[str, Any] = {}
            for x in opt_str.split(";"):
                try:
                    k, v = x.split("=", 1)
                    _options[k.strip()] = v.strip()
                except ValueError:
                    continue
            for old_k, new_k in [
                ("DIAL", "DEFAULT_REFLECTOR"),
                ("TIMER", "DEFAULT_UA_TIMER"),
                ("TS1", "TS1_STATIC"),
                ("TS2", "TS2_STATIC"),
                ("IDENTTG", "OVERRIDE_IDENT_TG"),
                ("VOICETG", "OVERRIDE_IDENT_TG"),
                ("IDENT", "VOICE"),
            ]:
                if old_k in _options:
                    _options[new_k] = _options.pop(old_k)
            for old_k, new_k in [("StartRef", "DEFAULT_REFLECTOR"), ("RelinkTime", "DEFAULT_UA_TIMER")]:
                if old_k in _options:
                    _options[new_k] = _options.pop(old_k)
            if "TS1_1" in _options:
                parts = [_options.pop("TS1_1", "")]
                for i in range(2, 10):
                    p = _options.pop(f"TS1_{i}", None)
                    if p is not None:
                        parts.append(p)
                _options["TS1_STATIC"] = ",".join(parts)
            if "TS2_1" in _options:
                parts = [_options.pop("TS2_1", "")]
                for i in range(2, 10):
                    p = _options.pop(f"TS2_{i}", None)
                    if p is not None:
                        parts.append(p)
                _options["TS2_STATIC"] = ",".join(parts)
            return _options
        except Exception:
            return None

    def _yaml_default_ua_timer(self, sys_cfg: dict[str, Any]) -> float:
        tmout = float(sys_cfg.get("DEFAULT_UA_TIMER", 10))
        return 35791394.0 if tmout <= 0 else tmout

    def _peer_ua_timer_minutes(self, parsed: dict[str, Any], sys_cfg: dict[str, Any]) -> float:
        try:
            value = int(parsed.get("DEFAULT_UA_TIMER", sys_cfg.get("DEFAULT_UA_TIMER", 10)))
        except (TypeError, ValueError):
            return self._yaml_default_ua_timer(sys_cfg)
        if value == 0:
            return 35791394.0
        return float(value)

    def _ua_timer_minutes_for_peer(self, system_name: str, peer_id: bytes) -> float:
        """UA bridge timeout (minutes): transmitting peer OPTIONS TIMER, else YAML default."""
        sys_cfg = self._config.get("SYSTEMS", {}).get(system_name, {})
        protocols = self._get_protocols() if self._get_protocols else {}
        proto = protocols.get(system_name)
        peers = getattr(proto, "_peers", {}) if proto is not None else {}
        if isinstance(peers, dict):
            peer_int = int_id(peer_id)
            for pk, peer in peers.items():
                if not isinstance(peer, dict) or peer.get("CONNECTION") != "YES":
                    continue
                try:
                    pk_int = int_id(pk if isinstance(pk, bytes) else bytes_4(int(pk)))
                except (TypeError, ValueError):
                    continue
                if pk_int != peer_int:
                    continue
                opt = peer.get("OPTIONS")
                if opt is None:
                    break
                parsed = self._parse_options_string(opt)
                if parsed:
                    return self._peer_ua_timer_minutes(parsed, sys_cfg)
                break
        return self._yaml_default_ua_timer(sys_cfg)

    def _connected_peer_options_strings(self, system_name: str) -> list[bytes | str]:
        protocols = self._get_protocols() if self._get_protocols else {}
        proto = protocols.get(system_name)
        peers = getattr(proto, "_peers", {}) if proto is not None else {}
        options: list[bytes | str] = []
        if not isinstance(peers, dict):
            return options
        for peer in peers.values():
            if not isinstance(peer, dict) or peer.get("CONNECTION") != "YES":
                continue
            opt = peer.get("OPTIONS")
            if opt is not None:
                options.append(opt)
        return options

    def _static_tg_timer_maps_for_master(
        self,
        system_name: str,
        *,
        peer_options: bytes | str | None = None,
    ) -> tuple[dict[int, float], dict[int, float]]:
        """Per-TG TIMER (minutes) from each peer OPTIONS; never merged with max() across peers."""
        sys_cfg = self._config.get("SYSTEMS", {}).get(system_name, {})
        ts1_timers: dict[int, float] = {}
        ts2_timers: dict[int, float] = {}
        if peer_options is not None:
            parsed = self._parse_options_static_tgs(peer_options, sys_cfg)
            if parsed is not None:
                peer_tmout, ts1_list, ts2_list = parsed
                for tg in ts1_list:
                    ts1_timers[tg] = peer_tmout
                for tg in ts2_list:
                    ts2_timers[tg] = peer_tmout
            return ts1_timers, ts2_timers
        for opt in self._connected_peer_options_strings(system_name):
            parsed = self._parse_options_static_tgs(opt, sys_cfg)
            if parsed is None:
                continue
            peer_tmout, ts1_list, ts2_list = parsed
            for tg in ts1_list:
                if tg not in ts1_timers:
                    ts1_timers[tg] = peer_tmout
            for tg in ts2_list:
                if tg not in ts2_timers:
                    ts2_timers[tg] = peer_tmout
        return ts1_timers, ts2_timers

    def _options_static_apply_fingerprint(self, system_name: str) -> str:
        """Fingerprint for duplicate RPTO short-circuit (includes runtime SINGLE_MODE)."""
        sys_cfg = self._config.get("SYSTEMS", {}).get(system_name, {})
        merged = self._merged_static_tg_lists_for_master(system_name)
        if merged is not None:
            ts1_nums, ts2_nums = merged
            new_ts1 = ",".join(str(x) for x in ts1_nums)
            new_ts2 = ",".join(str(x) for x in ts2_nums)
        else:
            new_ts1 = str(sys_cfg.get("TS1_STATIC") or "").strip()
            new_ts2 = str(sys_cfg.get("TS2_STATIC") or "").strip()
        return (
            f"{new_ts1}|{new_ts2}|"
            f"{int(bool(sys_cfg.get('SINGLE_MODE', False)))}"
        )

    def _options_static_lists_valid(self, opt_str: bytes | str) -> bool:
        """Legacy: malformed TS1/TS2 in OPTIONS aborts static bridge refresh."""
        from adn_server.application.report.payloads import peer_options_static_valid

        return peer_options_static_valid(opt_str)

    def _should_apply_system_single_from_options(self, system_name: str) -> bool:
        """Whether peer OPTIONS may overwrite system ``SINGLE_MODE`` (legacy single-hotspot only).

        Inject-only proxy and multi-peer masters keep YAML ``SINGLE_MODE`` for bridge
        timers / in-band signalling; per-peer ``SINGLE`` still applies to downlink via
        ``peer_single_mode()``.
        """
        sys_cfg = self._config.get("SYSTEMS", {}).get(system_name, {})
        if is_proxy_inject_only(self._config, system_name):
            return False
        try:
            max_peers = int(sys_cfg.get("MAX_PEERS", 1))
        except (TypeError, ValueError):
            max_peers = 1
        return max_peers <= 1

    def _apply_master_runtime_options(self, system_name: str, _options: dict[str, Any]) -> None:
        """Apply SINGLE/TIMER/VOICE/LANG from peer OPTIONS over YAML defaults (legacy options_config).

        Runtime YAML/OPTIONS flags only; bridge legs are updated via the subscription store.
        """
        systems_cfg = self._config.get("SYSTEMS", {})
        sys_cfg = systems_cfg.get(system_name, {})
        if sys_cfg.get("MODE") != "MASTER":
            return
        if "VOICE" in _options and bool(_options["VOICE"]) and (
            sys_cfg.get("VOICE_IDENT") != bool(int(_options["VOICE"]))
        ):
            sys_cfg["VOICE_IDENT"] = bool(int(_options["VOICE"]))
            logger.debug("(OPTIONS) %s - Setting voice ident to %s", system_name, sys_cfg["VOICE_IDENT"])
        if "OVERRIDE_IDENT_TG" in _options and _options["OVERRIDE_IDENT_TG"] and (
            sys_cfg.get("OVERRIDE_IDENT_TG") != int(_options["OVERRIDE_IDENT_TG"])
        ):
            sys_cfg["OVERRIDE_IDENT_TG"] = int(_options["OVERRIDE_IDENT_TG"])
            logger.debug(
                "(OPTIONS) %s - Setting OVERRIDE_IDENT_TG to %s",
                system_name,
                sys_cfg["OVERRIDE_IDENT_TG"],
            )
        if "LANG" in _options and _options["LANG"] != sys_cfg.get("ANNOUNCEMENT_LANGUAGE"):
            sys_cfg["ANNOUNCEMENT_LANGUAGE"] = _options["LANG"]
            logger.debug("(OPTIONS) %s - Setting voice language to %s", system_name, sys_cfg["ANNOUNCEMENT_LANGUAGE"])
        if "SINGLE" in _options and self._should_apply_system_single_from_options(system_name):
            new_single = parse_options_single(_options["SINGLE"])
            if new_single is not None and coerce_bool(sys_cfg.get("SINGLE_MODE", False)) != new_single:
                sys_cfg["SINGLE_MODE"] = new_single
                logger.info("(OPTIONS) %s - Setting SINGLE_MODE to %s", system_name, sys_cfg["SINGLE_MODE"])
        # TIMER is per-peer: applied via make_static_tg for that peer's static TGs only.

    def options_config_for_system(
        self,
        system_name: str,
        peer_options: bytes | str | None = None,
    ) -> None:
        """Update runtime flags and static TG bridges (RPTO or voice path).

        ``peer_options`` from RPTO overrides YAML (inject-only proxy: OPTIONS live on each peer).
        """
        prohibited_tgs = (0, 1, 2, 3, 4, 5, 9, 9990, 9991, 9992, 9993, 9994, 9995, 9996, 9997, 9998, 9999)
        systems_cfg = self._config.get("SYSTEMS", {})
        sys_cfg = systems_cfg.get(system_name, {})
        if sys_cfg.get("MODE") != "MASTER":
            return
        try:
            runtime_source: bytes | str | None = peer_options
            if runtime_source is None and "OPTIONS" in sys_cfg:
                runtime_source = sys_cfg["OPTIONS"]
            if runtime_source is None:
                runtime_source = self._first_connected_peer_options(system_name)
            if runtime_source is not None:
                parsed_runtime = self._parse_options_string(runtime_source)
                if parsed_runtime and self._options_key_allows(system_name, parsed_runtime):
                    self._apply_master_runtime_options(system_name, parsed_runtime)
                    self._maybe_update_reflector_from_options(system_name, parsed_runtime)

            source_opt: bytes | str | None = peer_options
            if source_opt is None and "OPTIONS" in sys_cfg:
                source_opt = sys_cfg["OPTIONS"]
            if source_opt is not None and not self._options_static_lists_valid(source_opt):
                self._sync_subscription_store()
                return

            merged = self._merged_static_tg_lists_for_master(system_name)
            _fp = self._options_static_apply_fingerprint(system_name)
            if sys_cfg.get("_options_static_apply_fp") == _fp:
                if merged is not None:
                    ts1_nums, ts2_nums = merged
                    yaml_tmout = self._yaml_default_ua_timer(sys_cfg)
                    ts1_timers, ts2_timers = self._static_tg_timer_maps_for_master(
                        system_name,
                        peer_options=peer_options if peer_options is not None else None,
                    )
                    if peer_options is not None:
                        refresh_ts1 = set(ts1_timers)
                        refresh_ts2 = set(ts2_timers)
                    else:
                        refresh_ts1 = set(ts1_nums)
                        refresh_ts2 = set(ts2_nums)
                    for tg in refresh_ts1:
                        if tg in prohibited_tgs:
                            continue
                        self.make_static_tg(tg, 1, ts1_timers.get(tg, yaml_tmout), system_name)
                    for tg in refresh_ts2:
                        if tg in prohibited_tgs:
                            continue
                        self.make_static_tg(tg, 2, ts2_timers.get(tg, yaml_tmout), system_name)
                self._restore_prohibited_static_bridge_legs(system_name)
                self._sync_subscription_store()
                return
            if merged is None:
                # Echo TGs (9990–9999) are excluded from merged lists but still need restore.
                self._restore_prohibited_static_bridge_legs(system_name)
                self._sync_subscription_store()
                return
            ts1_nums, ts2_nums = merged
            yaml_tmout = self._yaml_default_ua_timer(sys_cfg)
            if peer_options is not None:
                ts1_timers, ts2_timers = self._static_tg_timer_maps_for_master(
                    system_name, peer_options=peer_options
                )
            else:
                ts1_timers, ts2_timers = self._static_tg_timer_maps_for_master(system_name)
            new_ts1 = ",".join(str(x) for x in ts1_nums)
            new_ts2 = ",".join(str(x) for x in ts2_nums)
            if re.search(r"[^\d,]", new_ts1) or re.search(r"[^\d,]", new_ts2):
                return
            # Legacy: reset TGs that were removed (bridge_master.py 1736-1767)
            old_ts1 = str(sys_cfg.get("TS1_STATIC") or "").strip()
            old_ts2 = str(sys_cfg.get("TS2_STATIC") or "").strip()
            new_ts1_set: set[int] = set()
            new_ts2_set: set[int] = set()
            for x in new_ts1.split(","):
                if x.strip():
                    try:
                        new_ts1_set.add(int(x))
                    except ValueError:
                        pass
            for x in new_ts2.split(","):
                if x.strip():
                    try:
                        t = int(x)
                        if t != 0 and t < 16777215:
                            new_ts2_set.add(t)
                    except ValueError:
                        pass
            for tg_s in old_ts1.split(","):
                if not tg_s.strip():
                    continue
                try:
                    tg = int(tg_s)
                    if tg not in new_ts1_set:
                        self.reset_static_tg(tg, 1, ts1_timers.get(tg, yaml_tmout), system_name)
                except ValueError:
                    pass
            for tg_s in old_ts2.split(","):
                if not tg_s.strip():
                    continue
                try:
                    tg = int(tg_s)
                    if tg not in new_ts2_set and tg != 0 and tg < 16777215:
                        self.reset_static_tg(tg, 2, ts2_timers.get(tg, yaml_tmout), system_name)
                except ValueError:
                    pass
            peer_ts1_set = set(ts1_timers) if peer_options is not None else None
            peer_ts2_set = set(ts2_timers) if peer_options is not None else None
            for tg in ts1_nums:
                if tg in prohibited_tgs:
                    continue
                if peer_ts1_set is not None and tg not in peer_ts1_set:
                    continue
                self.make_static_tg(tg, 1, ts1_timers.get(tg, yaml_tmout), system_name)
            for tg in ts2_nums:
                if tg == 0 or tg >= 16777215 or tg in prohibited_tgs:
                    continue
                if peer_ts2_set is not None and tg not in peer_ts2_set:
                    continue
                self.make_static_tg(tg, 2, ts2_timers.get(tg, yaml_tmout), system_name)
            systems_cfg[system_name]["TS1_STATIC"] = new_ts1
            systems_cfg[system_name]["TS2_STATIC"] = new_ts2
            systems_cfg[system_name]["_options_static_apply_fp"] = self._options_static_apply_fingerprint(
                system_name
            )
            if new_ts1 or new_ts2:
                logger.info("(OPTIONS) %s static TGs applied: TS1=%s TS2=%s", system_name, new_ts1 or "-", new_ts2 or "-")
            self._restore_prohibited_static_bridge_legs(system_name)
            self._sync_subscription_store()
        except Exception as e:
            logger.debug("(OPTIONS) options_config_for_system %s: %s", system_name, e)

    def _static_tg_lists_from_runtime_cfg(self, sys_cfg: dict[str, Any]) -> tuple[list[int], list[int]] | None:
        """Build static TG lists from TS1_STATIC / TS2_STATIC (updated when peers send RPTO)."""
        prohibited_tgs = (0, 1, 2, 3, 4, 5, 9, 9990, 9991, 9992, 9993, 9994, 9995, 9996, 9997, 9998, 9999)
        ts1_list: list[int] = []
        ts2_list: list[int] = []
        for tg_s in str(sys_cfg.get("TS1_STATIC") or "").split(","):
            if not tg_s.strip():
                continue
            try:
                tg = int(tg_s.strip())
            except ValueError:
                continue
            if tg in prohibited_tgs:
                continue
            ts1_list.append(tg)
        for tg_s in str(sys_cfg.get("TS2_STATIC") or "").split(","):
            if not tg_s.strip():
                continue
            try:
                tg = int(tg_s.strip())
            except ValueError:
                continue
            if tg in prohibited_tgs:
                continue
            if tg == 0 or tg >= 16777215:
                continue
            ts2_list.append(tg)
        if not ts1_list and not ts2_list:
            return None
        return (ts1_list, ts2_list)

    def _parse_options_static_tgs(self, opt_str: str, sys_cfg: dict) -> tuple[float, list[int], list[int]] | None:
        """Parse OPTIONS string; return (tmout, ts1_tg_list, ts2_tg_list) or None. Used to apply static TGs to an existing bridge."""
        try:
            if isinstance(opt_str, bytes):
                opt_str = opt_str.decode("utf8", errors="replace")
            opt_str = opt_str.rstrip("\x00").encode("ascii", "ignore").decode()
            opt_str = re.sub(r"['\"]", "", opt_str)
            _options: dict[str, Any] = {}
            for x in opt_str.split(";"):
                try:
                    k, v = x.split("=", 1)
                    _options[k.strip()] = v.strip()
                except ValueError:
                    continue
            for old_k, new_k in [("DIAL", "DEFAULT_REFLECTOR"), ("TIMER", "DEFAULT_UA_TIMER"), ("TS1", "TS1_STATIC"), ("TS2", "TS2_STATIC")]:
                if old_k in _options:
                    _options[new_k] = _options.pop(old_k)
            if "TS1_1" in _options:
                parts = [_options.pop("TS1_1", "")]
                for i in range(2, 10):
                    p = _options.pop(f"TS1_{i}", None)
                    if p is not None:
                        parts.append(p)
                _options["TS1_STATIC"] = ",".join(parts)
            if "TS2_1" in _options:
                parts = [_options.pop("TS2_1", "")]
                for i in range(2, 10):
                    p = _options.pop(f"TS2_{i}", None)
                    if p is not None:
                        parts.append(p)
                _options["TS2_STATIC"] = ",".join(parts)
            _tmout = float(int(_options.get("DEFAULT_UA_TIMER", sys_cfg.get("DEFAULT_UA_TIMER", 10))))
            if _tmout <= 0:
                _tmout = 35791394
            ts1_list: list[int] = []
            ts2_list: list[int] = []
            ts1_raw = str(_options.get("TS1_STATIC") or "").strip()
            ts2_raw = str(_options.get("TS2_STATIC") or "").strip()
            if ts1_raw and not re.search(r"[^\d,]", ts1_raw):
                for tg_s in ts1_raw.split(","):
                    try:
                        tg1 = int(tg_s.strip())
                        if tg1 not in (0, 1, 2, 3, 4, 5, 9, 9990, 9991, 9992, 9993, 9994, 9995, 9996, 9997, 9998, 9999):
                            ts1_list.append(tg1)
                    except ValueError:
                        pass
            if ts2_raw and not re.search(r"[^\d,]", ts2_raw):
                for tg_s in ts2_raw.split(","):
                    try:
                        tg2 = int(tg_s.strip())
                        if 0 < tg2 < 16777215 and tg2 not in (9990, 9991, 9992, 9993, 9994, 9995, 9996, 9997, 9998, 9999):
                            ts2_list.append(tg2)
                    except ValueError:
                        pass
            return (_tmout, ts1_list, ts2_list)
        except Exception:
            return None

    def _merged_static_tg_lists_for_master(
        self, system_name: str
    ) -> tuple[list[int], list[int]] | None:
        """Union static TG ids from runtime YAML and every connected peer RPTO (inject proxy)."""
        sys_cfg = self._config.get("SYSTEMS", {}).get(system_name, {})
        if sys_cfg.get("MODE") != "MASTER":
            return None
        ts1_set: set[int] = set()
        ts2_set: set[int] = set()
        runtime = self._static_tg_lists_from_runtime_cfg(sys_cfg)
        if runtime is not None:
            ts1_list, ts2_list = runtime
            ts1_set.update(ts1_list)
            ts2_set.update(ts2_list)
        yaml_ts1, yaml_ts2 = sys_cfg.get("_YAML_STATIC") or ((), ())
        ts1_set.update(int(tg) for tg in yaml_ts1)
        ts2_set.update(int(tg) for tg in yaml_ts2)
        protocols = self._get_protocols() if self._get_protocols else {}
        proto = protocols.get(system_name)
        peers = getattr(proto, "_peers", {}) if proto is not None else {}
        if isinstance(peers, dict):
            for peer in peers.values():
                if not isinstance(peer, dict) or peer.get("CONNECTION") != "YES":
                    continue
                opt = peer.get("OPTIONS")
                if opt is None:
                    continue
                if isinstance(opt, bytes):
                    opt_str = opt.decode("utf8", errors="replace")
                else:
                    opt_str = str(opt)
                parsed = self._parse_options_static_tgs(opt_str, sys_cfg)
                if parsed is None:
                    continue
                _peer_tmout, ts1_list, ts2_list = parsed
                ts1_set.update(ts1_list)
                ts2_set.update(ts2_list)
        if not ts1_set and not ts2_set and "OPTIONS" in sys_cfg:
            parsed = self._parse_options_static_tgs(sys_cfg["OPTIONS"], sys_cfg)
            if parsed is not None:
                _peer_tmout, ts1_list, ts2_list = parsed
                ts1_set.update(ts1_list)
                ts2_set.update(ts2_list)
        if not ts1_set and not ts2_set:
            return None
        return (sorted(ts1_set), sorted(ts2_set))

    def apply_static_tg_to_bridge(self, tg_int: int) -> None:
        """Activate MASTER legs for a TG: static (OPTIONS/runtime) or dynamic UA sessions."""
        from .helpers import master_dynamic_tg_slots

        systems_cfg = self._config.get("SYSTEMS", {})
        for _system in systems_cfg:
            sys_cfg = systems_cfg.get(_system, {})
            if sys_cfg.get("MODE") != "MASTER":
                continue
            if not sys_cfg.get("ENABLED", True):
                continue
            parsed = self._merged_static_tg_lists_for_master(_system)
            ts1_list, ts2_list = parsed or ([], [])
            ts1_timers, ts2_timers = self._static_tg_timer_maps_for_master(_system)
            yaml_tmout = self._yaml_default_ua_timer(sys_cfg)
            activated_slots: set[int] = set()
            if tg_int in ts1_list:
                self.make_static_tg(tg_int, 1, ts1_timers.get(tg_int, yaml_tmout), _system)
                activated_slots.add(1)
            if tg_int in ts2_list:
                self.make_static_tg(tg_int, 2, ts2_timers.get(tg_int, yaml_tmout), _system)
                activated_slots.add(2)
            for slot in master_dynamic_tg_slots(sys_cfg, tg_int) - activated_slots:
                self.make_static_tg(tg_int, slot, yaml_tmout, _system)

    def log_connected_systems_and_tgs(self) -> None:
        """Periodic debug: log each system, connection state, and static TGs (TS1/TS2). Only emits at DEBUG level."""
        if not logger.isEnabledFor(logging.DEBUG):
            return
        systems_cfg = self._config.get("SYSTEMS", {})
        protocols = self._get_protocols() if self._get_protocols else {}
        lines: list[str] = ["(DEBUG) Systems and TGs:"]
        for name in sorted(systems_cfg.keys()):
            cfg = systems_cfg.get(name, {})
            mode = cfg.get("MODE", "?")
            enabled = cfg.get("ENABLED", True)
            en = "enabled" if enabled else "disabled"
            parts = [f"  {name}: {mode} ({en})"]
            if mode == "MASTER":
                proto = protocols.get(name)
                peers = getattr(proto, "_peers", {}) if proto else {}
                connected = [p for p in peers.values() if p.get("CONNECTION") == "YES"]
                parts.append(f"peers_connected={len(connected)}")
                if connected:
                    def _cs(c):
                        return normalize_fixed_width_ascii(c.get("CALLSIGN")) or "?"
                    parts.append(
                        "peers=[%s]"
                        % ", ".join("%s/%s" % (p.get("RADIO_ID", "?"), _cs(p)) for p in connected[:10])
                    )
                    if len(connected) > 10:
                        parts[-1] = parts[-1].rstrip("]") + f", +{len(connected) - 10} more]"
                ts1 = (cfg.get("TS1_STATIC") or "").strip()
                ts2 = (cfg.get("TS2_STATIC") or "").strip()
                if ts1 or ts2:
                    parts.append("TS1=%s TS2=%s" % (ts1 or "-", ts2 or "-"))
            elif mode == "PEER":
                proto = protocols.get(name)
                conn = getattr(proto, "_stats", {}).get("CONNECTION", "?") if proto else "?"
                parts.append("connection=%s" % conn)
            lines.append("".join(parts))
        if len(lines) > 1:
            logger.debug("\n".join(lines))
