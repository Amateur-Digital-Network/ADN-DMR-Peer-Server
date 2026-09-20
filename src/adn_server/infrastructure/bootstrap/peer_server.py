# ADN DMR Peer Server - peer runtime bootstrap
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

"""Wire use cases, listeners, and reactor loops (extracted from main)."""

from __future__ import annotations

import logging
import os
import signal
import time
from typing import Any

from twisted.internet import reactor, task, threads

from adn_server.application import (
    IdentUseCases,
    ReportingUseCases,
    ReportSender,
    RoutingUseCases,
    VoiceUseCases,
)
from adn_server.application.dynamic_tg_use_cases import DynamicTgUseCases
from adn_server.application.plugins.application.bus import PluginBus
from adn_server.application.plugins.application.context import ServerContext
from adn_server.application.plugins.application.data_bridge import DataPluginBridge
from adn_server.application.plugins.application.manager import PluginManager
from adn_server.application.plugins.application.voice_bridge import VoicePluginBridge
from adn_server.application.proxy.deployment import (
    is_obp_proxy_managed,
    is_proxy_inject_only,
    normalize_obp_proxy_targets,
    normalize_proxy_target,
    obp_proxy_enabled,
    proxy_target_system,
)
from adn_server.application.report.queue import BoundedReportQueue, QueuedReportSender
from adn_server.application.runtime_context import (
    ConfigProxy,
    RuntimeContext,
    RuntimeContextHolder,
    prepare_reload_config,
    swap_runtime_config,
)
from adn_server.application.subscription.echo_seed import seed_echo_routing_table
from adn_server.application.subscription.store_sync import replace_store_from_routing_table
from adn_server.domain import bytes_4
from adn_server.domain.dmr.bptc import encode_emblc
from adn_server.domain.mesh_session import mesh_sessions
from adn_server.infrastructure.acl_router import InMemoryAclRouter
from adn_server.infrastructure.config_normalizer import (
    ensure_system_runtime_config as _ensure_system_runtime_config,
)
from adn_server.infrastructure.config_normalizer import (
    expand_generator as _expand_generator,
)
from adn_server.infrastructure.config_normalizer import (
    normalize_obp_config as _normalize_obp_config,
)
from adn_server.infrastructure.config_normalizer import (
    normalize_peer_config as _normalize_peer_config,
)
from adn_server.infrastructure.config_reload import BindSpec, reload_server_config
from adn_server.infrastructure.logging_config import reopen_file_handlers
from adn_server.infrastructure.persistence import PickleSubMapStore
from adn_server.infrastructure.persistence.alias_loader import DefaultAliasLoader
from adn_server.infrastructure.persistence.database_config import database_settings
from adn_server.infrastructure.persistence.dynamic_tg_repository import MysqlDynamicTgRepository
from adn_server.infrastructure.persistence.keys_store import JsonKeysStore
from adn_server.infrastructure.persistence.mysql_pool import create_mysql_pool, ensure_database_sync
from adn_server.infrastructure.proxy import apply_proxy_config_reload, start_proxy_service
from adn_server.infrastructure.proxy.obp_runtime import (
    apply_obp_proxy_config_reload,
    start_obp_proxy_service,
)
from adn_server.infrastructure.security.password_download import (
    DefaultSecurityDownloader,
    StubSecurityDownloader,
)
from adn_server.infrastructure.security.user_passwords_loader import UserPasswordsLoader
from adn_server.infrastructure.subscription_store import InMemorySubscriptionStore
from adn_server.infrastructure.talker_alias_emblc import default_ta_emblc_encoder
from adn_server.infrastructure.twisted_adapters.report.mqtt_config import mqtt_settings_from_config
from adn_server.infrastructure.twisted_adapters.report.mqtt_publisher import (
    create_report_mqtt_publisher,
    reconcile_mqtt_publisher,
)
from adn_server.infrastructure.twisted_adapters.report.worker import start_report_queue_worker
from adn_server.infrastructure.twisted_adapters.report_server import ReportServerFactory
from adn_server.infrastructure.twisted_adapters.udp_hbp import HBPProtocolFactory
from adn_server.infrastructure.udp_rcvbuf import apply_udp_rcvbuf, udp_rcvbuf_bytes
from adn_server.infrastructure.voice import DefaultVoiceProvider, StubVoiceProvider
from adn_server.infrastructure.voice.recording import RecordingHandler


class ReportSenderAdapter(ReportSender):
    """Adapt ReportServerFactory to ReportSender port."""

    def __init__(self, factory: ReportServerFactory) -> None:
        self._factory = factory

    def set_systems(self, systems) -> None:
        self._factory.set_systems(systems)

    def set_routing_table(self, bridges) -> None:
        self._factory.set_routing_table(bridges)

    def send_config(self, systems, *, incremental: bool = False) -> None:
        self._factory.set_systems(systems)
        self._factory.send_config(incremental=incremental)

    def send_routing_table(self, bridges, *, incremental: bool = False) -> None:
        self._factory.set_routing_table(bridges)
        self._factory.send_routing_table(incremental=incremental)

    def send_routing_event(self, event: str) -> None:
        self._factory.send_routing_event(event)

    def set_peer_slot_map(self, provider) -> None:
        self._factory.set_peer_slot_map(provider)


