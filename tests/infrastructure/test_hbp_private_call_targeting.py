# ADN DMR Peer Server - tests infrastructure hbp private call targeting
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

"""SUB_MAP-based precise private-call targeting on a MASTER with 3+ peers.

Legacy (hblink.py master_datagramReceived) broadcasts every private call to
every connected peer unconditionally -- the receiving hotspot's own ACL/sub
list is the only filter, so a private call between two unrelated users still
reaches every other hotspot's air interface. _pvt_repeat_targets narrows this:
once SUB_MAP has recorded which peer_id last carried a given subscriber's
traffic, a private call to that subscriber is delivered only to that peer,
never broadcast. Unknown destinations still fall back to broadcast (legacy
parity) -- see test_hbp_repeat_private_call.py."""

from __future__ import annotations

import dataclasses

from tests.harness.deterministic import DeterministicScenario, PacketSpec
from tests.support.hbp_repeat_stack import build_hbp_repeat_stack

from adn_server.domain import bytes_3, bytes_4
from adn_server.infrastructure.hbp_constants import HBPF_DATA_SYNC, RPTC, RPTK, RPTL
from adn_server.infrastructure.twisted_adapters.udp_hbp import _calc_hash, _get_passphrase_bytes

_PEER_TX = bytes_4(730039210)
_PEER_RX = bytes_4(730039101)
_PEER_OTHER = bytes_4(730039199)
_ADDR_TX = ("10.0.0.1", 62001)
_ADDR_RX = ("10.0.0.2", 62002)
_ADDR_OTHER = ("10.0.0.3", 62003)

_DST_SUB = 7304011


def _private_spec() -> PacketSpec:
    return PacketSpec(
        peer_id=730039210,
        rf_src=7300392,
        dst_id=_DST_SUB,
        slot=2,
        call_type="unit",
        stream_id=0xA1B2C3D4,
        payload=b"\x00" * 33,
    )


def _fire_private_call(stack, base: PacketSpec) -> None:
    stack.inject_spec(DeterministicScenario.voice_head_spec(base), _ADDR_TX)
    stack.transport.clear()
    stack.inject_spec(
        DeterministicScenario.voice_burst_spec(base, seq=1, dtype_vseq=1), _ADDR_TX,
    )


def test_private_call_delivered_only_to_last_known_peer_not_broadcast() -> None:
    stack = build_hbp_repeat_stack(talker_alias=True)
    stack.register_peer(_PEER_TX, _ADDR_TX, options="TS2=7304;")
    stack.register_peer(_PEER_RX, _ADDR_RX, options="TS2=7304;")
    stack.register_peer(_PEER_OTHER, _ADDR_OTHER, options="TS2=7304;")
    stack.config["_SUB_MAP"] = {
        bytes_3(_DST_SUB): (stack.system_name, 2, 1_700_000_000.0, _PEER_RX),
    }

    _fire_private_call(stack, _private_spec())

    assert stack.transport.for_addr(_ADDR_RX), "known peer must still receive the call"
    assert not stack.transport.for_addr(_ADDR_OTHER), "uninvolved peer must not see this private call"


def test_private_call_dropped_when_known_peer_no_longer_connected() -> None:
    stack = build_hbp_repeat_stack(talker_alias=True)
    stack.register_peer(_PEER_TX, _ADDR_TX, options="TS2=7304;")
    stack.register_peer(_PEER_OTHER, _ADDR_OTHER, options="TS2=7304;")
    # _PEER_RX was last heard here, but is no longer registered/connected.
    stack.config["_SUB_MAP"] = {
        bytes_3(_DST_SUB): (stack.system_name, 2, 1_700_000_000.0, _PEER_RX),
    }

    _fire_private_call(stack, _private_spec())

    assert not stack.transport.for_addr(_ADDR_OTHER), "must not blast to peers that can't be the destination"


