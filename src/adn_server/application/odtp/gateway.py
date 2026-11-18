# ADN DMR Peer Server - ODTP gateway
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

"""ODTP gateway: one TerminalSession per terminal address, with caps on sessions and logins."""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable

from ...domain import odtp
from .ports import Clock, MasterLinks, OdtpSettings, TerminalSocket
from .session import SessionState, TerminalSession

logger = logging.getLogger(__name__)

REJECT_LOG_EVERY_S = 60.0


class OdtpGateway:
    def __init__(
        self,
        settings: OdtpSettings,
        socket: TerminalSocket,
        links: MasterLinks,
        clock: Clock,
        callsigns: Callable[[int], str],
    ) -> None:
        self.settings = settings
        self.socket = socket
        self.links = links
        self.clock = clock
        self.callsigns = callsigns
        self.sessions: dict[tuple[str, int], TerminalSession] = {}
        self.sessions_by_key: dict[int, TerminalSession] = {}
        self._logins: deque[float] = deque()
        self._rejected: dict[str, int] = {}
        self._rejected_logged = -REJECT_LOG_EVERY_S

    def datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        msg = odtp.unpack(data)
        if msg is None:
            return
        msg_type, _flags, _seq, payload = msg
        session = self.sessions.get(addr)
        if session is not None:
            session.received(msg_type, payload)
        elif msg_type == odtp.KEEP_ALIVE and self._admit(addr):
            session = TerminalSession(self, addr)
            session.received(msg_type, payload)
            if session.state not in (SessionState.NEW, SessionState.CLOSED):  # kept once its MASTER link is open
                self.sessions[addr] = session

    def _admit(self, addr: tuple[str, int]) -> bool:
        """A new session opens a login to the MASTER: cap them in total, per IP and per minute."""
        s, now = self.settings, self.clock.now()
        while self._logins and now - self._logins[0] > 60.0:
            self._logins.popleft()
        if len(self.sessions) >= s.max_sessions:
            return self._reject("sessions", addr)
        if sum(1 for a in self.sessions if a[0] == addr[0]) >= s.max_sessions_per_ip:
            return self._reject("sessions per IP", addr)
        if len(self._logins) >= s.max_logins_per_minute:
            return self._reject("logins per minute", addr)
        return True

    def count_login(self) -> None:
        """A session opened its link to the MASTER: only those spend the logins-per-minute cap."""
        self._logins.append(self.clock.now())

    def _reject(self, cap: str, addr: tuple[str, int]) -> bool:
        self._rejected[cap] = self._rejected.get(cap, 0) + 1
        now = self.clock.now()
        if now - self._rejected_logged >= REJECT_LOG_EVERY_S:  # at most one line a minute
            logger.warning("(ODTP) new terminals refused, cap reached: %s (last from %s)",
                           ", ".join(f"{k} x{v}" for k, v in sorted(self._rejected.items())), addr[0])
            self._rejected.clear()
            self._rejected_logged = now
        return False

    def register(self, session: TerminalSession) -> None:
        """A session the MASTER accepted replaces an older one of the same DMR ID or login."""
        old = self.sessions_by_key.get(session.key)
        if old is not None and old is not session:
            self.drop(old, "same DMR ID connected again")
        self.sessions_by_key[session.key] = session

    def drop(self, session: TerminalSession, reason: str, notify: bool = True) -> None:
        logger.info("(ODTP) %s dropped: %s", session, reason)
        session.close(notify)
        if self.sessions.get(session.addr) is session:
            del self.sessions[session.addr]
        if self.sessions_by_key.get(session.key) is session:
            del self.sessions_by_key[session.key]

    def tick(self) -> None:
        now = self.clock.now()
        for session in list(self.sessions.values()):
            session.tick(now)

    def close(self) -> None:
        for session in list(self.sessions.values()):
            self.drop(session, "gateway stopping")
