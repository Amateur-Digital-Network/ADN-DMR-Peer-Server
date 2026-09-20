# ADN DMR Peer Server - entrypoint
# Copyright (C) 2026  Rodrigo Pérez, CE5RPY <ce5rpy@qmd.cl>
#
# Derived from ADN DMR Server / FreeDMR  / HBlink. Original license:
###############################################################################
# Copyright (C) 2026 Joaquin Madrid Belando, EA5GVK <ea5gvk@gmail.com>
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

"""
ADN DMR Peer Server entrypoint.

Run: python -m adn_server.main [-c adn-server.yaml] [--logging LEVEL]
       python -m adn_server.main --echo [-c adn-echo.yaml]
       python -m adn_server.main --doctor [-c adn-server.yaml]
       python -m adn_server.main --replay capture.pcap [--system OBP-FR]
Config default: adn-server.yaml (or adn-echo.yaml with --echo).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Ensure package is on path when run as __main__
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from adn_server.domain.errors import ConfigError
from adn_server.infrastructure import YamlConfigLoader, setup_logging
from adn_server.infrastructure.bootstrap import run_peer_server
from adn_server.infrastructure.config_normalizer import apply_talker_alias_defaults, normalize_obp_config
from adn_server.infrastructure.doctor import run_doctor
from adn_server.infrastructure.obp_replay import run_replay
from adn_server.infrastructure.echo import run_echo


def _project_root() -> str:
    project_root = os.path.dirname(os.path.abspath(__file__))
    if project_root.endswith("/adn_server"):
        project_root = str(Path(project_root).parent.parent)
    return project_root


def _parse_args() -> argparse.Namespace:
    from adn_server import __version__

    parser = argparse.ArgumentParser(description="ADN DMR Peer Server")
    parser.add_argument("-c", "--config", dest="CONFIG_FILE", default=None, help="Path to YAML config")
    parser.add_argument("--logging", dest="LOG_LEVEL", default=None, help="Override log level")
    parser.add_argument(
        "--echo",
        action="store_true",
        help="Playback mode: run ECHO PEER(s) from adn-echo.yaml (default config)",
    )
    parser.add_argument(
        "--no-proxy",
        action="store_true",
        help="Disable integrated hotspot PROXY even when PROXY is set in config",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Validate config, ports, and peers; exit non-zero on errors",
    )
    parser.add_argument(
        "--replay",
        dest="REPLAY_CAPTURE",
        default=None,
        metavar="CAPTURE.pcap",
        help="Replay OpenBridge frames from a pcap through the ingress and report what it would do",
    )
    parser.add_argument(
        "--system",
        dest="REPLAY_SYSTEM",
        default=None,
        help="With --replay: only this OPENBRIDGE system",
    )
    parser.add_argument(
        "--replay-limit",
        dest="REPLAY_LIMIT",
        type=int,
        default=None,
        help="With --replay: stop after this many datagrams",
    )
    parser.add_argument(
        "--replay-summary",
        action="store_true",
        help="With --replay: print the tally only, not one line per frame",
    )
    parser.add_argument("--version", action="version", version=f"adn-server {__version__}")
    return parser.parse_args()


def _log_copyright(logger) -> None:
    logger.info("\n\nCopyright (c) 2026 Rodrigo Pérez, CE5RPY ce5rpy@qmd.cl")
    logger.info("\n\nCopyright (c) 2026 Joaquin Madrid Belando, EA5GVK ea5gvk@gmail.com")
    logger.info("\nCopyright (c) 2024-2026 Esteban Mackay, HP3ICC setcom40@gmail.com")
    logger.info("\nCopyright (c) 2020 Simon Adlem, G7RZU g7rzu@gb7fr.org.uk")
    logger.info("\nCopyright (c) 2016-2019 Cortney T. Buffington, N0MJS n0mjs@me.com")
    logger.info("\nCopyright (c) 2013, 2014, 2015, 2016, 2018, 2019\n\tThe Regents of the K0USY Group. All rights reserved.")
    logger.debug("\n\n(GLOBAL) Logging system started, anything from here on gets logged")


def main() -> None:
    from adn_server import __version__

    args = _parse_args()
    project_root = _project_root()
    default_config = "adn-echo.yaml" if args.echo else "adn-server.yaml"
    config_path = args.CONFIG_FILE or os.path.join(project_root, default_config)

    if args.doctor:
        sys.exit(
            run_doctor(
                config_path,
                project_root,
                echo=args.echo,
                no_proxy=args.no_proxy,
                version=__version__,
            )
        )

    loader = YamlConfigLoader(project_root)
    try:
        config = loader.load(config_path)
    except ConfigError as exc:
        print(f"(CONFIG) {exc}", file=sys.stderr)
        sys.exit(1)

    if args.REPLAY_CAPTURE:
        normalize_obp_config(config)
        sys.exit(
            run_replay(
                config,
                args.REPLAY_CAPTURE,
                system=args.REPLAY_SYSTEM,
                limit=args.REPLAY_LIMIT,
                summary_only=args.replay_summary,
            )
        )

    if not args.echo:
        apply_talker_alias_defaults(config)

    voice_config_path = os.path.join(os.path.dirname(os.path.abspath(config_path)), "adn-voice.yaml")
    voice_data = loader.load_voice_config(voice_config_path) if not args.echo else None
    if voice_data:
        config.setdefault("VOICE", {}).update(voice_data)

    if args.LOG_LEVEL:
        config.setdefault("LOGGER", {})["LOG_LEVEL"] = args.LOG_LEVEL
    logger = setup_logging(config.get("LOGGER", {}))
    _log_copyright(logger)

    if args.echo:
        run_echo(config, logger=logger)
        return

    run_peer_server(
        config,
        config_path,
        project_root,
        no_proxy=args.no_proxy,
        logger=logger,
        voice_config_path=voice_config_path,
        loader=loader,
    )


if __name__ == "__main__":
    main()
