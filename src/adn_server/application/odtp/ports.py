# ADN DMR Peer Server - ODTP gateway ports
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

"""What the ODTP gateway needs from the outside: the ODTP socket, MASTER links, a clock, callsigns."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, get_args

SmsFormat = Literal["standard", "tms", "hytera"]
SMS_FORMATS: tuple[str, ...] = get_args(SmsFormat)


class TerminalSocket(Protocol):
    def send(self, data: bytes, addr: tuple[str, int]) -> None: ...


class MasterLink(Protocol):
    def send(self, packet: bytes) -> None: ...

    def close(self) -> None: ...


class MasterLinks(Protocol):
    def open(self, on_datagram: Callable[[bytes], None]) -> MasterLink: ...


class Clock(Protocol):
    def now(self) -> float: ...

    def call_later(self, delay: float, fn: Callable[..., Any], *args: Any) -> Any: ...


@dataclass(frozen=True)
class OdtpSettings:
    master_ip: str = "127.0.0.1"
    master_port: int = 62031
    peer_suffix: int = 98
    max_sessions: int = 500
    max_sessions_per_ip: int = 4
    max_logins_per_minute: int = 60
    sms_format: SmsFormat = "standard"
    sms_preambles: int = 2
    location_dst: int = 0
    location_min_gap_s: float = 10.0
    terminal_timeout_s: float = 30.0
    login_timeout_s: float = 15.0
    max_missed: int = 3

    @property
    def alt_suffix(self) -> int:
        return self.peer_suffix - 1 if self.peer_suffix > 0 else 1
