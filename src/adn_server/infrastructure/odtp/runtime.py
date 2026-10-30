# ADN DMR Peer Server - ODTP gateway runtime
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

"""ODTP gateway runtime (``adn-server --odtp -c adn-odtp.yaml``)."""

from __future__ import annotations

import logging
import signal
import socket
from typing import Any

from twisted.internet import reactor

from ...application.odtp.gateway import OdtpGateway
from ...application.odtp.ports import OdtpSettings
from .. import reopen_file_handlers
from ..twisted_adapters.looping import start_loop
from .callsigns import CallsignFile
from .udp import OdtpListener, ReactorClock, TwistedMasterLinks

TICK_S = 5.0  # keepalives to the MASTER and session timeouts
DNS_REFRESH_S = 600.0


def odtp_settings(cfg: dict[str, Any]) -> OdtpSettings:
    """OdtpSettings from the ODTP section; absent keys keep their defaults."""
    keys = {
        "MASTER_IP": "master_ip", "MASTER_PORT": "master_port", "PEER_SUFFIX": "peer_suffix",
        "MAX_SESSIONS": "max_sessions", "MAX_SESSIONS_PER_IP": "max_sessions_per_ip",
        "MAX_LOGINS_PER_MINUTE": "max_logins_per_minute", "SMS_FORMAT": "sms_format",
        "SMS_PREAMBLES": "sms_preambles", "LOCATION_DST": "location_dst",
    }
    return OdtpSettings(**{field: cfg[key] for key, field in keys.items() if key in cfg})


def run_odtp(config: dict[str, Any], *, logger: logging.Logger) -> None:
    """Start the ODTP listener and run the Twisted reactor (blocks until shutdown)."""
    cfg = config.get("ODTP", {})
    settings = odtp_settings(cfg)
    master_ip = socket.gethostbyname(settings.master_ip)  # a connected UDP socket needs an address
    subscribers = cfg.get("SUBSCRIBERS_FILE") or ""
    listener = OdtpListener()
    links = TwistedMasterLinks(settings.master_ip, master_ip, settings.master_port)
    gateway = OdtpGateway(
        settings,
        listener,
        links,
        ReactorClock(),
        CallsignFile(subscribers) if subscribers else (lambda _dmr_id: ""),
    )
    listener.gateway = gateway
    listen_ip, listen_port = cfg.get("LISTEN_IP", "0.0.0.0"), cfg.get("LISTEN_PORT", 54006)
    reactor.listenUDP(listen_port, listener, interface=listen_ip)
    start_loop(gateway.tick, TICK_S, logger, name="odtp sessions")
    if master_ip != settings.master_ip:
        start_loop(links.refresh, DNS_REFRESH_S, logger, now=False, name="odtp MASTER address")

    def stop() -> None:
        logger.info("SHUTDOWN: ODTP GATEWAY IS TERMINATING")
        gateway.close()  # CLOSE to the terminals and RPTCL to the MASTER: UDP writes go out at once

    def reopen_logs(_sig: int, _frame: Any) -> None:
        logger.info("(LOGGER) Reopened %s file log handler(s) after SIGUSR2", reopen_file_handlers())

    # Twisted's own SIGTERM/SIGINT handlers stop the reactor; this runs on any stop.
    reactor.addSystemEventTrigger("before", "shutdown", stop)
    signal.signal(signal.SIGUSR2, reopen_logs)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, lambda *_: logger.debug("(ODTP) SIGHUP ignored (restart to apply config)"))

    logger.info(
        "(ODTP) gateway on %s:%s -> MASTER %s:%s (peer suffix %s, callsigns %s)",
        listen_ip, listen_port, settings.master_ip, settings.master_port, settings.peer_suffix, subscribers or "off",
    )
    reactor.run()
