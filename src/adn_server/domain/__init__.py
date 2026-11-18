# ADN DMR Peer Server - domain layer
# Copyright (C) 2026  Rodrigo Pérez, CE5RPY <ce5rpy@qmd.cl>
#
# Derived from ADN DMR Server / FreeDMR  / HBlink. Original license:
###############################################################################
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

from .entities import BridgeEntry, StreamState, SystemConfig
from .errors import ACLError, ConfigError, DomainError
from .hbp_protocol import (
    HBPF_DATA_SYNC,
    HBPF_SLT_VHEAD,
    HBPF_SLT_VTERM,
    HBPF_VOICE,
    HBPF_VOICE_SYNC,
    PROTO_VER,
    SLOT_TYPE_VOICE,
    STREAM_TO,
    VER,
    slot_frame_type,
)
from .result import Fail, Result, Success, is_fail
from .subscription import (
    ActivationPolicy,
    AudioChannel,
    Subscription,
    SubscriptionId,
    SubscriptionPhase,
    SubscriptionRole,
    SubscriptionState,
    SystemId,
)
from .value_objects import ID_MAX, ID_MIN, PEER_MAX, CallType, DmrId, Slot, TgId, bytes_3, bytes_4, int_id
from .voice_routing import ForwardLeg, VoiceIngress

__all__ = [
    "BridgeEntry",
    "StreamState",
    "SystemConfig",
    "ActivationPolicy",
    "AudioChannel",
    "Subscription",
    "SubscriptionId",
    "SubscriptionPhase",
    "SubscriptionRole",
    "SubscriptionState",
    "SystemId",
    "VoiceIngress",
    "ForwardLeg",
    "DmrId",
    "TgId",
    "Slot",
    "CallType",
    "bytes_3",
    "bytes_4",
    "int_id",
    "ID_MIN",
    "ID_MAX",
    "PEER_MAX",
    "DomainError",
    "ConfigError",
    "ACLError",
    "Result",
    "Success",
    "Fail",
    "is_fail",
    "HBPF_VOICE",
    "HBPF_VOICE_SYNC",
    "HBPF_DATA_SYNC",
    "HBPF_SLT_VHEAD",
    "HBPF_SLT_VTERM",
    "VER",
    "PROTO_VER",
    "STREAM_TO",
    "SLOT_TYPE_VOICE",
    "slot_frame_type",
]
