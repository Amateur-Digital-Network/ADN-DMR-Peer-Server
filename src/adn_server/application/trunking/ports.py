# ADN DMR Peer Server - application trunking ports
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

"""Ports of the Tier III use cases, kept with the feature (as plugins keep theirs)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from .registry import Registration, Site


class SiteLink(ABC):
    @abstractmethod
    def send(self, site: Site, payload: bytes) -> bool: ...

    @abstractmethod
    def connected(self, site: Site) -> bool: ...


class TrunkRegistrationStore(ABC):
    @abstractmethod
    def load(self, path: str) -> list[Registration]: ...

    @abstractmethod
    def save(self, path: str, registrations: Iterable[Registration]) -> None: ...

    @abstractmethod
    def last_write_failed(self) -> bool: ...
