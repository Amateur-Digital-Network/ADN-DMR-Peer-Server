# ADN DMR Peer Server - application subscription obp source ops
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

"""Store-native OBP source leg ensure."""

from __future__ import annotations

from typing import Any

from adn_server.application.ports import SubscriptionStore
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


def _tgid_match(entry_tgid: Any, dst_id_b: bytes, dst_int: int) -> bool:
    if entry_tgid == dst_id_b:
        return True
    try:
        return int_id(entry_tgid or b"\x00\x00\x00") == dst_int
    except (TypeError, ValueError):
        return False


def obp_source_needs_ensure(
    store: SubscriptionStore,
    system_name: str,
    relay_table_key: str,
    dst_int: int,
) -> bool:
    """True when a bridge table exists but OBP lacks an ACTIVE TS1 source for ``dst_int``."""
    active_tables = set(store.relay_tables_with_active_source(system_name, 1, dst_int))
    pending_keys: list[str] = []
    for key in (relay_table_key, "#" + relay_table_key):
        if store.has_table(key):
            pending_keys.append(key)
    if not pending_keys:
        return False
    return any(key not in active_tables for key in pending_keys)


def ensure_obp_source_for_tg_store(
    store: SubscriptionStore,
    system_name: str,
    relay_table_key: str,
    dst_id_b: bytes,
    dst_int: int,
    now: float,
) -> None:
    """Ensure OBP has ACTIVE TS1 source row in main and #reflector tables."""
    for key in (relay_table_key, "#" + relay_table_key):
        if not store.has_table(key):
            continue
        channel_tgid = dst_int
        patched = False
        for sub in list(store.snapshot()):
            if sub.system.value != system_name:
                continue
            if sub.table_key() != key:
                continue
            if int(sub.channel.slot) != 1:
                continue
            if not _tgid_match(bytes_3(int(sub.target_tgid.value)), dst_id_b, dst_int):
                continue
            if not sub.is_active():
                sub.state.phase = SubscriptionPhase.ACTIVE
                store.upsert(sub)
            patched = True
            break
        if not patched:
            store.upsert(
                Subscription(
                    channel=AudioChannel(tgid=TgId(channel_tgid), slot=1),  # type: ignore[arg-type]
                    system=SystemId(system_name),
                    target_tgid=TgId(dst_int),
                    role=SubscriptionRole.ECHO,
                    policy=ActivationPolicy.INBAND,
                    state=SubscriptionState(phase=SubscriptionPhase.ACTIVE, timer_expires_at=now),
                    relay_table_key=key if key.startswith("#") else None,
                    timeout_seconds=None,
                    triggers=InbandTriggers(),
                )
            )
