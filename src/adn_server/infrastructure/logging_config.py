# ADN DMR Peer Server - logging setup
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

"""Configure logging (same format/handlers as legacy log.config_logging)."""

from __future__ import annotations

import logging
import sys
from functools import partial, partialmethod
from typing import Any


def logging_enabled(log_config: dict[str, Any]) -> bool:
    """LOGGER.ENABLED — default True when omitted (legacy configs unchanged)."""
    if "ENABLED" not in log_config:
        return True
    val = log_config.get("ENABLED")
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    return bool(val)


def reopen_file_handlers(logger: logging.Logger | None = None) -> int:
    """Reopen all :class:`logging.FileHandler` streams on *logger* (default: root).

    Use after **logrotate** moves/renames the log file (``create`` + ``postrotate``),
    so new writes go to the current path. Typically invoked from **SIGUSR2**.

    Does not reload YAML or change log level. Returns the number of handlers reopened.
    """
    target = logger if logger is not None else logging.root
    count = 0
    for handler in target.handlers:
        if not isinstance(handler, logging.FileHandler):
            continue
        handler.acquire()
        try:
            handler.flush()
            if handler.stream:
                handler.stream.close()
            handler.stream = handler._open()
            count += 1
        except OSError as e:
            sys.stderr.write(
                "(LOGGER) Could not reopen log file %s: %s\n" % (getattr(handler, "baseFilename", "?"), e)
            )
        finally:
            handler.release()
    return count


def _silence_noisy_library_loggers() -> None:
    """Keep third-party DEBUG chatter out of the server log when LOG_LEVEL=DEBUG."""
    for name in (
        "watchdog",
        "watchdog.observers",
        "watchdog.observers.inotify",
        "watchdog.observers.inotify_buffer",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)


def reapply_log_level(log_config: dict[str, Any]) -> str:
    """Apply LOGGER.LOG_LEVEL after SIGHUP reload (handlers unchanged).

    Returns the level name applied (e.g. ``DEBUG``).
    """
    if not logging_enabled(log_config):
        level = logging.CRITICAL
    else:
        level = getattr(logging, (log_config.get("LOG_LEVEL", "INFO")).upper(), logging.INFO)
    log_name = log_config.get("LOG_NAME", "ADN")
    root = logging.getLogger()
    root.setLevel(level)
    app_logger = logging.getLogger(log_name)
    app_logger.setLevel(level)
    for handler in root.handlers:
        handler.setLevel(level)
    for handler in app_logger.handlers:
        handler.setLevel(level)
    _silence_noisy_library_loggers()
    return logging.getLevelName(level)


def setup_logging(log_config: dict[str, Any]) -> logging.Logger:
    """Configure logging from CONFIG['LOGGER']. Returns application logger."""
    log_name = log_config.get("LOG_NAME", "ADN")

    if not logging_enabled(log_config):
        logging.basicConfig(
            level=logging.CRITICAL,
            handlers=[logging.NullHandler()],
            force=True,
        )
        logger = logging.getLogger(log_name)
        logger.setLevel(logging.CRITICAL)
        return logger

    level = getattr(logging, (log_config.get("LOG_LEVEL", "INFO")).upper(), logging.INFO)
    log_file = log_config.get("LOG_FILE", "/dev/null")
    handlers_cfg = log_config.get("LOG_HANDLERS", "console-timed").strip().split(",")

    logging.TRACE = 5
    logging.addLevelName(logging.TRACE, "TRACE")
    logging.Logger.trace = partialmethod(logging.Logger.log, logging.TRACE)
    logging.trace = partial(logging.log, logging.TRACE)

    handlers: list[logging.Handler] = []
    if "console-timed" in handlers_cfg or "console" in handlers_cfg:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(levelname)s %(asctime)s %(message)s"))
        handlers.append(h)
    if ("file-timed" in handlers_cfg or "file" in handlers_cfg) and log_file and log_file != "/dev/null":
        try:
            h = logging.FileHandler(log_file, encoding="utf-8")
            h.setFormatter(logging.Formatter("%(levelname)s %(asctime)s %(message)s"))
            handlers.append(h)
        except OSError as e:
            # Fallback: warn to stderr if file cannot be opened (e.g. permission, missing dir)
            sys.stderr.write("(LOGGER) Could not open log file %s: %s\n" % (log_file, e))

    # force=True (Python 3.8+) so our handlers replace any already set by other libs (e.g. Twisted)
    logging.basicConfig(level=level, handlers=handlers or [logging.NullHandler()], force=True)
    logger = logging.getLogger(log_name)
    logger.setLevel(level)
    _silence_noisy_library_loggers()
    return logger
