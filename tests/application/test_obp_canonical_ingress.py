# ADN DMR Peer Server - tests OBP canonical ingress helpers
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

"""Core OBP canonical ingress selection for plugin lifecycle."""

from __future__ import annotations

from types import SimpleNamespace

from adn_server.application.routing.helpers import obp_is_canonical_ingress


def _stream_id(n: int) -> bytes:
    return n.to_bytes(4, "big")


def _rf(n: int) -> bytes:
    return n.to_bytes(3, "big")


def _tg(n: int) -> bytes:
    return n.to_bytes(3, "big")


def test_canonical_ingress_single_stream_on_obp() -> None:
    sid = _stream_id(42)
    status = {
        sid: {"1ST": 1.0, "TGID": _tg(0x33420), "RFS": _rf(0x334202)},
    }
    proto = SimpleNamespace(STATUS=status)
    protocols = {"OBP-CL": proto}
    systems_cfg = {"OBP-CL": {"MODE": "OPENBRIDGE"}}
    assert obp_is_canonical_ingress(
        protocols, systems_cfg, "OBP-CL", sid, _tg(0x33420), _rf(0x334202),
    )


def test_canonical_ingress_picks_earliest_same_tg_rf() -> None:
    winner = _stream_id(42)
    loser = _stream_id(99)
    status = {
        winner: {"1ST": 1.0, "TGID": _tg(0x33420), "RFS": _rf(0x334202)},
        loser: {"1ST": 2.0, "TGID": _tg(0x33420), "RFS": _rf(0x334202)},
    }
    proto = SimpleNamespace(STATUS=status)
    protocols = {"OBP-CL": proto}
    systems_cfg = {"OBP-CL": {"MODE": "OPENBRIDGE"}}
    assert obp_is_canonical_ingress(
        protocols, systems_cfg, "OBP-CL", winner, _tg(0x33420), _rf(0x334202),
    )
    assert not obp_is_canonical_ingress(
        protocols, systems_cfg, "OBP-CL", loser, _tg(0x33420), _rf(0x334202),
    )


def test_obp_status_plugin_voice_flag() -> None:
    from adn_server.application.routing.helpers import obp_status_plugin_voice

    sid = _stream_id(42)
    proto = SimpleNamespace(STATUS={sid: {"_plugin_voice": True}})
    protocols = {"OBP-CL": proto}
    assert obp_status_plugin_voice(protocols, "OBP-CL", sid)
    assert not obp_status_plugin_voice(protocols, "OBP-CL", _stream_id(99))


def test_canonical_ingress_cross_obp_winner() -> None:
    sid = _stream_id(42)
    proto_a = SimpleNamespace(
        STATUS={sid: {"1ST": 2.0, "TGID": _tg(0x33420), "RFS": _rf(0x334202)}},
    )
    proto_b = SimpleNamespace(
        STATUS={sid: {"1ST": 1.0, "TGID": _tg(0x33420), "RFS": _rf(0x334202)}},
    )
    protocols = {"OBP-A": proto_a, "OBP-B": proto_b}
    systems_cfg = {"OBP-A": {"MODE": "OPENBRIDGE"}, "OBP-B": {"MODE": "OPENBRIDGE"}}
    assert not obp_is_canonical_ingress(
        protocols, systems_cfg, "OBP-A", sid, _tg(0x33420), _rf(0x334202),
    )
    assert obp_is_canonical_ingress(
        protocols, systems_cfg, "OBP-B", sid, _tg(0x33420), _rf(0x334202),
    )