def test_private_call_not_delivered_locally_when_known_on_different_system() -> None:
    stack = build_hbp_repeat_stack(talker_alias=True)
    stack.register_peer(_PEER_TX, _ADDR_TX, options="TS2=7304;")
    stack.register_peer(_PEER_OTHER, _ADDR_OTHER, options="TS2=7304;")
    stack.config["_SUB_MAP"] = {
        bytes_3(_DST_SUB): ("MASTER-B", 2, 1_700_000_000.0, _PEER_RX),
    }

    _fire_private_call(stack, _private_spec())

    assert not stack.transport.for_addr(_ADDR_OTHER), "destination known elsewhere must not repeat locally"


def test_private_call_to_4000_not_broadcast() -> None:
    """TG/ID 4000 is the dynamic-TG reset control code, never a real subscriber --
    it can never appear in _SUB_MAP, so without a special case it always fell into
    the "unknown destination" broadcast fallback and reached every connected peer
    instead of being stopped at the server (dmrd_received's own dst_id == 4000
    guard runs too late: _pvt_repeat_targets/the REPEAT loop already ran)."""
    stack = build_hbp_repeat_stack(talker_alias=True)
    stack.register_peer(_PEER_TX, _ADDR_TX, options="TS2=7304;")
    stack.register_peer(_PEER_RX, _ADDR_RX, options="TS2=7304;")
    stack.register_peer(_PEER_OTHER, _ADDR_OTHER, options="TS2=7304;")

    base = dataclasses.replace(_private_spec(), dst_id=4000)
    _fire_private_call(stack, base)

    assert not stack.transport.for_addr(_ADDR_RX), "TG 4000 must not be broadcast to any peer"
    assert not stack.transport.for_addr(_ADDR_OTHER), "TG 4000 must not be broadcast to any peer"


def test_sub_map_entries_for_peer_purged_on_reconnect() -> None:
    """Once a hotspot finishes (re)logging in, any SUB_MAP entry pointing at it is
    stale -- the radio may have moved to a different hotspot while this one was
    offline and there's no way to tell without hearing it transmit again."""
    stack = build_hbp_repeat_stack(talker_alias=True)
    stack.hbp._config["PASSPHRASE"] = b"test-passphrase"
    stack.config["_SUB_MAP"] = {
        bytes_3(_DST_SUB): (stack.system_name, 2, 1_700_000_000.0, _PEER_RX),
        bytes_3(1234567): (stack.system_name, 1, 1_700_000_000.0, _PEER_OTHER),
    }
    passphrase = _get_passphrase_bytes(stack.hbp._config)

    stack.hbp.datagramReceived(RPTL + _PEER_RX, _ADDR_RX)
    salt = bytes_4(stack.hbp._peers[_PEER_RX]["SALT"])
    stack.hbp.datagramReceived(RPTK + _PEER_RX + _calc_hash(salt, passphrase), _ADDR_RX)
    stack.hbp.datagramReceived(
        RPTC + _PEER_RX + b"CE5RPY  " + b"\x00" * 85 + b"4", _ADDR_RX,
    )

    assert stack.hbp._peers[_PEER_RX]["CONNECTION"] == "YES"
    assert bytes_3(_DST_SUB) not in stack.config["_SUB_MAP"], "entry pointing at the reconnected peer must be gone"
    assert bytes_3(1234567) in stack.config["_SUB_MAP"], "entries for other peers must be untouched"


def _unit_data_specs(dst_id: int) -> list[PacketSpec]:
    """A MOTOTRBO GPS/ARS burst: CSBK preambles (a new stream each), data header, data block."""
    base = dataclasses.replace(_private_spec(), dst_id=dst_id, frame_type=HBPF_DATA_SYNC)
    csbk = [dataclasses.replace(base, stream_id=0xC5B00000 + i, seq=i, dtype_vseq=3) for i in range(3)]
    data = [dataclasses.replace(base, stream_id=0xDA7A0001, seq=3 + i, dtype_vseq=d) for i, d in enumerate((6, 7))]
    return csbk + data


