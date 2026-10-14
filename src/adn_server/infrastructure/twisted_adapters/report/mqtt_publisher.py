# ADN DMR Peer Server - infrastructure twisted adapters report mqtt publisher
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

"""Optional MQTT publisher for report v2 JSON (same payloads as TCP wire)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from adn_server.application.ports import ReportMqttPublisher, ReportWireEncoder
from adn_server.application.report.dashboard_state import build_dashboard_state
from adn_server.domain.mesh_session import MeshSessionStore

from .mqtt_config import MQTT_PUBLISH_VOICE_EVENT, MqttSettings, mqtt_settings_from_config
from .mqtt_topics import frame_message_type, mqtt_shared_state_topic, topic_for_frame

logger = logging.getLogger(__name__)

_MQTT_WIRE_LABEL = "state,voice_event"
_SHARED_STATE_DEDUP_KEY = "__shared__"

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover - exercised via create_report_mqtt_publisher
    mqtt = None  # type: ignore[assignment]


class NullReportMqttPublisher(ReportMqttPublisher):
    """No-op sink when MQTT is disabled."""

    def start(self, wire: ReportWireEncoder, get_systems: Any, routing_table_for_report: Any) -> None:
        del wire, get_systems, routing_table_for_report

    def publish_frames(self, frames: tuple[bytes, ...]) -> None:
        del frames

    def publish_dashboard(self, systems: dict[str, Any]) -> None:
        del systems

    def stop(self) -> None:
        pass


class PahoReportMqttPublisher(ReportMqttPublisher):
    """Publish retained shared ``state`` and live ``voice_event`` only."""

    def __init__(self, settings: MqttSettings, sessions: MeshSessionStore | None = None) -> None:
        self._settings = settings
        self._sessions = sessions
        self._client: Any = None
        self._connected = False
        self._get_systems: Callable[[], dict[str, Any]] | None = None
        self._last_state_dedup: dict[str, bytes] = {}

    def start(
        self,
        wire: ReportWireEncoder,
        get_systems: Callable[[], dict[str, Any]],
        routing_table_for_report: Callable[[], dict[str, Any]],
    ) -> None:
        del wire, routing_table_for_report
        if mqtt is None:
            logger.error("(REPORT) MQTT enabled but paho-mqtt is not installed; publisher disabled")
            return
        self._get_systems = get_systems

        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=self._settings.client_id,
            protocol=mqtt.MQTTv311,
        )
        _apply_mqtt_auth(client, self._settings)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        broker = self._settings.broker
        if broker.use_tls:
            if self._settings.cafile:
                client.tls_set(ca_certs=self._settings.cafile)
            else:
                client.tls_set()
        self._client = client
        try:
            client.connect(broker.host, broker.port, keepalive=60)
            client.loop_start()
            auth_user = self._settings.username
            logger.info(
                "(REPORT) MQTT publisher started broker=%s client_id=%s prefix=%s qos=%s auth=%s wire=%s",
                broker.display_url,
                self._settings.client_id,
                self._settings.topic_prefix,
                self._settings.qos,
                auth_user or "no",
                _MQTT_WIRE_LABEL,
            )
        except Exception as e:
            logger.warning("(REPORT) MQTT connect failed: %s", e)
            self._client = None

    def publish_dashboard(self, systems: dict[str, Any]) -> None:
        self._emit_shared_dashboard(systems, force=False)

    def _emit_shared_dashboard(self, systems: dict[str, Any], *, force: bool = False) -> None:
        topic = mqtt_shared_state_topic(self._settings.topic_prefix)
        self._publish_state(systems, topic=topic, dedup_key=_SHARED_STATE_DEDUP_KEY, force=force)

    def _publish_state(
        self,
        systems: dict[str, Any],
        *,
        topic: str,
        dedup_key: str,
        force: bool,
    ) -> None:
        if not self._connected or self._client is None:
            return
        payload = build_dashboard_state(
            systems,
            server_id=_server_id_from_settings(self._settings),
            sessions=self._sessions,
        )
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        content_key = json.dumps(
            {"ctable": payload.get("ctable"), "server_id": payload.get("server_id")},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if not force and self._last_state_dedup.get(dedup_key) == content_key:
            logger.debug("(REPORT) MQTT state unchanged, skip publish topic=%s", topic)
            return
        self._last_state_dedup[dedup_key] = content_key
        try:
            info = self._client.publish(
                topic,
                payload=body,
                qos=self._settings.qos,
                retain=True,
            )
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                logger.warning("(REPORT) MQTT state publish rc=%s topic=%s", info.rc, topic)
            else:
                logger.info("(REPORT) MQTT state published topic=%s bytes=%s", topic, len(body))
        except Exception as e:
            logger.warning("(REPORT) MQTT state publish failed topic=%s: %s", topic, e)

    def publish_frames(self, frames: tuple[bytes, ...]) -> None:
        if not self._connected or self._client is None:
            return
        prefix = self._settings.topic_prefix
        qos = self._settings.qos
        for frame in frames:
            if frame_message_type(frame) != MQTT_PUBLISH_VOICE_EVENT:
                continue
            topic = topic_for_frame(frame, prefix)
            if topic is None:
                continue
            payload = frame[1:]
            try:
                info = self._client.publish(topic, payload=payload, qos=qos, retain=False)
                if info.rc != mqtt.MQTT_ERR_SUCCESS:
                    logger.debug("(REPORT) MQTT publish rc=%s topic=%s", info.rc, topic)
            except Exception as e:
                logger.debug("(REPORT) MQTT publish failed topic=%s: %s", topic, e)

    def stop(self) -> None:
        client = self._client
        self._client = None
        self._connected = False
        self._last_state_dedup.clear()
        if client is None:
            return
        try:
            client.loop_stop()
            client.disconnect()
        except Exception as e:
            logger.debug("(REPORT) MQTT disconnect: %s", e)

    def _on_connect(self, client: Any, userdata: Any, connect_flags: Any, reason_code: Any, properties: Any) -> None:
        del client, userdata, connect_flags, properties
        if getattr(reason_code, "value", reason_code) != 0:
            logger.warning("(REPORT) MQTT broker rejected connection: %s", reason_code)
            return
        self._connected = True
        systems = self._get_systems() if self._get_systems else {}
        if systems:
            self._emit_shared_dashboard(systems, force=False)

    def _on_disconnect(
        self,
        client: Any,
        userdata: Any,
        disconnect_flags: Any,
        reason_code: Any,
        properties: Any,
    ) -> None:
        del client, userdata, disconnect_flags, properties
        self._connected = False
        if getattr(reason_code, "value", reason_code) != 0:
            logger.debug("(REPORT) MQTT disconnected: %s", reason_code)


def _server_id_from_settings(settings: MqttSettings) -> str:
    prefix = settings.topic_prefix
    if prefix.startswith("adn/"):
        return prefix[4:]
    return prefix


def _apply_mqtt_auth(client: Any, settings: MqttSettings) -> None:
    if settings.username is None and settings.password is None:
        return
    client.username_pw_set(settings.username or "", settings.password)


def reconcile_mqtt_publisher(
    factory: Any,
    current: ReportMqttPublisher | None,
    before: MqttSettings | None,
    after: MqttSettings | None,
    *,
    report_enabled: bool,
    sessions: MeshSessionStore | None = None,
) -> ReportMqttPublisher | None:
    """Stop/start MQTT client after SIGHUP when REPORTS.MQTT settings change."""
    if before == after and (current is not None) == (after is not None):
        return current
    if current is not None:
        try:
            current.stop()
        except Exception as e:
            logger.debug("(REPORT) MQTT stop on reload: %s", e)
    if after is None:
        factory.set_mqtt(None)
        if before is not None:
            logger.info("(REPORT) MQTT disconnected (disabled in config reload)")
        return None
    publisher = create_report_mqtt_publisher_from_settings(after, sessions=sessions)
    factory.set_mqtt(publisher)
    if publisher is None:
        logger.warning("(REPORT) MQTT enabled in config but publisher could not start")
        return None
    if report_enabled:
        factory.start_mqtt()
    action = "reconnected" if before is not None else "connected"
    logger.info("(REPORT) MQTT %s after config reload broker=%s", action, after.broker.display_url)
    return publisher


def create_report_mqtt_publisher_from_settings(
    settings: MqttSettings, *, sessions: MeshSessionStore | None = None
) -> ReportMqttPublisher | None:
    if mqtt is None:
        logger.error(
            "(REPORT) MQTT enabled but paho-mqtt is missing; "
            "install with: pip install 'adn-server[mqtt]'"
        )
        return None
    return PahoReportMqttPublisher(settings, sessions)


def create_report_mqtt_publisher(config: dict[str, Any]) -> ReportMqttPublisher | None:
    """Return a publisher when MQTT is explicitly enabled; otherwise None."""
    settings = mqtt_settings_from_config(config)
    if settings is None:
        return None
    return create_report_mqtt_publisher_from_settings(settings)
