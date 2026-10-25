# ADN DMR Peer Server - infrastructure proxy self service bridge
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

"""Self-service hooks on integrated proxy (legacy adn-proxy DB + RPTO parity)."""

from __future__ import annotations

import logging
import struct
from hashlib import pbkdf2_hmac
from typing import Any, Callable

from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks
from twisted.internet.interfaces import IDelayedCall
from twisted.internet.task import LoopingCall

from adn_server.application.ports import (
    ProxyClientSender,
    ProxyMasterSink,
    ProxySelfServiceStore,
)
from adn_server.application.proxy import ProxyUseCases
from adn_server.domain.hbp_protocol import normalize_fixed_width_ascii, normalize_fixed_width_bytes
from adn_server.domain.proxy import ClientEndpoint
from adn_server.domain.value_objects import int_id
from adn_server.infrastructure.hbp_constants import RPTACK, RPTC, RPTCL, RPTO
from adn_server.infrastructure.options_redaction import redact_pass_in_options
from adn_server.infrastructure.twisted_adapters.looping import start_loop

_RPTC_FALLBACK_DELAY = 10.0


def _peer_id_from_db(value: Any) -> bytes | None:
    if isinstance(value, bytes) and len(value) == 4:
        return value
    if isinstance(value, int) and 0 <= value <= 0xFFFFFFFF:
        return struct.pack(">I", value)
    return None


