# ADN DMR Peer Server - application subscription in band signalling ops
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

"""Store-native in-band VTERM signalling."""

from __future__ import annotations

import logging
from typing import Any

from adn_server.application.ports import SubscriptionStore
from adn_server.application.routing.helpers import is_special_tg
from adn_server.application.subscription.routing_table_export import _legacy_to_type
from adn_server.application.subscription.trigger_bytes import dst_in_triggers
from adn_server.domain import bytes_3, int_id
from adn_server.domain.subscription import ActivationPolicy, Subscription, SubscriptionPhase, SystemId

logger = logging.getLogger(__name__)


def _tgid_matches(sub: Subscription, dst_id_b: bytes, dst_group: int) -> bool:
    tgid_b = bytes_3(int(sub.target_tgid))
    return tgid_b == dst_id_b or int(sub.target_tgid) == dst_group


def apply_in_band_signalling_store(
    store: SubscriptionStore,
    system_name: str,
    slot: int,
    dst_id: bytes,
    pkt_time: float,
    systems_cfg: dict[str, Any],
) -> None:
    """Mirror ``RoutingTimerMixin.apply_in_band_signalling`` on the subscription store."""
    dst_group = int_id(dst_id)
    dst_id_b = dst_id if isinstance(dst_id, bytes) and len(dst_id) >= 3 else bytes_3(dst_group)

    for sub in store.list_by_system(SystemId(system_name)):
        relay_table_key = sub.table_key()
        if relay_table_key[:1] == "#" and dst_group != 9:
            continue

        entry_ts = int(sub.channel.slot)
        tgid_b = bytes_3(int(sub.target_tgid))
        to_type = _legacy_to_type(sub)
        active = sub.is_active()
        timeout = sub.timeout_seconds
        timeout_sec = float(timeout) if isinstance(timeout, (int, float)) else 0.0
        changed = False

        if slot == entry_ts and _tgid_matches(sub, dst_id_b, dst_group):
            if (to_type == "ON" and active) or (to_type == "OFF" and not active):
                if timeout_sec:
                    sub.state.timer_expires_at = pkt_time + timeout_sec
                    changed = True
                    logger.info(
                        "(%s) [1] Transmission match for Bridge: %s. Reset timeout to %s",
                        system_name,
                        relay_table_key,
                        sub.state.timer_expires_at,
                    )

        on_list = sub.triggers.on
        reset_list = sub.triggers.reset
        if slot == entry_ts and (
            dst_in_triggers(dst_id_b, dst_group, on_list)
            or dst_in_triggers(dst_id_b, dst_group, reset_list)
        ):
            if dst_in_triggers(dst_id_b, dst_group, on_list):
                if not active:
                    sub.state.phase = SubscriptionPhase.ACTIVE
                    sub.state.timer_expires_at = pkt_time + (timeout_sec or 0.0)
                    changed = True
                    logger.info(
                        "(%s) [2] Bridge: %s, connection changed to state: %s",
                        system_name,
                        relay_table_key,
                        True,
                    )
                    if to_type == "OFF":
                        sub.state.timer_expires_at = pkt_time
                        logger.info(
                            "(%s) [3] Bridge: %s set to \"OFF\" with an on timer rule: timeout timer cancelled",
                            system_name,
                            relay_table_key,
                        )
                if sub.is_active() and to_type == "ON" and timeout_sec:
                    sub.state.timer_expires_at = pkt_time + timeout_sec
                    changed = True
                    logger.info(
                        "(%s) [4] Bridge: %s, timeout timer reset to: %s",
                        system_name,
                        relay_table_key,
                        sub.state.timer_expires_at - pkt_time,
                    )

        sys_cfg = systems_cfg.get(system_name, {})
        is_single_mode = sys_cfg.get("MODE") == "MASTER" and sys_cfg.get("SINGLE_MODE", False)
        off_list = sub.triggers.off

        if is_single_mode:
            if slot == entry_ts and (
                dst_in_triggers(dst_id_b, dst_group, off_list)
                or dst_in_triggers(dst_id_b, dst_group, reset_list)
                or dst_id_b == bytes_3(4000)
                or dst_id_b != tgid_b
            ):
                if (
                    dst_in_triggers(dst_id_b, dst_group, off_list)
                    or dst_id_b != tgid_b
                    or dst_id_b == bytes_3(4000)
                ):
                    # OPTIONS static (OFF) legs stay armed when echo special TG ends (9990–9999).
                    if (
                        sub.policy == ActivationPolicy.STATIC
                        and dst_id_b != tgid_b
                        and is_special_tg(str(dst_group))
                    ):
                        pass
                    elif sub.is_active():
                        sub.state.phase = SubscriptionPhase.IDLE
                        changed = True
                        logger.info(
                            "(%s) [5] Bridge: %s, connection changed to state: %s",
                            system_name,
                            relay_table_key,
                            False,
                        )
                        if to_type == "ON":
                            sub.state.timer_expires_at = pkt_time
                            logger.info(
                                "(%s) [6] Bridge: %s set to \"OFF\" with an on timer rule: timeout timer cancelled",
                                system_name,
                                relay_table_key,
                            )
                if not sub.is_active() and to_type == "OFF" and timeout_sec:
                    sub.state.timer_expires_at = pkt_time + timeout_sec
                    changed = True
                    logger.info(
                        "(%s) [7] Bridge: %s, timeout timer reset to: %s",
                        system_name,
                        relay_table_key,
                        sub.state.timer_expires_at - pkt_time,
                    )
                if sub.is_active() and to_type == "ON" and dst_in_triggers(dst_id_b, dst_group, off_list):
                    sub.state.timer_expires_at = pkt_time
                    changed = True
                    logger.info(
                        "(%s) [8] Bridge: %s set to ON with and \"OFF\" timer rule: timeout timer cancelled",
                        system_name,
                        relay_table_key,
                    )
        elif dst_id_b == bytes_3(4000) and slot == entry_ts:
            is_static_tg = False
            ts1_static = sys_cfg.get("TS1_STATIC") or ""
            ts2_static = sys_cfg.get("TS2_STATIC") or ""
            if ts1_static and slot == 1:
                static_tgs = [int(tg) for tg in ts1_static.split(",") if tg.strip()]
                if dst_group in static_tgs:
                    is_static_tg = True
            elif ts2_static and slot == 2:
                static_tgs = [int(tg) for tg in ts2_static.split(",") if tg.strip()]
                if dst_group in static_tgs:
                    is_static_tg = True

            is_reflector = relay_table_key[:1] == "#"
            if (
                dst_in_triggers(dst_id_b, dst_group, off_list)
                or dst_id_b == bytes_3(4000)
                or (dst_id_b != tgid_b and not is_static_tg and not is_reflector)
            ):
                if sub.is_active():
                    sub.state.phase = SubscriptionPhase.IDLE
                    changed = True
                    logger.info(
                        "(%s) [5b] Bridge: %s, connection changed to state: %s (TG 4000 forced deactivation)",
                        system_name,
                        relay_table_key,
                        False,
                    )
                    if to_type == "ON":
                        sub.state.timer_expires_at = pkt_time
                        logger.info(
                            "(%s) [6b] Bridge: %s set to \"OFF\" with an on timer rule: timeout timer cancelled",
                            system_name,
                            relay_table_key,
                        )
            if not sub.is_active() and to_type == "OFF" and timeout_sec:
                sub.state.timer_expires_at = pkt_time + timeout_sec
                changed = True
                logger.info(
                    "(%s) [7b] Bridge: %s, timeout timer reset to: %s",
                    system_name,
                    relay_table_key,
                    sub.state.timer_expires_at - pkt_time,
                )
            if sub.is_active() and to_type == "ON" and dst_in_triggers(dst_id_b, dst_group, off_list):
                sub.state.timer_expires_at = pkt_time
                changed = True
                logger.info(
                    "(%s) [8b] Bridge: %s set to ON with and \"OFF\" timer rule: timeout timer cancelled",
                    system_name,
                    relay_table_key,
                )

        if changed:
            store.upsert(sub)
