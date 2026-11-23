# ADN DMR Peer Server - tests infrastructure mysql pool reconnect
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

import sys
import types
from collections.abc import Callable
from typing import Any

import pytest

from adn_server.infrastructure.persistence.mysql_pool import create_mysql_pool
from adn_server.infrastructure.proxy.persistence.db_pool import create_pool


class _GoneAway(Exception):
    pass


class _MysqlServer:
    """Stands in for MariaDB: a restart kills every connection opened before it."""

    def __init__(self) -> None:
        self.generation = 0

    def restart(self) -> None:
        self.generation += 1


class _Connection:
    def __init__(self, server: _MysqlServer) -> None:
        self._server = server
        self._generation = server.generation

    def _check(self) -> None:
        if self._generation != self._server.generation:
            raise _GoneAway(2006, "Server has gone away")

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def commit(self) -> None:
        self._check()

    def rollback(self) -> None:
        self._check()

    def close(self) -> None:
        pass


class _Cursor:
    def __init__(self, conn: _Connection) -> None:
        self._conn = conn

    def execute(self, sql: str, args: Any = None) -> None:
        self._conn._check()

    def fetchall(self) -> list[tuple[int]]:
        return [(1,)]

    def close(self) -> None:
        pass


_FACTORIES: list[Callable[..., Any]] = [create_mysql_pool, create_pool]


@pytest.mark.parametrize("factory", _FACTORIES, ids=lambda f: f.__name__)
def test_pool_recovers_after_mysql_restart(
    monkeypatch: pytest.MonkeyPatch, factory: Callable[..., Any]
) -> None:
    server = _MysqlServer()
    fake = types.ModuleType("MySQLdb")
    fake.connect = lambda **_: _Connection(server)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "MySQLdb", fake)
    pool = factory("localhost", "hbmon", "x", "hbmon", 3306)

    def query() -> Any:
        # The same call runQuery makes on a pool thread.
        return pool._runInteraction(pool._runQuery, "SELECT 1")

    assert query() == [(1,)]
    server.restart()
    with pytest.raises(Exception):
        query()
    assert query() == [(1,)]