def test_unit_data_to_unknown_destination_not_broadcast_on_shared_master() -> None:
    """A radio's GPS/ARS to an ID never heard here (7141: 7140287 -> 714999) was repeated to
    every hotspot of the shared MASTER, cutting the voice each of them was receiving."""
    stack = build_hbp_repeat_stack(talker_alias=True)
    stack.config["PROXY"] = {"TARGET_SYSTEM": stack.system_name}
    stack.register_peer(_PEER_TX, _ADDR_TX, options="TS2=7304;")
    stack.register_peer(_PEER_RX, _ADDR_RX, options="TS2=7304;")
    stack.register_peer(_PEER_OTHER, _ADDR_OTHER, options="TS2=7304;")

    for spec in _unit_data_specs(714999):
        stack.inject_spec(spec, _ADDR_TX)

    assert not stack.transport.for_addr(_ADDR_RX), "unit data to an unknown ID must not reach other hotspots"
    assert not stack.transport.for_addr(_ADDR_OTHER), "unit data to an unknown ID must not reach other hotspots"


def test_unit_data_to_known_destination_reaches_only_its_peer() -> None:
    stack = build_hbp_repeat_stack(talker_alias=True)
    stack.config["PROXY"] = {"TARGET_SYSTEM": stack.system_name}
    stack.register_peer(_PEER_TX, _ADDR_TX, options="TS2=7304;")
    stack.register_peer(_PEER_RX, _ADDR_RX, options="TS2=7304;")
    stack.register_peer(_PEER_OTHER, _ADDR_OTHER, options="TS2=7304;")
    stack.config["_SUB_MAP"] = {
        bytes_3(_DST_SUB): (stack.system_name, 2, 1_700_000_000.0, _PEER_RX),
    }

    for spec in _unit_data_specs(_DST_SUB):
        stack.inject_spec(spec, _ADDR_TX)

    assert len(stack.transport.for_addr(_ADDR_RX)) == 5, "the destination's hotspot gets the whole burst"
    assert not stack.transport.for_addr(_ADDR_OTHER)


def test_unit_data_waits_while_destination_hotspot_receives_voice() -> None:
    """The destination's hotspot is playing a group call: a data header there would make
    MMDVMHost drop the voice, so the burst is not sent until the call is over."""
    stack = build_hbp_repeat_stack(talker_alias=True)
    stack.config["PROXY"] = {"TARGET_SYSTEM": stack.system_name}
    stack.register_peer(_PEER_TX, _ADDR_TX, options="TS2=7304;")
    stack.register_peer(_PEER_RX, _ADDR_RX, options="TS2=7304;")
    stack.register_peer(_PEER_OTHER, _ADDR_OTHER, options="TS2=7304;")
    stack.config["_SUB_MAP"] = {
        bytes_3(_DST_SUB): (stack.system_name, 2, 1_700_000_000.0, _PEER_RX),
    }
    voice = PacketSpec(peer_id=730039199, rf_src=7300391, dst_id=7304, slot=2, stream_id=0x0E0E0E0E)
    stack.inject_spec(DeterministicScenario.voice_head_spec(voice), _ADDR_OTHER)
    stack.inject_spec(DeterministicScenario.voice_burst_spec(voice, seq=1, dtype_vseq=1), _ADDR_OTHER)
    assert stack.transport.for_addr(_ADDR_RX), "precondition: RX is receiving the group call"
    stack.transport.clear()

    for spec in _unit_data_specs(_DST_SUB):
        stack.inject_spec(spec, _ADDR_TX)
    assert not stack.transport.for_addr(_ADDR_RX), "no data on a hotspot that is playing voice"

    stack.inject_spec(DeterministicScenario.voice_term_spec(voice, seq=2), _ADDR_OTHER)
    stack.transport.clear()
    for spec in _unit_data_specs(_DST_SUB):
        stack.inject_spec(dataclasses.replace(spec, stream_id=spec.stream_id + 0x100, seq=spec.seq + 10), _ADDR_TX)
    assert len(stack.transport.for_addr(_ADDR_RX)) == 5, "once the call is over the burst goes through"