def _wire_proxy_report_slots(
    report_factory: ReportServerFactory,
    proxy_state: Any,
) -> None:
    """Bind proxy upstream slot indices into monitor topology expansion."""
    if proxy_state is None:
        report_factory.set_peer_slot_map(None)
        return

    def _slot_map() -> dict[bytes, int]:
        return {
            slot.peer_id: slot.report_slot
            for slot in proxy_state.use_cases.list_slots()
            if slot.report_slot is not None
        }

    report_factory.set_peer_slot_map(_slot_map)


def _wire_monitor_downlink_ctx(
    report_factory: ReportServerFactory,
    protocols: dict[str, Any],
) -> None:
    """Bind live MASTER downlink state into monitor voice-event fan-out."""

    def _ctx_for(system_name: str) -> Any:
        protocol = protocols.get(system_name)
        if protocol is None:
            return None
        provider = getattr(protocol, "downlink_context_for_monitor", None)
        if callable(provider):
            return provider()
        legacy = getattr(protocol, "_downlink_ctx", None)
        if callable(legacy):
            return legacy()
        return None

    report_factory.set_downlink_ctx_for_system(_ctx_for)


_DEFAULT_ALIAS_POLL_INTERVAL_SEC = 900.0


def _resolve_alias_poll_interval(aliases_cfg: dict[str, Any], logger: logging.Logger) -> float:
    """ALIASES.POLL_INTERVAL_SEC, defaulting to 900s. Never raises — a bad value (missing,
    non-numeric, zero/negative) falls back to the default with a warning instead of
    aborting server startup."""
    raw = aliases_cfg.get("POLL_INTERVAL_SEC")
    if raw is None or raw == "":
        return _DEFAULT_ALIAS_POLL_INTERVAL_SEC
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "(ALIAS) invalid ALIASES.POLL_INTERVAL_SEC=%r, using default %gs",
            raw,
            _DEFAULT_ALIAS_POLL_INTERVAL_SEC,
        )
        return _DEFAULT_ALIAS_POLL_INTERVAL_SEC
    if value <= 0:
        logger.warning(
            "(ALIAS) ALIASES.POLL_INTERVAL_SEC=%s must be positive, using default %gs",
            raw,
            _DEFAULT_ALIAS_POLL_INTERVAL_SEC,
        )
        return _DEFAULT_ALIAS_POLL_INTERVAL_SEC
    return value


def _log_alias_health(config: Any, systems_cfg: dict[str, Any], logger: logging.Logger) -> None:
    """Warn loudly when peer/subscriber/server ID tables are empty and would fail-closed.

    validate_id() and validate_obp_source_server_id() (udp_hbp.py) reject any ID that is
    not found in _PEER_IDS/_SUB_IDS/_LOCAL_SUBSCRIBER_IDS whenever ALLOW_UNREG_ID is False,
    and OBP source-server checks reject on an empty _SERVER_IDS when VALIDATE_SERVER_IDS is
    True. An empty table under those flags means every registration/relay is silently
    rejected — this makes that failure mode visible in the logs instead of only showing up
    as "users can't connect" reports.
    """
    peer_ids = config.get("_PEER_IDS") or {}
    sub_ids = config.get("_SUB_IDS") or {}
    local_sub_ids = config.get("_LOCAL_SUBSCRIBER_IDS") or {}
    server_ids = config.get("_SERVER_IDS") or {}
    logger.info(
        "(ALIAS) dictionary sizes: peer_ids=%d subscriber_ids=%d local_subscriber_ids=%d "
        "talkgroup_ids=%d server_ids=%d",
        len(peer_ids),
        len(sub_ids),
        len(local_sub_ids),
        len(config.get("_TG_IDS") or {}),
        len(server_ids),
    )
    enforces_reg = any(
        sys_cfg.get("ENABLED", True) and not sys_cfg.get("ALLOW_UNREG_ID", True)
        for sys_cfg in systems_cfg.values()
    )
    if enforces_reg and not peer_ids and not sub_ids and not local_sub_ids:
        logger.error(
            "(ALIAS) peer_ids/subscriber_ids/local_subscriber_ids are ALL EMPTY while at "
            "least one system has ALLOW_UNREG_ID: false — every peer/hotspot registration "
            "will be rejected until a download succeeds or a .bak file is available"
        )
    if config.get("GLOBAL", {}).get("VALIDATE_SERVER_IDS") and not server_ids:
        logger.error(
            "(ALIAS) server_ids is EMPTY while GLOBAL.VALIDATE_SERVER_IDS is true — all "
            "OpenBridge traffic with a 4-5 digit source server will be rejected"
        )


def _looping_errback(logger: logging.Logger, failure):
    """Errback for LoopingCalls (legacy loopingErrHandle). Stops reactor to avoid memory leaks."""
    try:
        tb = failure.getTraceback() if hasattr(failure, "getTraceback") else str(failure)
    except Exception:
        tb = repr(failure)
    logger.error(
        "(GLOBAL) STOPPING REACTOR TO AVOID MEMORY LEAK: Unhandled error in timed loop.\n%s",
        tb,
    )
    from twisted.internet import reactor as _reactor

    _reactor.stop()


