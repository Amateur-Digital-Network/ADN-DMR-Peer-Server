# ADN DMR Peer Server - application subscription subscription table ops
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

"""Store-native bridge table mutations: OPTIONS, static TG, UA, reflectors."""

from __future__ import annotations

import logging
from typing import Any

from adn_server.application.ports import SubscriptionStore
from adn_server.application.subscription.routing_table_export import _legacy_to_type
from adn_server.domain import bytes_3, int_id
from adn_server.domain.subscription import (
    ActivationPolicy,
    AudioChannel,
    InbandTriggers,
    Subscription,
    SubscriptionPhase,
    SubscriptionRole,
    SubscriptionState,
    SystemId,
    TgId,
)

logger = logging.getLogger(__name__)

_SERVICE_TG_STRS = frozenset(
    str(t) for t in (9990, 9991, 9992, 9993, 9994, 9995, 9996, 9997, 9998, 9999)
)


def _effective_tmout_minutes(tgid_int: int, tmout: float) -> float:
    if str(tgid_int) in _SERVICE_TG_STRS:
        return 1.0 / 6.0
    return tmout


def _table_has_legs(store: SubscriptionStore, table_key: str) -> bool:
    return store.has_table(table_key)


def _remove_table(store: SubscriptionStore, table_key: str) -> None:
    for sub in store.legs_in_table(table_key):
        store.remove(sub.subscription_id)


def _find_leg(
    store: SubscriptionStore,
    system: str,
    table_key: str,
    slot: int,
) -> Subscription | None:
    system_id = SystemId(system)
    for sub in store.snapshot():
        if sub.system == system_id and sub.table_key() == table_key and int(sub.channel.slot) == slot:
            return sub
    return None


def _upsert_inband_sink(
    store: SubscriptionStore,
    *,
    system: str,
    table_key: str,
    channel_tgid: int,
    slot: int,
    target_tgid: int,
    active: bool,
    timeout_sec: float,
    now: float,
    timer_at: float | None = None,
    on_trigger: bytes | None = None,
    relay_table_key: str | None = None,
) -> None:
    trigger = on_trigger if on_trigger is not None else bytes_3(target_tgid)
    timer = timer_at
    if timer is None:
        timer = now + timeout_sec if active else now
    store.upsert(
        Subscription(
            channel=AudioChannel(tgid=TgId(channel_tgid), slot=slot),  # type: ignore[arg-type]
            system=SystemId(system),
            target_tgid=TgId(target_tgid),
            role=SubscriptionRole.SINK,
            policy=ActivationPolicy.INBAND,
            state=SubscriptionState(
                phase=SubscriptionPhase.ACTIVE if active else SubscriptionPhase.IDLE,
                timer_expires_at=timer,
            ),
            relay_table_key=relay_table_key,
            timeout_seconds=timeout_sec,
            triggers=InbandTriggers(on=(trigger,), off=(), reset=()),
        )
    )


def _upsert_static_off(
    store: SubscriptionStore,
    *,
    system: str,
    tg: int,
    slot: int,
    active: bool,
    timeout_sec: float,
    timer_at: float,
) -> None:
    tgid_b = bytes_3(tg)
    store.upsert(
        Subscription(
            channel=AudioChannel(tgid=TgId(tg), slot=slot),  # type: ignore[arg-type]
            system=SystemId(system),
            target_tgid=TgId(tg),
            role=SubscriptionRole.SINK,
            policy=ActivationPolicy.STATIC,
            state=SubscriptionState(
                phase=SubscriptionPhase.ACTIVE if active else SubscriptionPhase.IDLE,
                timer_expires_at=timer_at,
            ),
            timeout_seconds=timeout_sec,
            triggers=InbandTriggers(on=(tgid_b,), off=(), reset=()),
        )
    )


def _upsert_obp_none(
    store: SubscriptionStore,
    *,
    system: str,
    table_key: str,
    channel_tgid: int,
    target_tgid: int,
    now: float,
    relay_table_key: str | None = None,
) -> None:
    store.upsert(
        Subscription(
            channel=AudioChannel(tgid=TgId(channel_tgid), slot=1),  # type: ignore[arg-type]
            system=SystemId(system),
            target_tgid=TgId(target_tgid),
            role=SubscriptionRole.ECHO,
            policy=ActivationPolicy.INBAND,
            state=SubscriptionState(phase=SubscriptionPhase.ACTIVE, timer_expires_at=now),
            relay_table_key=relay_table_key,
            timeout_seconds=None,
            triggers=InbandTriggers(),
        )
    )


