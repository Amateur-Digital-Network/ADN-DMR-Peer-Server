# ADN DMR Peer Server - infrastructure bootstrap trunking
#
# Copyright (C) 2026  Rodrigo Pérez, CE5RPY <ce5rpy@qmd.cl>
# Copyright (C) 2026  Joaquin Madrid Belando, EA5GVK <ea5gvk@gmail.com>
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

"""Assembly of the Tier III services."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from adn_server.application.ports import AclRouter, SubscriptionStore
from adn_server.application.reporting_use_cases import ReportingUseCases
from adn_server.application.routing_use_cases import RoutingUseCases
from adn_server.application.trunking.legs import SiteLegs
from adn_server.application.trunking.ports import TrunkRegistrationStore
from adn_server.application.trunking.registry import SiteRegistry
from adn_server.application.trunking.signalling import DmrtSignalling
from adn_server.application.trunking.sites import TrunkSites
from adn_server.infrastructure.twisted_adapters.udp_hbp_trunk import ProtocolSiteLink


def build_trunk_sites(
    config: dict[str, Any],
    protocols: Mapping[str, object],
    store: SubscriptionStore,
    acl: AclRouter,
    routing: RoutingUseCases,
    reporting: ReportingUseCases,
    path: str,
    registrations: TrunkRegistrationStore,
) -> TrunkSites:
    """The Tier III services, built when the first TRUNKING system is created."""
    registry = SiteRegistry()
    legs = SiteLegs(
        store,
        config,
        routing.apply_static_tg_to_bridge,
        lambda: reporting.send_routing_table(routing.routing_table_for_report(), incremental=True),
    )
    signalling = DmrtSignalling(config, registry, legs, ProtocolSiteLink(protocols), acl)
    return TrunkSites(config, registry, legs, signalling, registrations, path)