class ProxySelfServiceBridge:
    """Mirror legacy proxy ``Clients`` writes and RPTO push loops on core fan-in."""

    def __init__(
        self,
        store: ProxySelfServiceStore,
        use_cases: ProxyUseCases,
        master_sink: ProxyMasterSink,
        client_sender: ProxyClientSender,
        *,
        pbkdf2_salt: str = "ADN",
        pbkdf2_iterations: int = 2000,
        logger: logging.Logger | None = None,
        dynamic_tg_uc: Any = None,
        purge_peer_dynamic: Callable[[bytes, str], bool] | None = None,
    ) -> None:
        self._store = store
        self._use_cases = use_cases
        self._master_sink = master_sink
        self._client_sender = client_sender
        self._pbkdf2_salt = pbkdf2_salt
        self._pbkdf2_iterations = pbkdf2_iterations
        self._log = logger or logging.getLogger(__name__)
        self._opt_timers: dict[bytes, IDelayedCall] = {}
        self._loop_calls: list[LoopingCall] = []
        self._dynamic_tg_uc = dynamic_tg_uc
        self._purge_peer_dynamic = purge_peer_dynamic
        # Peers that sent PASS= this session — only they get MySQL OPTIONS push.
        self._mysql_option_peers: set[bytes] = set()

    def start_loops(self) -> None:
        """Timers: send_opts 10s, lst_seen+reconcile 120s (now=True for startup clean slate)."""
        for interval, fn, now in (
            (10.0, self.send_opts, False),
            (120.0, self.lst_seen, True),
        ):
            self._loop_calls.append(start_loop(fn, interval, self._log, now=now))
        self._log.info(
            "(SELF_SERVICE) DB options on PASS= (immediate), send_opts every 10s, "
            "lst_seen + reconcile_logged_in every 2min"
        )
        if self._dynamic_tg_uc is not None and self._purge_peer_dynamic is not None:
            self._log.info(
                "(SELF_SERVICE) peer_dynamic_tgs.need_reload polled on send_opts (TG 4000 parity)"
            )

    def stop_loops(self) -> None:
        for call in self._loop_calls:
            if call.running:
                call.stop()
        self._loop_calls.clear()
        for timer in self._opt_timers.values():
            if timer.active():
                timer.cancel()
        self._opt_timers.clear()
        self._mysql_option_peers.clear()

    def before_inject(
        self,
        data: bytes,
        addr: tuple[str, int],
        peer_id: bytes,
    ) -> bool:
        """Handle RPTC/RPTO DB side effects. Return True to skip master inject."""
        if len(data) < 4:
            return False
        command = data[:4]
        host, port = addr
        if command == RPTO:
            self._log.info(
                "(SELF_SERVICE) RPTO from %s:%s peer=%s len=%d payload=%s",
                host, port, int_id(peer_id), len(data),
                redact_pass_in_options(data[8:] if len(data) > 8 else b""),
            )
            return self._handle_rpto(data, peer_id, host, port)
        if command == RPTC and len(data) >= 5 and data[:5] != RPTCL:
            self._handle_rptc(data, peer_id, host)
        return False

    def on_session_expired(self, peer_id: bytes) -> None:
        self._cancel_opt_timer(peer_id)
        self._mysql_option_peers.discard(peer_id)
        self._store.updt_tbl("log_out", peer_id)

    def _handle_rptc(self, data: bytes, peer_id: bytes, host: str) -> None:
        client = self._use_cases.resolve_client(peer_id)
        if client is None:
            self._log.debug(
                "(SELF_SERVICE) RPTC from %s peer=%s but peer NOT in proxy slots",
                host, int_id(peer_id),
            )
            return
        mode = data[97:98].decode("utf-8", errors="replace") if len(data) >= 98 else "4"
        callsign = normalize_fixed_width_ascii(data[8:16])
        self._store.ins_conf(int_id(peer_id), peer_id, callsign, host, mode)
        if peer_id in self._mysql_option_peers:
            self._fetch_options_now(peer_id)
            return
        # Schedule a fallback fetch: if the hotspot does not send an RPTO within the
        # delay window, treat it the same as an empty RPTO (BD is the authority).
        self._schedule_rptc_fallback(peer_id)

    def _handle_rpto(
        self,
        data: bytes,
        peer_id: bytes,
        host: str,
        port: int,
    ) -> bool:
        client = self._use_cases.resolve_client(peer_id)
        if client is None:
            self._log.warning(
                "(SELF_SERVICE) RPTO from %s:%s peer=%s but peer NOT in proxy slots — "
                "cannot process PASS/OPTIONS",
                host, port, int_id(peer_id),
            )
            return False
        payload = normalize_fixed_width_bytes(data[8:]) if len(data) > 8 else b""
        if payload.upper().startswith(b"PASS="):
            psswd_raw = payload[5:]
            if len(psswd_raw) < 6:
                self._log.warning(
                    "(SELF_SERVICE) RPTO PASS= from %s (peer=%s) too short (%d bytes) — "
                    "skipped, packet NOT injected to master",
                    host, int_id(peer_id), len(psswd_raw),
                )
                return True
            dk = pbkdf2_hmac(
                "sha256",
                psswd_raw,
                self._pbkdf2_salt.encode("utf-8"),
                self._pbkdf2_iterations,
            ).hex()
            self._store.updt_tbl("psswd", peer_id, psswd=dk)
            self._client_sender.send_to_client(
                RPTACK + peer_id,
                ClientEndpoint(host=host, port=port),
            )
            self._log.info("(SELF_SERVICE) Password stored for: %s", int_id(peer_id))
            self._mysql_option_peers.add(peer_id)
            self._fetch_options_now(peer_id)
            return True
        if payload:
            # Hotspot sent OPTIONS directly (e.g. TS2=730;SINGLE=0;): it is the authority.
            # Clear any stored password so only IP auto-login works (no password login).
            self._clear_password(peer_id, host, port)
            self._mysql_option_peers.discard(peer_id)
            self._store.updt_tbl("opt_rcvd", peer_id)
            self._cancel_opt_timer(peer_id)
            self._log.info("(SELF_SERVICE) Options received from: %s", int_id(peer_id))
            return False
        # Empty payload: BD is the authority. Same treatment as PASS (password cleared).
        self._clear_password(peer_id, host, port)
        self._mysql_option_peers.add(peer_id)
        self._fetch_options_now(peer_id)
        self._log.info(
            "(SELF_SERVICE) Empty OPTIONS from %s (peer=%s) — BD is authority",
            host, int_id(peer_id),
        )
        return True

    def _clear_password(
        self, peer_id: bytes, host: str, port: int
    ) -> None:
        """Clear stored password (NULL) when the hotspot does not send PASS.
        IP auto-login still works; password login is impossible with a NULL hash."""
        self._store.updt_tbl("clear_psswd", peer_id)
        self._client_sender.send_to_client(
            RPTACK + peer_id,
            ClientEndpoint(host=host, port=port),
        )

    def _cancel_opt_timer(self, peer_id: bytes) -> None:
        timer = self._opt_timers.pop(peer_id, None)
        if timer is not None and timer.active():
            timer.cancel()

    def _schedule_rptc_fallback(self, peer_id: bytes) -> None:
        """If the hotspot does not send RPTO within the delay, fetch OPTIONS from DB.

        Mirrors the legacy proxy 10s ``login_opt`` timer: when no RPTO arrives
        after RPTC, the server treats it as "BD is the authority" and fetches
        OPTIONS from MySQL so the peer gets its configured static TGs.
        """
        self._cancel_opt_timer(peer_id)
        timer = reactor.callLater(
            _RPTC_FALLBACK_DELAY, self._rptc_fallback_fire, peer_id
        )
        self._opt_timers[peer_id] = timer

    def _rptc_fallback_fire(self, peer_id: bytes) -> None:
        """Called by the RPTC fallback timer if no RPTO was received."""
        self._opt_timers.pop(peer_id, None)
        if self._use_cases.resolve_client(peer_id) is None:
            return
        if peer_id in self._mysql_option_peers:
            return
        self._log.info(
            "(SELF_SERVICE) No RPTO from peer=%s after %.0fs — fetching DB options",
            int_id(peer_id), _RPTC_FALLBACK_DELAY,
        )
        self._mysql_option_peers.add(peer_id)
        self._fetch_options_now(peer_id)

    def _fetch_options_now(self, peer_id: bytes) -> None:
        """Load OPTIONS from MySQL and inject RPTO to the server (no legacy 10s delay)."""
        self._cancel_opt_timer(peer_id)
        if peer_id not in self._mysql_option_peers:
            return
        if self._use_cases.resolve_client(peer_id) is None:
            return
        d = self._login_opt(peer_id)
        d.addErrback(
            lambda f: self._log.warning(
                "(SELF_SERVICE) fetch_options_now error: %s", f.getErrorMessage()
            )
        )

    def _inject_rpto(self, peer_id: bytes, options: str | bytes) -> None:
        client = self._use_cases.resolve_client(peer_id)
        if client is None:
            return
        body = options.encode("utf-8") if isinstance(options, str) else options
        packet = RPTO + peer_id + body
        self._master_sink.inject(packet, (client.host, client.port))
        self._log.info(
            "(SELF_SERVICE) Options sent for: %s, opt: %s",
            int_id(peer_id),
            options if isinstance(options, str) else options.decode("utf-8", errors="replace"),
        )

    @inlineCallbacks
    def _login_opt(self, peer_id: bytes) -> None:
        self._opt_timers.pop(peer_id, None)
        if peer_id not in self._mysql_option_peers:
            self._log.debug(
                "(SELF_SERVICE) _login_opt skip: peer=%s not in mysql_option_peers",
                int_id(peer_id),
            )
            return
        if self._use_cases.resolve_client(peer_id) is None:
            self._log.warning(
                "(SELF_SERVICE) _login_opt skip: peer=%s no longer in proxy slots",
                int_id(peer_id),
            )
            return
        try:
            rows = yield self._store.slct_opt(peer_id)
            if not rows or not rows[0]:
                self._log.info(
                    "(SELF_SERVICE) _login_opt: peer=%s — no options in DB; "
                    "server YAML defaults will apply",
                    int_id(peer_id),
                )
                return
            options = rows[0][0]
            if not options:
                self._log.info(
                    "(SELF_SERVICE) _login_opt: peer=%s — options column empty in DB; "
                    "server YAML defaults will apply",
                    int_id(peer_id),
                )
                return
            self._inject_rpto(peer_id, options)
            self._log.info("(SELF_SERVICE) Options sent at login for: %s", int_id(peer_id))
        except Exception as err:
            self._log.warning("(SELF_SERVICE) login_opt error: %s", err)

    @inlineCallbacks
    def send_opts(self) -> None:
        try:
            results = yield self._store.slct_db()
            for row in results:
                if len(row) < 2:
                    continue
                pid = _peer_id_from_db(row[0])
                options = row[1]
                if pid is None:
                    continue
                if not options:
                    self._log.debug(
                        "(SELF_SERVICE) send_opts: peer=%s has modified=1 but OPTIONS is EMPTY in DB",
                        int_id(pid),
                    )
                    continue
                if self._use_cases.resolve_client(pid) is None:
                    self._log.debug(
                        "(SELF_SERVICE) send_opts: peer=%s has modified=1 but is NOT connected",
                        int_id(pid),
                    )
                    continue
                if pid not in self._mysql_option_peers:
                    continue
                self._store.updt_tbl("rst_mod", pid)
                self._inject_rpto(pid, options)
                self._log.info("(SELF_SERVICE) Options update sent for: %s", int_id(pid))
            yield self._process_dynamic_reload()
        except Exception as err:
            self._log.warning("(SELF_SERVICE) send_opts error: %s", err)

    @inlineCallbacks
    def _process_dynamic_reload(self) -> None:
        if self._dynamic_tg_uc is None or self._purge_peer_dynamic is None:
            return

        def _try_purge(peer_int: int, system_name: str, peer_id: bytes) -> bool:
            if self._use_cases.resolve_client(peer_id) is None:
                return False
            return bool(self._purge_peer_dynamic(peer_id, system_name))

        yield self._dynamic_tg_uc.process_reload_queue(try_purge=_try_purge)

    def lst_seen(self) -> None:
        slots = self._use_cases.list_slots()
        dmrid_list = [(slot.peer_id,) for slot in slots]
        if dmrid_list:
            self._store.updt_lstseen(dmrid_list)
        self._store.reconcile_logged_in([slot.peer_id for slot in slots])