def _upsert_stat_obp(
    store: SubscriptionStore,
    *,
    system: str,
    tgid_b: bytes,
    now: float,
) -> None:
    tgid_int = int_id(tgid_b)
    store.upsert(
        Subscription(
            channel=AudioChannel(tgid=TgId(tgid_int), slot=1),  # type: ignore[arg-type]
            system=SystemId(system),
            target_tgid=TgId(tgid_int),
            role=SubscriptionRole.PASSIVE_STAT,
            policy=ActivationPolicy.OPENBRIDGE_STAT,
            state=SubscriptionState(phase=SubscriptionPhase.ACTIVE, timer_expires_at=now),
            timeout_seconds=None,
            triggers=InbandTriggers(),
        )
    )


def ensure_dynamic_relay_store(
    store: SubscriptionStore,
    tgid_int: int,
    source_system: str,
    slot: int,
    tmout: float,
    systems_cfg: dict[str, Any],
    now: float,
) -> None:
    """Create bridge table for TG with per-system legs (legacy ensure_dynamic_relay)."""
    tmout_eff = _effective_tmout_minutes(tgid_int, tmout)
    timeout_sec = tmout_eff * 60.0
    table_key = str(tgid_int)
    tgid_b = bytes_3(tgid_int)
    _remove_table(store, table_key)

    for system, sys_cfg in systems_cfg.items():
        mode = sys_cfg.get("MODE")
        if mode == "OPENBRIDGE":
            if 79 <= tgid_int < 9990 or tgid_int > 9999:
                _upsert_obp_none(
                    store,
                    system=system,
                    table_key=table_key,
                    channel_tgid=tgid_int,
                    target_tgid=tgid_int,
                    now=now,
                )
            continue
        if system == source_system:
            if slot == 1:
                _upsert_inband_sink(
                    store,
                    system=system,
                    table_key=table_key,
                    channel_tgid=tgid_int,
                    slot=1,
                    target_tgid=tgid_int,
                    active=True,
                    timeout_sec=timeout_sec,
                    now=now,
                    timer_at=now + timeout_sec,
                    on_trigger=tgid_b,
                )
                _upsert_inband_sink(
                    store,
                    system=system,
                    table_key=table_key,
                    channel_tgid=tgid_int,
                    slot=2,
                    target_tgid=tgid_int,
                    active=False,
                    timeout_sec=timeout_sec,
                    now=now,
                    on_trigger=tgid_b,
                )
            else:
                _upsert_inband_sink(
                    store,
                    system=system,
                    table_key=table_key,
                    channel_tgid=tgid_int,
                    slot=2,
                    target_tgid=tgid_int,
                    active=True,
                    timeout_sec=timeout_sec,
                    now=now,
                    timer_at=now + timeout_sec,
                    on_trigger=tgid_b,
                )
                _upsert_inband_sink(
                    store,
                    system=system,
                    table_key=table_key,
                    channel_tgid=tgid_int,
                    slot=1,
                    target_tgid=tgid_int,
                    active=False,
                    timeout_sec=timeout_sec,
                    now=now,
                    on_trigger=tgid_b,
                )
        else:
            for ts in (1, 2):
                _upsert_inband_sink(
                    store,
                    system=system,
                    table_key=table_key,
                    channel_tgid=tgid_int,
                    slot=ts,
                    target_tgid=tgid_int,
                    active=False,
                    timeout_sec=timeout_sec,
                    now=now,
                    on_trigger=tgid_b,
                )


def make_single_reflector_store(
    store: SubscriptionStore,
    tgid_int: int,
    tmout: float,
    source_system: str,
    systems_cfg: dict[str, Any],
    now: float,
) -> None:
    """Create #tgid reflector bridge (legacy make_single_reflector)."""
    table_key = "#" + str(tgid_int)
    tgid_b = bytes_3(tgid_int)
    tmout_eff = _effective_tmout_minutes(tgid_int, tmout)
    timeout_sec = tmout_eff * 60.0
    _remove_table(store, table_key)

    for system, sys_cfg in systems_cfg.items():
        mode = sys_cfg.get("MODE")
        if mode == "MASTER":
            def_ua = float(sys_cfg.get("DEFAULT_UA_TIMER", 10)) * 60.0
            if system == source_system:
                _upsert_inband_sink(
                    store,
                    system=system,
                    table_key=table_key,
                    channel_tgid=9,
                    slot=2,
                    target_tgid=9,
                    active=True,
                    timeout_sec=timeout_sec,
                    now=now,
                    timer_at=now + timeout_sec,
                    on_trigger=tgid_b,
                    relay_table_key=table_key,
                )
            else:
                _upsert_inband_sink(
                    store,
                    system=system,
                    table_key=table_key,
                    channel_tgid=9,
                    slot=2,
                    target_tgid=9,
                    active=False,
                    timeout_sec=def_ua,
                    now=now,
                    on_trigger=tgid_b,
                    relay_table_key=table_key,
                )
        elif mode == "OPENBRIDGE" and (79 <= tgid_int < 9990 or tgid_int > 9999):
            _upsert_obp_none(
                store,
                system=system,
                table_key=table_key,
                channel_tgid=tgid_int,
                target_tgid=tgid_int,
                now=now,
                relay_table_key=table_key,
            )


