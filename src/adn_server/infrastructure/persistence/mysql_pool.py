# ADN DMR Peer Server - infrastructure persistence mysql pool helpers
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

"""Shared Twisted adbapi MySQL pool helpers."""

from __future__ import annotations

import logging
import sys
from typing import Any

from twisted.enterprise import adbapi

from adn_server.infrastructure.persistence.database_config import validate_database_settings

logger = logging.getLogger(__name__)

_PEER_DYNAMIC_TGS_MIGRATION = "004_peer_dynamic_tgs"
_PEER_DYNAMIC_TGS_NEED_RELOAD_MIGRATION = "006_peer_dynamic_tgs_need_reload"

_MYSQL_HINTS: dict[int, str] = {
    1045: "check DATABASE.DB_USERNAME and DB_PASSWORD in adn-server.yaml",
    1049: "database does not exist — create it or fix DATABASE.DB_NAME",
    1044: "user lacks permission on the database — fix MariaDB grants or DATABASE settings",
    2003: "cannot reach MariaDB — check DB_SERVER/DB_PORT and that MariaDB is running",
    2002: "connection refused — is MariaDB listening on DB_SERVER:DB_PORT?",
}

_CREATE_SCHEMA_MIGRATIONS = """CREATE TABLE IF NOT EXISTS schema_migrations (
    id VARCHAR(64) PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) DEFAULT CHARSET=utf8mb4"""

_CREATE_PEER_DYNAMIC_TGS = """CREATE TABLE IF NOT EXISTS peer_dynamic_tgs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    int_id INT NOT NULL,
    system_name VARCHAR(50) NOT NULL,
    slot TINYINT NOT NULL,
    tgid INT NOT NULL,
    single_mode TINYINT(1) NOT NULL,
    expires_at INT NULL,
    updated_at INT NOT NULL,
    UNIQUE KEY uq_peer_dynamic (int_id, system_name, slot, tgid),
    KEY idx_peer_system (int_id, system_name),
    KEY idx_expires (expires_at)
) DEFAULT CHARSET=utf8mb4"""


def describe_mysql_error(err: Exception) -> str:
    """Turn a MySQL/MariaDB exception into an actionable startup message."""
    args = getattr(err, "args", ())
    if len(args) >= 2 and isinstance(args[0], int):
        code, msg = int(args[0]), str(args[1])
        hint = _MYSQL_HINTS.get(code, "check the DATABASE block in adn-server.yaml")
        return f"MySQL error {code}: {msg} ({hint})"
    return str(err)


def abort_database_startup(reason: str) -> bool:
    """Log and print a fatal DATABASE message; return False for ensure_database_sync."""
    headline = f"(DATABASE) ADN DMR Peer Server not started — {reason}"
    logger.critical(headline)
    logger.critical(
        "(DATABASE) Dynamic TG persistence requires MariaDB; fix DATABASE in adn-server.yaml and restart"
    )
    print(headline, file=sys.stderr)
    return False


def create_mysql_pool(
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


def _migration_applied(cursor: Any, migration_id: str) -> bool:
    cursor.execute("SELECT 1 FROM schema_migrations WHERE id = %s", (migration_id,))
    return cursor.fetchone() is not None


def _mark_migration(cursor: Any, migration_id: str) -> None:
    cursor.execute(
        "INSERT IGNORE INTO schema_migrations (id) VALUES (%s)",
        (migration_id,),
    )


def _column_exists(cursor: Any, table: str, column: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (table, column),
    )
    return cursor.fetchone() is not None


def _table_exists(cursor: Any, table: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (table,),
    )
    return cursor.fetchone() is not None


def _index_exists(cursor: Any, table: str, index_name: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME = %s",
        (table, index_name),
    )
    return cursor.fetchone() is not None


def _ensure_peer_dynamic_tgs_on_cursor(cursor: Any) -> None:
    """Server-owned table; same migration ids/DLL as adn-monitor."""
    cursor.execute(_CREATE_SCHEMA_MIGRATIONS)
    if not _migration_applied(cursor, _PEER_DYNAMIC_TGS_MIGRATION):
        cursor.execute(_CREATE_PEER_DYNAMIC_TGS)
        _mark_migration(cursor, _PEER_DYNAMIC_TGS_MIGRATION)
        logger.info("(DATABASE) applied migration %s (peer_dynamic_tgs)", _PEER_DYNAMIC_TGS_MIGRATION)
    if not _migration_applied(cursor, _PEER_DYNAMIC_TGS_NEED_RELOAD_MIGRATION):
        if _table_exists(cursor, "peer_dynamic_tgs"):
            if not _column_exists(cursor, "peer_dynamic_tgs", "need_reload"):
                cursor.execute(
                    "ALTER TABLE peer_dynamic_tgs "
                    "ADD COLUMN need_reload TINYINT(1) NOT NULL DEFAULT 0"
                )
            if not _index_exists(cursor, "peer_dynamic_tgs", "idx_need_reload_peer"):
                cursor.execute(
                    "ALTER TABLE peer_dynamic_tgs "
                    "ADD INDEX idx_need_reload_peer (need_reload, int_id, system_name)"
                )
        _mark_migration(cursor, _PEER_DYNAMIC_TGS_NEED_RELOAD_MIGRATION)
        logger.info(
            "(DATABASE) applied migration %s (peer_dynamic_tgs.need_reload)",
            _PEER_DYNAMIC_TGS_NEED_RELOAD_MIGRATION,
        )


def ensure_database_sync(
    host: str,
    user: str,
    password: str,
    db_name: str,
    port: int,
) -> bool:
    """Blocking startup: connect, ensure ``peer_dynamic_tgs`` exists (idempotent)."""
    settings = {
        "db_server": host,
        "db_username": user,
        "db_password": password,
        "db_name": db_name,
        "db_port": port,
    }
    config_err = validate_database_settings(settings)
    if config_err:
        return abort_database_startup(config_err)
    try:
        import MySQLdb
    except ImportError as err:
        return abort_database_startup(f"mysqlclient package required: {err}")
    try:
        conn = MySQLdb.connect(
            host=host,
            user=user,
            passwd=password,
            db=db_name,
            port=port,
            charset="utf8mb4",
        )
        cur = conn.cursor()
        _ensure_peer_dynamic_tgs_on_cursor(cur)
        conn.commit()
        cur.close()
        conn.close()
        logger.info(
            "(DATABASE) MariaDB OK (%s@%s:%s/%s, peer_dynamic_tgs ready)",
            user, host, port, db_name,
        )
        return True
    except Exception as err:
        return abort_database_startup(describe_mysql_error(err))
