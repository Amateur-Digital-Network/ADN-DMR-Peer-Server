# ADN DMR Peer Server - tests trunking site DMRT experimental
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

"""EXPERIMENTAL DMRT between real Tier III sites, wired as bootstrap wires it."""

from __future__ import annotations

import pytest
from tests.support.dmrtc_wire import dmrt_reply, dmrt_udt
from tests.support.trunk_site_stack import build_trunk_site_stack

pytestmark = pytest.mark.integration

A, A_ADDR = 214000101, ("10.0.1.1", 40001)
B, B_ADDR = 214000102, ("10.0.1.2", 40002)
MS_A, MS_B = 2149050, 2149060
UUID = bytes(range(0xC0, 0xD0))


def test_a_short_message_crosses_sites_and_its_accept_comes_back() -> None:
    """A private UDT from site A reaches site B's address as 0x45, and B's 0x06 reaches A as 0x46."""
    s = build_trunk_site_stack()
    a = s.site("TRUNK", A, A_ADDR)
    b = s.site("TRUNK", B, B_ADDR)
    a.register(MS_A)
    b.register(MS_B)

    udt = dmrt_udt(UUID, MS_A, MS_B, b"73")
    assert a.send(udt) == []
    assert b.dmrt()[-1] == udt[:4] + b"\x45" + udt[5:]

    accept = dmrt_reply(0x06, UUID, MS_B, MS_A)
    assert b.send(accept) == []
    assert a.dmrt()[-1] == accept[:4] + b"\x46" + accept[5:]