def run_peer_server(
    config: dict,
    config_path: str,
    project_root: str,
    *,
    no_proxy: bool,
    logger: logging.Logger,
    voice_config_path: str,
    loader: Any,
) -> None:
    # Aliases
    alias_loader = DefaultAliasLoader()
    peer_ids, subscriber_ids, talkgroup_ids, local_subscriber_ids, server_ids, checksums = (
        alias_loader.load_aliases(config)
    )
    subscriber_ids[900999] = "D-APRS"
    subscriber_ids[4294967295] = "SC"
    config["_SUB_IDS"] = subscriber_ids
    config["_SUB_PROFILES"] = alias_loader.load_subscriber_profiles(config)
    config["_PEER_IDS"] = peer_ids
    config["_TG_IDS"] = talkgroup_ids
    config["_LOCAL_SUBSCRIBER_IDS"] = local_subscriber_ids
    config["_SERVER_IDS"] = server_ids
    config["CHECKSUMS"] = checksums
    _log_alias_health(config, config.get("SYSTEMS", {}), logger)

    # SUB_MAP (shared mutable; used by SubMapTrimmer and shutdown)
    aliases_cfg = config.get("ALIASES", {})
    data_path = (aliases_cfg.get("PATH") or ".").rstrip("/")
    sub_map_file = aliases_cfg.get("SUB_MAP_FILE") or "sub_map.pkl"
    sub_map_path = os.path.join(project_root, data_path, sub_map_file)
    sub_map_store = PickleSubMapStore()
    sub_map = sub_map_store.load(sub_map_path)
    config["_SUB_MAP"] = sub_map

    # Generator: expand MASTER systems with GENERATOR > 1 into SYSTEM-0, SYSTEM-1, ... (legacy)
    _expand_generator(config, logger)
    normalize_proxy_target(config)
    _ensure_system_runtime_config(config)
    _normalize_peer_config(config)
    _normalize_obp_config(config)
    normalize_obp_proxy_targets(config)

    runtime_holder = RuntimeContextHolder(RuntimeContext(config=config, config_path=config_path))
    config = ConfigProxy(runtime_holder)

    db_settings = database_settings(config)
    if not ensure_database_sync(
        db_settings["db_server"],
        db_settings["db_username"],
        db_settings["db_password"],
        db_settings["db_name"],
        db_settings["db_port"],
    ):
        logger.critical("(GLOBAL) Startup aborted — MariaDB required for dynamic TG persistence")
        raise SystemExit(1)
    mysql_pool = create_mysql_pool(
        db_settings["db_server"],
        db_settings["db_username"],
        db_settings["db_password"],
        db_settings["db_name"],
        db_settings["db_port"],
    )
    dynamic_tg_store = MysqlDynamicTgRepository(mysql_pool)

    # Subscription store is runtime routing authority; acl_router is ACL-only (D-08).
    acl_router = InMemoryAclRouter()
    systems_cfg = config.get("SYSTEMS", {})
    subscription_store = InMemorySubscriptionStore()
    if "ECHO" in systems_cfg and systems_cfg["ECHO"].get("MODE") in ("PEER", "MASTER"):
        replace_store_from_routing_table(subscription_store, seed_echo_routing_table(config))
    _ctx = runtime_holder.get()
    runtime_holder.swap(
        RuntimeContext(
            config=_ctx.config,
            config_path=_ctx.config_path,
            subscription_store=subscription_store,
        )
    )

    # Protocol registry for send_to_system (legacy: systems[name].send_system(packet))
    protocols: dict[str, Any] = {}
    udp_ports: dict[str, Any] = {}
    report_mqtt = create_report_mqtt_publisher(config)
    report_factory = ReportServerFactory(config, mqtt=report_mqtt)

    def send_to_system(system_name: str, packet: bytes, **kwargs: Any) -> None:
        p = protocols.get(system_name)
        if p is not None and hasattr(p, "send_system"):
            p.send_system(packet, **kwargs)

    def send_dmra_to_system(
        system_name: str,
        packets: list[bytes],
        exclude_peer: bytes | None = None,
        *,
        slot: int | None = None,
        tgid: int | None = None,
    ) -> int:
        p = protocols.get(system_name)
        if p is not None and hasattr(p, "send_dmra_system"):
            return int(
                p.send_dmra_system(
                    packets,
                    exclude_peer=exclude_peer,
                    slot=slot,
                    tgid=tgid,
                )
                or 0
            )
        return 0

    def get_dmra_blocks(system_name: str, stream_id: bytes) -> dict[int, bytes] | None:
        p = protocols.get(system_name)
        if p is not None and hasattr(p, "get_dmra_blocks"):
            return p.get_dmra_blocks(stream_id)
        return None

    def send_bcsq(system_name: str, tgid: bytes, stream_id: bytes) -> None:
        """Legacy: bridge calls send_bcsq (e.g. loop control first OBP). OBP protocol only."""
        p = protocols.get(system_name)
        if p is not None and hasattr(p, "_obp_send_bcsq"):
            p._obp_send_bcsq(tgid, stream_id)

    report_inner = ReportSenderAdapter(report_factory)
    report_queue = BoundedReportQueue()
    report_sender = QueuedReportSender(report_queue, report_inner)
    report_factory.set_systems(systems_cfg)
    report_factory.set_routing_table({})
    reporting_use_cases = ReportingUseCases(report_sender, config)
    if config.get("GLOBAL", {}).get("URL_SECURITY", "").strip():
        security = DefaultSecurityDownloader(project_root)
    else:
        security = StubSecurityDownloader()
    security.init_downloads(config)

    user_passwords_loader = UserPasswordsLoader(project_root)
    user_passwords_loader.load(config)

    recording_handler = RecordingHandler(config, project_root)

    # Voice: AMBE words + pkt_gen (legacy readAMBE + mk_voice). Use Default when Audio dir exists.
    # ANNOUNCEMENT_LANGUAGES is only for voice ident; announcements/TTS use per-item LANGUAGE.
    audio_path = os.path.join(project_root, config.get("VOICE", {}).get("AUDIO_PATH", "Audio"))
    if config.get("VOICE") and not os.path.isdir(audio_path):
        os.makedirs(audio_path, exist_ok=True)
    if os.path.isdir(audio_path):
        voice_provider = DefaultVoiceProvider()
    else:
        voice_provider = StubVoiceProvider()
    def _start_voice_loop(fn, interval: float, now: bool):
        lc = task.LoopingCall(fn)
        d = lc.start(interval, now=now)
        d.addErrback(_looping_errback, logger)
        return lc

    voice_use_cases = VoiceUseCases(
        voice_provider,
        config,
        get_protocols=lambda: protocols,
        call_from_reactor=reactor.callFromThread,
        audio_path=audio_path,
        routing_table_for_report=lambda: {},
        call_later=reactor.callLater,
        start_looping_call=_start_voice_loop,
        defer_to_thread=threads.deferToThread,
    )
    voice_use_cases.apply_voice_config()
    ident_use_cases = IdentUseCases(
        config,
        voice_use_cases,
        audio_path,
        get_protocols=lambda: protocols,
        call_from_reactor=reactor.callFromThread,
    )

    plugin_bus = PluginBus()
    plugin_bus.set_call_later(reactor.callLater)
    server_ctx = ServerContext(
        config=config,
        project_root=project_root,
        defer_to_thread=threads.deferToThread,
        call_from_reactor=reactor.callFromThread,
        call_later=reactor.callLater,
    )
    plugin_manager = PluginManager(plugin_bus, server_ctx, project_root)
    voice_plugin_bridge = VoicePluginBridge(plugin_bus, config, get_dmra_blocks=get_dmra_blocks)
    data_plugin_bridge = DataPluginBridge(plugin_bus, config)

    routing_use_cases = RoutingUseCases(
        acl_router,
        config,
        subscription_store,
        send_to_system=send_to_system,
        get_protocols=lambda: protocols,
        reporting=reporting_use_cases,
        on_relay_deactivated=lambda sys: reactor.callInThread(voice_use_cases.disconnected_voice, sys),
        send_bcsq=send_bcsq,
        send_dmra_to_system=send_dmra_to_system,
        get_dmra_blocks=get_dmra_blocks,
        call_later=reactor.callLater,
        encode_emblc=encode_emblc,
        ta_emblc_encoder=default_ta_emblc_encoder,
        voice_plugin_bridge=voice_plugin_bridge,
        data_plugin_bridge=data_plugin_bridge,
    )
    plugin_manager.discover_and_load(config)
    reactor.addSystemEventTrigger("before", "shutdown", plugin_manager.shutdown_all)
    routing_use_cases.apply_startup_subscriptions()
    dynamic_tg_uc = DynamicTgUseCases(
        dynamic_tg_store,
        on_restored=routing_use_cases.sync_restored_dynamic_tgs,
    )
    routing_table_for_report = routing_use_cases.routing_table_for_report
    voice_use_cases._routing_table_for_report = routing_table_for_report
    from adn_server.application.routing.announcement_ptt_inject import (
        announcement_ptt_system,
        inject_announcement_ptt,
    )

    _ptt_system = announcement_ptt_system(config)

    def _inject_announcement_ptt(pkt: bytes, pkt_time: float) -> bool | None:
        if not _ptt_system:
            return False
        server_id = config.get("GLOBAL", {}).get("SERVER_ID", b"\x00\x00\x00\x00")
        if not isinstance(server_id, bytes):
            server_id = bytes_4(int(server_id or 0) & 0xFFFFFFFF)
        accepted = inject_announcement_ptt(
            routing_use_cases,
            _ptt_system,
            pkt,
            pkt_time=pkt_time,
            server_id=server_id,
        )
        proto = protocols.get(_ptt_system)
        send_system = getattr(proto, "send_system", None) if proto is not None else None
        if callable(send_system):
            send_system(pkt)
        return accepted

    voice_use_cases._inject_announcement_ptt = _inject_announcement_ptt
    voice_use_cases._announcement_ptt_system = _ptt_system
    voice_use_cases._send_routing_event = reporting_use_cases.send_routing_event
    report_factory.set_routing_table(routing_table_for_report())
    report_factory.set_systems(config.get("SYSTEMS", {}))

    # Report server (same order as legacy: log then listen)
    if config.get("REPORTS", {}).get("REPORT", True):
        logger.info("(REPORT) HBlink TCP reporting server configured")
        port = config["REPORTS"].get("REPORT_PORT", 4321)
        reactor.listenTCP(port, report_factory)
        logger.info("(REPORT) Report server listening on TCP %s", port)
        start_report_queue_worker(
            report_queue,
            report_inner,
            on_errback=lambda e: _looping_errback(e, logger),
        )
        if report_mqtt is not None:
            report_factory.start_mqtt()
            reactor.addSystemEventTrigger("during", "shutdown", report_mqtt.stop)

    # Reporting loop (REPORT_INTERVAL) — same logs as legacy after send_config/send_routing_table
    def reporting_loop():
        logger.debug("(REPORT) Periodic reporting loop started")
        reporting_use_cases.send_config(config.get("SYSTEMS", {}))
        reporting_use_cases.send_routing_table(routing_table_for_report())
        # Legacy: peer count and SUB_MAP count
        systems_with_peers = sum(1 for s in config.get("SYSTEMS", {}) if config.get("SYSTEMS", {}).get(s, {}).get("PEERS"))
        logger.info("(REPORT) %s systems have at least one peer", systems_with_peers)
        logger.info("(REPORT) Subscriber Map has %s entries", len(config.get("_SUB_MAP", {})))

    report_interval = config.get("REPORTS", {}).get("REPORT_INTERVAL", 60)
    task.LoopingCall(reporting_loop).start(report_interval).addErrback(_looping_errback, logger)

    # LoopingCalls (legacy intervals). rule_timer / stat_trimmer mutate SubscriptionStore on reactor
    # Timer loops run on the reactor thread (no deferToThread — avoids races with dmrd_received).
    def _rule_timer_on_reactor() -> None:
        routing_use_cases.rule_timer_loop()
        reporting_use_cases.send_routing_table(routing_table_for_report(), incremental=True)

    task.LoopingCall(_rule_timer_on_reactor).start(52).addErrback(_looping_errback, logger)
    task.LoopingCall(routing_use_cases.stream_trimmer_loop).start(5).addErrback(_looping_errback, logger)
    task.LoopingCall(routing_use_cases.subscription_reset_loop).start(6).addErrback(_looping_errback, logger)
    if config.get("GLOBAL", {}).get("GEN_STAT_BRIDGES", False):

        def _stat_trimmer_on_reactor() -> None:
            routing_use_cases.stat_trimmer_loop()
            reporting_use_cases.send_routing_table(routing_table_for_report(), incremental=True)

        task.LoopingCall(_stat_trimmer_on_reactor).start(303).addErrback(_looping_errback, logger)

    # KA Reporting (legacy kaReporting, 60s)
    task.LoopingCall(lambda: threads.deferToThread(reporting_use_cases.ka_reporting_loop)).start(60).addErrback(_looping_errback, logger)

    # bridgeDebug (legacy 66s) — remove invalid bridges, fix >1 active dial per MASTER
    if config.get("GLOBAL", {}).get("DEBUG_BRIDGES"):
        task.LoopingCall(
            lambda: (
                routing_use_cases.subscription_debug_loop(),
                reporting_use_cases.send_routing_table(routing_table_for_report(), incremental=True),
            )
        ).start(66).addErrback(_looping_errback, logger)

    # Alias reload: poll every ALIAS_POLL_INTERVAL_SEC (default 900s / 15 min), independent
    # from STALE_DAYS. STALE_DAYS only controls how old a cached file must be before
    # try_download re-fetches it (see alias_loader.try_download); polling on a short, fixed
    # cadence means a failed download (e.g. first boot with no cached files yet) is retried
    # within minutes instead of waiting up to a full STALE_DAYS cycle. Once files are fresh,
    # each tick is a cheap mtime check with no network call.
    alias_poll_interval = _resolve_alias_poll_interval(aliases_cfg, logger)
    alias_reload_state: dict[str, Any] = {"generation": 0, "pending": False}

    def alias_reload_loop():
        alias_reload_state["generation"] += 1
        my_generation = alias_reload_state["generation"]
        if alias_reload_state["pending"]:
            logger.warning(
                "(ALIAS) previous alias download still running after %gs, starting a new "
                "attempt (gen %s); its result will be discarded if it arrives late",
                alias_poll_interval,
                my_generation,
            )
        alias_reload_state["pending"] = True
        logger.debug("(ALIAS) starting alias thread (gen %s)", my_generation)
        # Downloads run in the thread pool (blocking HTTP would stall hotspot pings);
        # the config swap is applied back on the reactor thread.
        d = threads.deferToThread(alias_loader.load_aliases, config)

        def _apply(loaded):
            if my_generation != alias_reload_state["generation"]:
                logger.info(
                    "(ALIAS) discarding alias download result from a superseded attempt "
                    "(gen %s, current gen %s)",
                    my_generation,
                    alias_reload_state["generation"],
                )
                return
            alias_reload_state["pending"] = False
            DefaultAliasLoader.merge_reload_into_config(config, alias_loader, *loaded)
            _log_alias_health(config, config.get("SYSTEMS", {}), logger)

        def _fail(failure):
            if my_generation == alias_reload_state["generation"]:
                alias_reload_state["pending"] = False
            logger.warning(
                "(ALIAS) alias reload failed (gen %s): %s", my_generation, failure.getErrorMessage()
            )

        d.addCallback(_apply)
        d.addErrback(_fail)

    task.LoopingCall(alias_reload_loop).start(alias_poll_interval).addErrback(_looping_errback, logger)

    # SubMapTrimmer (3600s) + save
    def sub_map_trimmer_loop():
        logger.debug("(SUBSCRIBER) Subscriber Map trimmer loop started")
        now = time.time()
        to_remove = [k for k, v in sub_map.items() if v[2] < (now - 86400)]
        for k in to_remove:
            sub_map.pop(k, None)
        if aliases_cfg.get("SUB_MAP_FILE"):
            try:
                sub_map_store.save(sub_map_path, sub_map)
                logger.info("(SUBSCRIBER) Writing SUB_MAP to disk")
            except Exception as e:
                logger.warning("(SUBSCRIBER) Cannot write SUB_MAP to file: %s", e)

    task.LoopingCall(sub_map_trimmer_loop).start(3600).addErrback(_looping_errback, logger)

    # Kill switch + shutdown (legacy kill_server every 5s; SIGTERM/SIGINT trigger _KILL_SERVER)
    config.setdefault("GLOBAL", {})["_KILL_SERVER"] = False
    keys_store = JsonKeysStore()
    keys_path = os.path.join(project_root, data_path, aliases_cfg.get("KEYS_FILE") or "keys.json")
    keys = keys_store.load(keys_path) if keys_path else {}

    def kill_server_loop():
        try:
            if config.get("GLOBAL", {}).get("_KILL_SERVER"):
                logger.info("(GLOBAL) SHUTDOWN: CONFBRIDGE IS TERMINATING - killserver called")
                if reactor.running:
                    reactor.stop()
                if aliases_cfg.get("SUB_MAP_FILE"):
                    sub_map_store.save(sub_map_path, sub_map)
                try:
                    keys_store.save(keys_path, keys)
                    logger.info("(KEYS) saved system keys to keystore")
                except Exception as e:
                    logger.error("(GLOBAL) Cannot save key file: %s", e)
        except KeyError:
            pass

    task.LoopingCall(kill_server_loop).start(5).addErrback(_looping_errback, logger)

    def shutdown_handler():
        """On reactor shutdown: save SUB_MAP and keys."""
        if aliases_cfg.get("SUB_MAP_FILE"):
            try:
                sub_map_store.save(sub_map_path, config["_SUB_MAP"])
                logger.info("(SUBSCRIBER) Writing SUB_MAP to disk (shutdown)")
            except Exception as e:
                logger.warning("(SUBSCRIBER) Cannot write SUB_MAP on shutdown: %s", e)
        try:
            keys_store.save(keys_path, keys)
            logger.info("(KEYS) saved system keys to keystore (shutdown)")
        except Exception as e:
            logger.error("(GLOBAL) Cannot save key file on shutdown: %s", e)

    def sig_handler(sig, frame):
        logger.info("(GLOBAL) SHUTDOWN: CONFBRIDGE IS TERMINATING WITH SIGNAL %s", sig)
        for _sys_name in protocols:
            logger.info("(GLOBAL) SHUTDOWN: DE-REGISTER SYSTEM: %s", _sys_name)
            try:
                protocols[_sys_name].dereg()
            except Exception:
                pass
        config["GLOBAL"]["_KILL_SERVER"] = True
        if reactor.running:
            reactor.stop()

    def sigusr2_reopen_logs(_sig, _frame):
        """Logrotate: reopen file log handlers (does not reload config)."""
        n = reopen_file_handlers()
        logger.info("(LOGGER) Reopened %s file log handler(s) after SIGUSR2", n)

    def _create_hbp_protocol(system_name: str) -> Any:
        return HBPProtocolFactory(
            system_name,
            config,
            report_sender,
            router=acl_router,
            dmrd_received=routing_use_cases.dmrd_received,
            get_user_password_callback=user_passwords_loader.get_user_password,
            on_play_file_request=voice_use_cases.play_file_on_request,
            on_handle_recording=recording_handler.handle_recording,
            on_in_band_signalling=routing_use_cases.apply_in_band_signalling,
            on_options_received=routing_use_cases.options_config_for_system,
            on_deactivate_dynamic_relays=routing_use_cases.deactivate_all_dynamic_relays,
            on_obp_bcsq_received=routing_use_cases.on_obp_bcsq_received,
            on_talker_alias_repeat_prepare=routing_use_cases.prepare_talker_alias_local_repeat,
            on_talker_alias_repeat_burst=routing_use_cases.rewrite_repeat_voice_burst,
            on_talker_alias_stream_end=routing_use_cases.clear_talker_alias_stream,
            on_dmra_fragment_stored=routing_use_cases.on_dmra_fragment_stored,
            routing_table_for_report=routing_table_for_report,
            get_subscription_store=lambda: subscription_store,
            dynamic_tg_uc=dynamic_tg_uc,
        )

    def _listen_system(_name: str, bind: BindSpec, protocol: Any) -> Any:
        port = reactor.listenUDP(bind.port, protocol, interface=bind.ip or "0.0.0.0")
        apply_udp_rcvbuf(port.socket, udp_rcvbuf_bytes(config), label=_name, logger=logger)
        logger.info("(GLOBAL) UDP %s listening on %s:%s", _name, bind.ip or "*", bind.port)
        return port

    def _stop_udp_port(port: Any) -> Any:
        if port is not None:
            return port.stopListening()
        return None

    # UDP / proxy listeners (proxy_state declared before reload handler uses nonlocal)
    proxy_state = None
    obp_proxy_state = None
    proxy_enabled = not no_proxy and proxy_target_system(config) is not None
    obp_proxy_on = obp_proxy_enabled(config)

    def _should_bind_udp(system_name: str, sys_cfg: dict[str, Any]) -> bool:
        if is_proxy_inject_only(config, system_name):
            return False
        if is_obp_proxy_managed(config, system_name):
            return False
        return True

    def _on_config_systems_changed() -> None:
        routing_use_cases.apply_startup_subscriptions()
        reporting_use_cases.send_config(config.get("SYSTEMS", {}))
        reporting_use_cases.send_routing_table(routing_table_for_report())
        user_passwords_loader.load(config)

    def _apply_reload_success(result: Any, *, new_config: dict[str, Any], mqtt_before: Any) -> None:
        nonlocal report_mqtt, proxy_state, obp_proxy_state
        swap_runtime_config(runtime_holder, new_config, config_path=config_path)
        normalize_proxy_target(config)
        normalize_obp_proxy_targets(config)
        # Follow the new YAML: refresh configured peers, drop sessions of dead links.
        mesh_sessions(config).sync(config)
        report_factory.set_config(config)
        mqtt_after = mqtt_settings_from_config(config)
        report_mqtt = reconcile_mqtt_publisher(
            report_factory,
            report_mqtt,
            mqtt_before,
            mqtt_after,
            report_enabled=config.get("REPORTS", {}).get("REPORT", True),
            sessions=mesh_sessions(config),
        )
        if proxy_state is not None:
            apply_proxy_config_reload(proxy_state, config, logger=logger)
            _wire_proxy_report_slots(report_factory, proxy_state)
        elif proxy_enabled and proxy_target_system(config):
            proxy_state = start_proxy_service(
                config,
                protocols,
                logger=logger,
                mysql_pool=mysql_pool,
                dynamic_tg_uc=dynamic_tg_uc,
                purge_peer_dynamic=_purge_peer_dynamic_tgs,
            )
            _wire_proxy_report_slots(report_factory, proxy_state)
        else:
            _wire_proxy_report_slots(report_factory, None)
        if obp_proxy_state is not None:
            apply_obp_proxy_config_reload(
                obp_proxy_state,
                config,
                protocols,
                logger=logger,
            )
        elif obp_proxy_enabled(config):
            obp_proxy_state = start_obp_proxy_service(config, protocols, logger=logger)
        if result.added or result.removed or result.updated or result.rebound:
            _on_config_systems_changed()
        voice_plugin_bridge.update_config(config)
        data_plugin_bridge.update_config(config)
        plugin_manager.rescan(config)

    def _do_config_reload() -> None:
        nonlocal report_mqtt, proxy_state, obp_proxy_state
        mqtt_before = mqtt_settings_from_config(config)
        new_config = prepare_reload_config(runtime_holder)

        def _on_reload_done(result: Any) -> None:
            _apply_reload_success(result, new_config=new_config, mqtt_before=mqtt_before)

        def _on_reload_failed(failure: Any) -> None:
            logger.error("(CONFIG-RELOAD) aborted: %s", failure.value)

        reload_server_config(
            new_config,
            config_path,
            loader,
            protocols,
            udp_ports,
            create_protocol=_create_hbp_protocol,
            listen_udp=lambda n, b, p: _listen_system(n, b, p),
            stop_listener=_stop_udp_port,
            on_systems_changed=None,
            on_system_removed=routing_use_cases.flush_monitor_events_for_system,
            should_bind_udp=_should_bind_udp,
            log=logger,
        ).addCallbacks(_on_reload_done, _on_reload_failed)

    def sighup_reload_config(_sig, _frame):
        """Reload adn-server.yaml (SYSTEMS, GLOBAL); keeps active streams on unchanged listeners."""
        logger.info("(CONFIG-RELOAD) SIGHUP received, scheduling reload")
        if aliases_cfg.get("SUB_MAP_FILE"):
            try:
                sub_map_store.save(sub_map_path, sub_map)
                logger.info("(SUBSCRIBER) Writing SUB_MAP to disk (SIGHUP)")
            except Exception as e:
                logger.warning("(SUBSCRIBER) Cannot write SUB_MAP to file: %s", e)
        reactor.callLater(0, _do_config_reload)

    signal.signal(signal.SIGTERM, sig_handler)
    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGUSR2, sigusr2_reopen_logs)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, sighup_reload_config)
        logger.info("(CONFIG-RELOAD) SIGHUP handler active (systemctl reload / kill -HUP)")
    reactor.addSystemEventTrigger("before", "shutdown", shutdown_handler)

    # Voice config reload (15s): re-read adn-voice.yaml, start/stop announcement LoopingCalls on change
    voice_config_mtime = 0.0
    if voice_config_path and os.path.isfile(voice_config_path):
        try:
            voice_config_mtime = os.path.getmtime(voice_config_path)
        except OSError:
            voice_config_mtime = 0.0

    def voice_reload_loop():
        nonlocal voice_config_mtime
        try:
            if voice_config_path and os.path.isfile(voice_config_path):
                try:
                    mtime = os.path.getmtime(voice_config_path)
                except OSError:
                    return
                if mtime == voice_config_mtime:
                    return
                voice_config_mtime = mtime
                logger.info("(VOICE-RELOAD) config file change detected, reloading configuration...")
            loader.reload_voice_config(config, voice_config_path)
            voice_use_cases.apply_voice_config()
            if voice_config_path and voice_config_mtime:
                logger.info("(VOICE-RELOAD) config reload completed")
        except Exception as e:
            logger.debug("(VOICE-RELOAD) %s", e)

    task.LoopingCall(voice_reload_loop).start(15).addErrback(_looping_errback, logger)
    logger.info("(VOICE-RELOAD) config file watch active (every 15 seconds)")

    # The download is blocking HTTP: run it in the thread pool. On the reactor thread a
    # slow/dead security server stalls RPTPING handling past PING_TIME * MAX_MISSED and
    # every hotspot gets timed out and reconnects.
    security_download_busy = {"value": False}

    def _security_download_done(updated):
        security_download_busy["value"] = False
        if updated:
            user_passwords_loader.load(config)

    def _security_download_error(failure):
        security_download_busy["value"] = False
        logger.error("(SECURITY) Periodic download failed: %s", failure.getErrorMessage())

    def security_loop():
        if security_download_busy["value"]:
            logger.warning("(SECURITY) Previous download still running, skipping this cycle")
            return
        security_download_busy["value"] = True
        threads.deferToThread(security.periodic_download, config).addCallbacks(
            _security_download_done, _security_download_error
        )

    task.LoopingCall(security_loop).start(300).addErrback(_looping_errback, logger)
    logger.info("(SECURITY) Periodic password download task started (every 5 minutes)")

    # Ident (3600s): run ident in thread for MASTERs with VOICE_IDENT
    def ident_loop():
        logger.debug("(IDENT) starting ident thread")
        reactor.callInThread(ident_use_cases.run_ident)

    task.LoopingCall(ident_loop).start(3600).addErrback(_looping_errback, logger)
    task.LoopingCall(routing_use_cases.log_connected_systems_and_tgs).start(60).addErrback(_looping_errback, logger)
    task.LoopingCall(lambda: logger.debug("(ROUTER) KeepAlive reporting loop started")).start(60).addErrback(_looping_errback, logger)

    def dynamic_tg_purge_loop() -> None:
        dynamic_tg_uc.purge_expired(config)

    task.LoopingCall(dynamic_tg_purge_loop).start(60).addErrback(_looping_errback, logger)
    logger.info("(DYNAMIC_TG) Expired SINGLE=1 purge loop started (every 60 seconds)")

    # UDP listeners per system (same order as legacy: SYSTEM STARTING then instance created per system)
    logger.info("(GLOBAL) ADN DMR Peer Server -- SYSTEM STARTING...")
    for system_name, sys_cfg in systems_cfg.items():
        if not sys_cfg.get("ENABLED", True):
            continue
        protocol = _create_hbp_protocol(system_name)
        protocols[system_name] = protocol
        if not _should_bind_udp(system_name, sys_cfg):
            if is_obp_proxy_managed(config, system_name):
                logger.info("(OBP_PROXY) %s inject-only (no UDP bind)", system_name)
            else:
                logger.info("(PROXY) %s inject-only (no UDP bind)", system_name)
            continue
        bind = BindSpec(ip=str(sys_cfg.get("IP") or "0.0.0.0"), port=int(sys_cfg.get("PORT", 56400)))
        udp_ports[system_name] = _listen_system(system_name, bind, protocol)
        logger.debug(
            "(GLOBAL) %s instance created: %s, %s",
            sys_cfg.get("MODE", "?"),
            system_name,
            protocol,
        )

    _wire_monitor_downlink_ctx(report_factory, protocols)

    def _purge_peer_dynamic_tgs(peer_id: bytes, system_name: str) -> bool:
        proto = protocols.get(system_name)
        if proto is None:
            return False
        purge = getattr(proto, "purge_peer_dynamic_tgs", None)
        if not callable(purge):
            return False
        return bool(purge(peer_id))

    def _try_purge_dynamic_reload(peer_int: int, system_name: str, peer_id: bytes) -> bool:
        proto = protocols.get(system_name)
        if proto is None:
            return False
        peers = getattr(proto, "_peers", None)
        if not isinstance(peers, dict) or peer_id not in peers:
            return False
        return _purge_peer_dynamic_tgs(peer_id, system_name)

    if proxy_enabled:
        try:
            proxy_state = start_proxy_service(
                config,
                protocols,
                logger=logger,
                mysql_pool=mysql_pool,
                dynamic_tg_uc=dynamic_tg_uc,
                purge_peer_dynamic=_purge_peer_dynamic_tgs,
            )
        except Exception as exc:
            logger.error("(PROXY) failed to start integrated proxy: %s", exc)
            raise

        def _stop_proxy(_: Any = None) -> None:
            if proxy_state is not None:
                proxy_state.stop()

        reactor.addSystemEventTrigger("before", "shutdown", _stop_proxy)
        _wire_proxy_report_slots(report_factory, proxy_state)

    if obp_proxy_on:
        try:
            obp_proxy_state = start_obp_proxy_service(config, protocols, logger=logger)
        except Exception as exc:
            logger.error("(OBP_PROXY) failed to start OBP proxy: %s", exc)
            raise

        def _stop_obp_proxy(_: Any = None) -> None:
            if obp_proxy_state is not None:
                for deferred in obp_proxy_state.stop():
                    if deferred is not None:
                        pass

        reactor.addSystemEventTrigger("before", "shutdown", _stop_obp_proxy)

    if proxy_state is None or proxy_state.self_service is None:
        def dynamic_reload_loop() -> None:
            dynamic_tg_uc.process_reload_queue(try_purge=_try_purge_dynamic_reload)

        task.LoopingCall(dynamic_reload_loop).start(10).addErrback(_looping_errback, logger)
        logger.info("(DYNAMIC_TG) need_reload poll loop started (every 10 seconds)")

    logger.info("(GLOBAL) ADN DMR Peer Server started. Use adn-dmr-server as reference.")
    reactor.suggestThreadPoolSize(100)
    reactor.run()
