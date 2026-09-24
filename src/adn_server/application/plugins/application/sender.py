# ADN DMR Peer Server - plugin DMRD sender
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

"""``ServerContext.send_dmrd`` for one plugin: guards in front of the routing core."""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any, Callable

from ....domain import int_id
from ..domain.send import SendPermission, is_plugin_sendable, parse_dmrd_header, send_permission

logger = logging.getLogger(__name__)


class PluginDmrdSender:
    """Checks each frame against the plugin's live permission, then hands it to the reactor.

    The permission is read from the server config on every call, so a SIGHUP that
    removes or narrows ``PLUGINS.send.<plugin>`` takes effect at once. Safe to call
    from any thread; delivery always happens on the reactor.
    """

    def __init__(
        self,
        plugin: str,
        server_config: dict[str, Any],
        deliver: Callable[[bytes, str], Any],
        call_from_reactor: Callable[..., Any],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._plugin = plugin
        self._config = server_config
        self._deliver = deliver
        self._call_from_reactor = call_from_reactor
        self._clock = clock
        self._lock = threading.Lock()
        self._tokens = math.inf  # starts full: clamped to the rate on first use
        self._refilled = clock()
        self._streams_seen: set[bytes] = set()
        self.dropped = 0

    def __call__(self, pkt: bytes) -> bool:
        """Queue one DMRD frame for routing; False (and logged) when a guard rejects it."""
        permission = send_permission(self._config, self._plugin)
        if permission is None:
            return self._reject("not allowed to send (PLUGINS.send)")
        header = parse_dmrd_header(bytes(pkt)) if isinstance(pkt, (bytes, bytearray)) else None
        if header is None:
            return self._reject("not a DMRD frame")
        if not is_plugin_sendable(header.call_type, header.frame_type, header.dtype_vseq):
            return self._reject("only unit data may be sent")
        rf_src = int_id(header.rf_src)
        stream_id, dst_id = header.stream_id, header.dst_id
        if rf_src not in permission.allowed_src_ids:
            return self._reject(f"source {rf_src} not in allowed_src_ids")
        if not self._take_token(permission):
            return self._reject(f"over {permission.max_frames_per_s:g} frames/s")
        with self._lock:
            first = stream_id not in self._streams_seen
            if first:
                if len(self._streams_seen) > 1024:
                    self._streams_seen.clear()
                self._streams_seen.add(stream_id)
        if first:
            logger.info(
                "(PLUGIN) %s sent stream %s src %s -> dst %s", self._plugin, int_id(stream_id), rf_src, int_id(dst_id)
            )
        self._call_from_reactor(self._deliver, bytes(pkt), self._plugin)
        return True

    def _take_token(self, permission: SendPermission) -> bool:
        with self._lock:
            now = self._clock()
            rate = permission.max_frames_per_s
            self._tokens = min(rate, self._tokens + (now - self._refilled) * rate)
            self._refilled = now
            if self._tokens < 1.0:
                return False
            self._tokens -= 1.0
            return True

    def _reject(self, reason: str) -> bool:
        with self._lock:
            self.dropped += 1
            dropped = self.dropped
        if dropped == 1 or dropped % 100 == 0:
            logger.warning("(PLUGIN) %s: frame dropped, %s (%d dropped so far)", self._plugin, reason, dropped)
        return False
