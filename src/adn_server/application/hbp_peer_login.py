# ADN DMR Peer Server - HBP PEER login and keepalive
# Copyright (C) 2026  Rodrigo Pérez, CE5RPY <ce5rpy@qmd.cl>
#
# Derived from ADN DMR Server / FreeDMR  / HBlink. Original license:
###############################################################################
# Copyright (C) 2020 Simon Adlem, G7RZU <g7rzu@gb7fr.org.uk>
# Copyright (C) 2016-2019 Cortney T. Buffington, N0MJS <n0mjs@me.com>
#
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

"""HBP PEER login (RPTL/RPTK/RPTC/RPTO) and keepalive (RPTPING) towards a MASTER."""

from __future__ import annotations

import logging
import time
from binascii import a2b_hex as bhex
from collections.abc import Callable
from hashlib import sha256
from typing import Any

from ..domain import int_id
from ..domain.hbp_protocol import (
    MSTC,
    MSTN,
    MSTP,
    RPTA,
    RPTC,
    RPTCL,
    RPTK,
    RPTL,
    RPTO,
    RPTPING,
)

_CONFIG_FIELDS = (
    "RADIO_ID", "CALLSIGN", "RX_FREQ", "TX_FREQ", "TX_POWER", "COLORCODE", "LATITUDE", "LONGITUDE",
    "HEIGHT", "LOCATION", "DESCRIPTION", "SLOTS", "URL", "SOFTWARE_ID", "PACKAGE_ID",
)


class PeerLogin:
    """Login state machine of one HBP PEER; state and counters live in the shared stats dict."""

    def __init__(
        self,
        system: str,
        config: dict[str, Any],
        stats: dict[str, Any],
        send: Callable[[bytes], None],
        log: logging.Logger,
        on_salt: Callable[[bytes], bytes | None] | None = None,
    ) -> None:
        self._system = system
        self._config = config
        self._stats = stats
        self._send = send
        self._log = log
        # Default key is the passphrase hash; a gateway can return None and send_key() later.
        self._on_salt = on_salt or self._passphrase_key

    def _passphrase_key(self, salt: bytes) -> bytes:
        return bhex(sha256(b"".join([salt, self._config.get("PASSPHRASE", b"")])).hexdigest())

    def _own(self, peer_id: bytes) -> bool:
        return bool(self._config.get("LOOSE")) or peer_id == self._config.get("RADIO_ID")

    def tick(self, max_missed: int, before_login: Callable[[], None] | None = None) -> None:
        stats = self._stats
        if stats.get("PING_OUTSTANDING"):
            stats["NUM_OUTSTANDING"] = stats.get("NUM_OUTSTANDING", 0) + 1
        if stats.get("CONNECTION") != "YES" or stats.get("NUM_OUTSTANDING", 0) >= max_missed:
            stats["PINGS_SENT"] = 0
            stats["PINGS_ACKD"] = 0
            stats["NUM_OUTSTANDING"] = 0
            stats["PING_OUTSTANDING"] = False
            stats["CONNECTION"] = "RPTL_SENT"
            if before_login:
                before_login()
            self._send(b"".join([RPTL, self._config["RADIO_ID"]]))
            self._log.info("(%s) Sending login request to master %s:%s", self._system, self._config.get("MASTER_IP"), self._config.get("MASTER_PORT"))
        if stats.get("CONNECTION") == "YES":
            self._send(b"".join([RPTPING, self._config["RADIO_ID"]]))
            stats["PINGS_SENT"] = stats.get("PINGS_SENT", 0) + 1
            stats["PING_OUTSTANDING"] = True

    def send_key(self, digest: bytes) -> None:
        """RPTK: the answer to the MASTER's salt."""
        self._send(b"".join([RPTK, self._config["RADIO_ID"], digest]))
        self._stats["CONNECTION"] = "AUTHENTICATED"

    def send_options(self) -> None:
        if self._stats.get("CONNECTION") == "YES" and self._config.get("OPTIONS"):
            self._send(b"".join([RPTO, self._config["RADIO_ID"], self._config["OPTIONS"]]))

    def close(self) -> None:
        if self._stats.get("CONNECTION") == "YES":
            self._send(b"".join([RPTCL, self._config["RADIO_ID"]]))
        self._stats["CONNECTION"] = "NO"

    def datagram(self, command: bytes, data: bytes) -> bool:
        """Handle a MASTER control packet; False if the command is not a login/keepalive one."""
        stats = self._stats
        if command == MSTN:
            if self._own(data[6:10]):
                self._log.warning("(%s) MSTNAK Received. Resetting connection to the Master.", self._system)
                stats["CONNECTION"] = "NO"
                stats["CONNECTED"] = time.time()
        elif command == RPTA:
            self._rpta(data)
        elif command == MSTP:
            if self._own(data[7:11]):
                stats["PING_OUTSTANDING"] = False
                stats["NUM_OUTSTANDING"] = 0
                stats["PINGS_ACKD"] = stats.get("PINGS_ACKD", 0) + 1
        elif command == MSTC:
            if self._own(data[5:9]):
                stats["CONNECTION"] = "NO"
                self._log.info("(%s) MSTCL Recieved", self._system)
        else:
            return False
        return True

    def _rpta(self, data: bytes) -> None:
        stats = self._stats
        state = stats.get("CONNECTION")
        if state == "RPTL_SENT":
            salt = data[6:10]
            self._log.info("(%s) Repeater Login ACK Received with 32bit ID: %s", self._system, int_id(salt))
            digest = self._on_salt(salt)
            if digest is not None:
                self.send_key(digest)
            return
        if state not in ("AUTHENTICATED", "CONFIG-SENT", "OPTIONS-SENT"):
            return
        if not self._own(data[6:10]):
            stats["CONNECTION"] = "NO"
            self._log.error("(%s) Master ACK Contained wrong ID - Connection Reset", self._system)
        elif state == "AUTHENTICATED":
            self._log.info("(%s) Repeater Authentication Accepted", self._system)
            self._send(b"".join([RPTC, b"".join(self._config.get(k, b"") for k in _CONFIG_FIELDS)]))
            stats["CONNECTION"] = "CONFIG-SENT"
            self._log.info("(%s) Repeater Configuration Sent", self._system)
        elif state == "CONFIG-SENT":
            self._log.info("(%s) Repeater Configuration Accepted", self._system)
            if self._config.get("OPTIONS"):
                self._send(b"".join([RPTO, self._config["RADIO_ID"], self._config["OPTIONS"]]))
                stats["CONNECTION"] = "OPTIONS-SENT"
                self._log.info("(%s) Sent options: (%s)", self._system, self._config["OPTIONS"])
            else:
                stats["CONNECTION"] = "YES"
                stats["CONNECTED"] = time.time()
                self._log.info("(%s) Connection to Master Completed", self._system)
        else:
            self._log.info("(%s) Repeater Options Accepted", self._system)
            stats["CONNECTION"] = "YES"
            stats["CONNECTED"] = time.time()
            self._log.info("(%s) Connection to Master Completed with options", self._system)