def make_default_reflector_store(
    store: SubscriptionStore,
    reflector: int,
    tmout: float,
    system: str,
    systems_cfg: dict[str, Any],
    now: float,
) -> None:
    """Ensure #reflector exists and set system's TS2 leg ACTIVE/OFF (legacy make_default_reflector)."""
    table_key = "#" + str(reflector)
    if not _table_has_legs(store, table_key):
        make_single_reflector_store(store, reflector, tmout, system, systems_cfg, now)
    timeout_sec = tmout * 60.0
    reflector_b = bytes_3(reflector)
    existing = _find_leg(store, system, table_key, 2)
    if existing is not None:
        store.remove(existing.subscription_id)
    store.upsert(
        Subscription(
            channel=AudioChannel(tgid=TgId(9), slot=2),
            system=SystemId(system),
            target_tgid=TgId(9),
            role=SubscriptionRole.SINK,
            policy=ActivationPolicy.STATIC,
            state=SubscriptionState(
                phase=SubscriptionPhase.ACTIVE,
                timer_expires_at=now + timeout_sec,
            ),
            relay_table_key=table_key,
            timeout_seconds=timeout_sec,
            triggers=InbandTriggers(on=(reflector_b,), off=(), reset=()),
        )
    )


def ensure_master_legs_in_tg_bridge_store(
    store: SubscriptionStore,
    tg: int,
    system: str,
    tmout: float,
    systems_cfg: dict[str, Any],
    now: float,
) -> None:
    """Append missing TS1/TS2 MASTER legs on an existing TG table."""
    sys_cfg = systems_cfg.get(system, {})
    if sys_cfg.get("MODE") != "MASTER":
        return
    table_key = str(tg)
    if table_key.startswith("#"):
        return
    if not _table_has_legs(store, table_key):
        return
    tmout_eff = _effective_tmout_minutes(tg, tmout)
    if tmout_eff <= 0:
        tmout_eff = 35791394.0
    timeout_sec = tmout_eff * 60.0
    for ts in (1, 2):
        if _find_leg(store, system, table_key, ts) is None:
            _upsert_inband_sink(
                store,
                system=system,
                table_key=table_key,
                channel_tgid=tg,
                slot=ts,
                target_tgid=tg,
                active=False,
                timeout_sec=timeout_sec,
                now=now,
                timer_at=now + timeout_sec,
            )


def make_static_tg_store(
    store: SubscriptionStore,
    tg: int,
    ts: int,
    tmout: float,
    system: str,
    systems_cfg: dict[str, Any],
    now: float,
    *,
    single_mode: bool,
) -> None:
    """Ensure TG bridge exists and mark system/ts STATIC active (legacy make_static_tg)."""
    table_key = str(tg)
    if not _table_has_legs(store, table_key):
        ensure_dynamic_relay_store(store, tg, system, ts, tmout, systems_cfg, now)
    ensure_master_legs_in_tg_bridge_store(store, tg, system, tmout, systems_cfg, now)
    timeout_sec = tmout * 60.0
    timer_at = now + timeout_sec
    # Legacy make_static_tg always sets ACTIVE True (TO_TYPE OFF); SINGLE_MODE contention
    # is handled in-band during QSO, not by withholding static arm on OPTIONS/RPTO.
    del single_mode  # reserved for callers; parity with bridge_master.make_static_tg
    _upsert_static_off(
        store,
        system=system,
        tg=tg,
        slot=ts,
        active=True,
        timeout_sec=timeout_sec,
        timer_at=timer_at,
    )


def reset_static_tg_store(
    store: SubscriptionStore,
    tg: int,
    ts: int,
    tmout: float,
    system: str,
    now: float,
) -> None:
    """Deactivate static TG leg (legacy reset_static_tg)."""
    table_key = str(tg)
    if not _table_has_legs(store, table_key):
        return
    timeout_sec = tmout * 60.0
    _upsert_inband_sink(
        store,
        system=system,
        table_key=table_key,
        channel_tgid=tg,
        slot=ts,
        target_tgid=tg,
        active=False,
        timeout_sec=timeout_sec,
        now=now,
        timer_at=now + timeout_sec,
    )


