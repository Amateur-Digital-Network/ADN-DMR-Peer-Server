# ADN DMR Peer Server - infrastructure proxy persistence db pool
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

"""Twisted adbapi MySQL pool for proxy self-service (legacy adn-proxy parity)."""

from __future__ import annotations

import logging

from twisted.enterprise import adbapi
from twisted.internet.defer import inlineCallbacks, returnValue

logger = logging.getLogger(__name__)


def create_pool(
    host: str,
    user: str,
    password: str,
    db_name: str,
    port: int,
) -> adbapi.ConnectionPool:
    """Create Twisted adbapi pool using ``MySQLdb`` (``mysqlclient`` package)."""
    return adbapi.ConnectionPool(
        "MySQLdb",
        host=host,
        user=user,
        passwd=password,
        db=db_name,
        port=port,
        charset="utf8mb4",
        # Drop a connection MySQL closed (restart, wait_timeout) instead of reusing it forever.
        cp_reconnect=True,
    )


@inlineCallbacks
def test_db(pool: adbapi.ConnectionPool) -> bool:
    """Verify DB connectivity. Returns True on success."""
    try:
        res = yield pool.runQuery("SELECT 1")
        if res:
            logger.info("(SELF_SERVICE) Database connection test: OK")
        returnValue(True)
    except Exception as err:
        logger.error("(SELF_SERVICE) Database connection error: %s", err)
        returnValue(False)
