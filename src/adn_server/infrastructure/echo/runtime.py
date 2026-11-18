# ADN DMR Peer Server - infrastructure echo runtime
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

"""Wire ECHO playback PEER systems at startup."""

from __future__ import annotations

import logging
import signal
import sys
from typing import Any

from twisted.internet import reactor

from adn_server.application.playback_use_cases import PlaybackUseCases
from adn_server.infrastructure import reopen_file_handlers
from adn_server.infrastructure.acl_router import InMemoryAclRouter
from adn_server.infrastructure.config_normalizer import (
    ensure_system_runtime_config,
    normalize_obp_config,
    normalize_peer_config,
)
from adn_server.infrastructure.twisted_adapters.looping import start_loop
from adn_server.infrastructure.twisted_adapters.report_server import ReportServerFactory
from adn_server.infrastructure.twisted_adapters.udp_hbp import HBPProtocolFactory


def run_echo(config: dict[str, Any], *, logger: logging.Logger) -> None:
    """Start playback PEER(s) and run the Twisted reactor (blocks until shutdown)."""
    ensure_system_runtime_config(config)
    normalize_peer_config(config)
    normalize_obp_config(config)

    g = config.setdefault("GLOBAL", {})
    sid = g.get("SERVER_ID", 0)
    g["SERVER_ID"] = (int(sid) & 0xFFFFFFFF).to_bytes(4, "big") if not isinstance(sid, bytes) else sid

    protocols: dict[str, Any] = {}
    router = InMemoryAclRouter()
    report_factory = ReportServerFactory(config)
    systems_cfg = config.get("SYSTEMS", {})
    report_factory.set_systems(systems_cfg)

    if config.get("REPORTS", {}).get("REPORT", False):
        port = config["REPORTS"].get("REPORT_PORT", 4321)
        reactor.listenTCP(port, report_factory)
        logger.info("(REPORT) Report server listening on TCP %s", port)

        def reporting_loop() -> None:
            report_factory.set_systems(config.get("SYSTEMS", {}))
            report_factory.send_config()

        report_interval = config.get("REPORTS", {}).get("REPORT_INTERVAL", 60)
        start_loop(reporting_loop, report_interval, logger)

    def sig_handler(sig: int, _frame: Any) -> None:
        logger.info("SHUTDOWN: ECHO IS TERMINATING WITH SIGNAL %s", sig)
        if reactor.running:
            reactor.stop()

    def sigusr2_reopen_logs(_sig: int, _frame: Any) -> None:
        n = reopen_file_handlers()
        logger.info("(LOGGER) Reopened %s file log handler(s) after SIGUSR2", n)

    def sighup_ignore(_sig: int, _frame: Any) -> None:
        logger.debug("(ECHO) SIGHUP ignored (no config hot-reload; use SIGUSR2 for logrotate)")

    signal.signal(signal.SIGTERM, sig_handler)
    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGUSR2, sigusr2_reopen_logs)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, sighup_ignore)

    logger.info("ADN Echo -- SYSTEM STARTING...")
    started = 0
    for system_name, sys_cfg in systems_cfg.items():
        if not sys_cfg.get("ENABLED", True):
            continue
        if sys_cfg.get("MODE") != "PEER":
            logger.debug("(ECHO) skip %s (MODE=%s; echo only runs PEER systems)", system_name, sys_cfg.get("MODE"))
            continue

        pb = PlaybackUseCases(
            system_name,
            call_later=reactor.callLater,
            get_protocol=lambda sn=system_name: protocols.get(sn),
        )

        protocol = HBPProtocolFactory(
            system_name,
            config,
            report_factory,
            router=router,
            dmrd_received=pb.dmrd_received,
        )
        protocols[system_name] = protocol
        ip = sys_cfg.get("IP", "")
        udp_port = sys_cfg.get("PORT", 56400)
        reactor.listenUDP(udp_port, protocol, interface=ip or "0.0.0.0")
        started += 1
        logger.info(
            "(ECHO) %s PEER on %s:%s → master %s:%s",
            system_name,
            ip or "0.0.0.0",
            udp_port,
            sys_cfg.get("MASTER_IP", "?"),
            sys_cfg.get("MASTER_PORT", "?"),
        )

    if started == 0:
        logger.critical("(ECHO) no enabled PEER systems in config")
        sys.exit("echo config must define at least one enabled SYSTEMS.<name> with MODE: PEER")

    reactor.run()
