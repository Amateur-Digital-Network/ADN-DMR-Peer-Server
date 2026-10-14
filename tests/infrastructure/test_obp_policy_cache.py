# ADN DMR Peer Server - tests infrastructure obp policy cache
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

"""A bridge's policy is built from configuration and kept until that changes.

Holding it across datagrams is only safe while every input that can move is
known to invalidate it, so each of those is asserted here: a config reload
swaps the dicts, and an alias refresh swaps ``_SERVER_IDS`` on its own.
"""

from __future__ import annotations

import copy

from adn_server.infrastructure.twisted_adapters.udp_hbp import HBPProtocol
from tests.harness.obp_ingress import PEER, build_config


def _protocol() -> HBPProtocol:
    case = {"kind": "v5", "name": "policy", "desc": "policy", "validate_server_ids": True}
    return HBPProtocol("OBP-FR", build_config(case), router=None, dmrd_received=lambda *a, **k: None)


def test_the_policy_is_built_once_while_nothing_moves() -> None:
    protocol = _protocol()
    assert protocol._obp_policy() is protocol._obp_policy()


def test_an_alias_refresh_that_swaps_server_ids_rebuilds_the_policy() -> None:
    """alias_loader replaces _SERVER_IDS in place on the live config, without a
    reload. A policy still holding the old set would admit or refuse by stale data.
    """
    protocol = _protocol()
    before = protocol._obp_policy()
    protocol._CONFIG["_SERVER_IDS"] = {"2084", "7302"}
    after = protocol._obp_policy()
    assert after is not before
    assert after.admission.known_server_prefixes == {"2084", "7302"}


def test_a_config_reload_rebuilds_the_policy() -> None:
    """config_reload carries _SERVER_IDS across untouched, so the identity check
    cannot notice a reload: apply_system_config has to drop the policy itself.
    """
    protocol = _protocol()
    before = protocol._obp_policy()
    reloaded = copy.deepcopy(protocol._CONFIG)
    reloaded["_SERVER_IDS"] = protocol._CONFIG.get("_SERVER_IDS")
    reloaded["SYSTEMS"]["OBP-FR"]["RELAX_CHECKS"] = not before.relax_checks
    protocol.apply_system_config(reloaded)
    after = protocol._obp_policy()
    assert after is not before
    assert after.relax_checks is not before.relax_checks


def test_a_config_reload_rebuilds_the_peer_mesh_config() -> None:
    protocol = _protocol()
    before = protocol._peer_mesh_config()
    assert protocol._peer_mesh_config() is before
    reloaded = copy.deepcopy(protocol._CONFIG)
    reloaded["SYSTEMS"]["OBP-FR"]["PASSPHRASE"] = b"rotated"
    protocol.apply_system_config(reloaded)
    assert protocol._peer_mesh_config().passphrase == b"rotated"


def test_egress_still_reaches_the_peer_after_a_reload() -> None:
    """The cached passphrase signs what leaves, so a stale one would sign wrongly."""
    protocol = _protocol()
    protocol._peer_mesh_config()
    reloaded = copy.deepcopy(protocol._CONFIG)
    reloaded["SYSTEMS"]["OBP-FR"]["TARGET_SOCK"] = PEER
    protocol.apply_system_config(reloaded)
    assert protocol._peer_mesh_config() is not None