def reset_all_reflector_system_store(
    store: SubscriptionStore,
    tmout: float,
    system: str,
    now: float,
) -> None:
    """Deactivate system's TS2 legs in every # bridge (legacy reset_all_reflector_system)."""
    timeout_sec = tmout * 60.0
    for sub in list(store.snapshot()):
        if sub.system.value != system:
            continue
        table_key = sub.table_key()
        if not table_key.startswith("#"):
            continue
        if int(sub.channel.slot) != 2:
            continue
        try:
            on_tgid = int(table_key[1:])
        except ValueError:
            on_tgid = 9
        _upsert_inband_sink(
            store,
            system=system,
            table_key=table_key,
            channel_tgid=9,
            slot=2,
            target_tgid=9,
            active=False,
            timeout_sec=timeout_sec,
            now=now,
            timer_at=now + timeout_sec,
            on_trigger=bytes_3(on_tgid),
            relay_table_key=table_key,
        )


def ensure_stat_relay_store(
    store: SubscriptionStore,
    tgid_b: bytes,
    systems_cfg: dict[str, Any],
    now: float,
) -> None:
    """On-the-fly STAT relay bridge for OBP (legacy ensure_stat_relay)."""
    tgid_int = int_id(tgid_b)
    table_key = str(tgid_int)
    _remove_table(store, table_key)
    for system, sys_cfg in systems_cfg.items():
        if sys_cfg.get("MODE") != "OPENBRIDGE":
            if sys_cfg.get("MODE") == "MASTER":
                tmout = float(sys_cfg.get("DEFAULT_UA_TIMER", 10))
                timeout_sec = tmout * 60.0
                for ts in (1, 2):
                    _upsert_inband_sink(
                        store,
                        system=system,
                        table_key=table_key,
                        channel_tgid=tgid_int,
                        slot=ts,
                        target_tgid=tgid_int,
                        active=False,
                        timeout_sec=timeout_sec,
                        now=now,
                        on_trigger=tgid_b,
                    )
        else:
            _upsert_stat_obp(store, system=system, tgid_b=tgid_b, now=now)


def deactivate_all_dynamic_relays_store(
    store: SubscriptionStore,
    system_name: str,
) -> None:
    """Deactivate non-STAT, non-reflector legs for a system (TG 4000 path)."""
    for sub in list(store.snapshot()):
        if sub.system.value != system_name:
            continue
        relay_table_key = sub.table_key()
        if relay_table_key.startswith("#"):
            continue
        if _legacy_to_type(sub) == "STAT":
            continue
        if not sub.is_active():
            continue
        sub.state.phase = SubscriptionPhase.IDLE
        store.upsert(sub)
        logger.info(
            "(ROUTER) Deactivated dynamic bridge due to TG/ID 4000: System: %s, Bridge: %s, TS: %s, TGID: %s",
            system_name,
            relay_table_key,
            sub.channel.slot,
            int(sub.target_tgid),
        )


def readd_system_after_ua_timer_change_store(
    store: SubscriptionStore,
    system: str,
    tmout: float,
    now: float,
) -> None:
    """Re-add missing TS1/TS2 legs after UA timer change (legacy _readd_system_after_ua_timer_change)."""
    timeout_sec = tmout * 60.0
    table_keys = {sub.table_key() for sub in store.snapshot()}
    for table_key in table_keys:
        if table_key.startswith("#"):
            if _find_leg(store, system, table_key, 2) is None:
                try:
                    _upsert_inband_sink(
                        store,
                        system=system,
                        table_key=table_key,
                        channel_tgid=9,
                        slot=2,
                        target_tgid=9,
                        active=False,
                        timeout_sec=timeout_sec,
                        now=now,
                        timer_at=now + timeout_sec,
                        on_trigger=bytes_3(4000),
                        relay_table_key=table_key,
                    )
                except ValueError:
                    pass
            continue
        if _find_leg(store, system, table_key, 1) is None:
            try:
                tg = int(table_key)
                _upsert_inband_sink(
                    store,
                    system=system,
                    table_key=table_key,
                    channel_tgid=tg,
                    slot=1,
                    target_tgid=tg,
                    active=False,
                    timeout_sec=timeout_sec,
                    now=now,
                    timer_at=now + timeout_sec,
                )
            except ValueError:
                pass
        if _find_leg(store, system, table_key, 2) is None:
            try:
                tg = int(table_key)
                _upsert_inband_sink(
                    store,
                    system=system,
                    table_key=table_key,
                    channel_tgid=tg,
                    slot=2,
                    target_tgid=tg,
                    active=False,
                    timeout_sec=timeout_sec,
                    now=now,
                    timer_at=now + timeout_sec,
                )
            except ValueError:
                pass
