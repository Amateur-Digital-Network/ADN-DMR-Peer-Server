# ADN DMR Peer Server - tests plugin bridge helpers
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

from __future__ import annotations

from adn_server.application.plugins.application.bridge_common import alias_extra


def test_alias_extra_resolves_server_name_with_string_server_ids_key():
    config = {
        "GLOBAL": {"SERVER_ID": 7302},
        "_SERVER_IDS": {"7302": "ADN_7302_Chile"},
        "_PEER_IDS": {},
        "_SUB_IDS": {},
        "_TG_IDS": {},
    }
    extra = alias_extra(config, b"\x00\x01\x1d\x12", b"\x00!\x0a", b"\x00\x03H")
    assert extra["server_name"] == "ADN_7302_Chile"
