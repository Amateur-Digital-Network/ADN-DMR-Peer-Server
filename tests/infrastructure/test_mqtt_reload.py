# ADN DMR Peer Server - tests infrastructure mqtt reload
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

"""MQTT reconnect on config reload."""

from __future__ import annotations

from typing import Any

from adn_server.infrastructure.twisted_adapters.report.mqtt_config import (
    MqttBroker,
    MqttSettings,
    mqtt_settings_from_config,
)
from adn_server.infrastructure.twisted_adapters.report.mqtt_publisher import reconcile_mqtt_publisher


class _RecordingMqtt:
    def __init__(self) -> None:
        self.stopped = False
        self.started = False

    def start(self, wire: Any, get_systems: Any, routing_table_for_report: Any) -> None:
        del wire, get_systems, routing_table_for_report
        self.started = True

    def publish_frames(self, frames: tuple[bytes, ...]) -> None:
        del frames

    def publish_dashboard(self, systems: dict[str, Any]) -> None:
        del systems

    def stop(self) -> None:
        self.stopped = True


class _FactoryStub:
    def __init__(self) -> None:
        self._mqtt = None
        self.start_mqtt_calls = 0

    def set_mqtt(self, mqtt: Any) -> None:
        self._mqtt = mqtt

    def start_mqtt(self) -> None:
        self.start_mqtt_calls += 1
        if self._mqtt is not None:
            self._mqtt.start(None, lambda: {}, lambda: {})


def _settings(url: str = "mqtt://127.0.0.1:1883") -> MqttSettings:
    return MqttSettings(
        broker=MqttBroker(host="127.0.0.1", port=1883, use_tls=False, display_url=url),
        topic_prefix="adn/1",
        client_id="adn-server-1-deadbeef",
        username=None,
        password=None,
        qos=0,
    )


def test_reload_noop_when_mqtt_unchanged():
    factory = _FactoryStub()
    current = _RecordingMqtt()
    same = _settings()
    result = reconcile_mqtt_publisher(factory, current, same, same, report_enabled=True)
    assert result is current
    assert not current.stopped


def test_reload_disconnects_when_mqtt_disabled():
    factory = _FactoryStub()
    current = _RecordingMqtt()
    result = reconcile_mqtt_publisher(factory, current, _settings(), None, report_enabled=True)
    assert result is None
    assert current.stopped
    assert factory._mqtt is None


def test_reload_enables_mqtt(monkeypatch):
    factory = _FactoryStub()

    class _NewPub(_RecordingMqtt):
        pass

    monkeypatch.setattr(
        "adn_server.infrastructure.twisted_adapters.report.mqtt_publisher.create_report_mqtt_publisher_from_settings",
        lambda _s, **_kw: _NewPub(),
    )
    result = reconcile_mqtt_publisher(factory, None, None, _settings(), report_enabled=True)
    assert isinstance(result, _NewPub)
    assert result.started
    assert factory.start_mqtt_calls == 1


def test_reload_restarts_when_broker_changes(monkeypatch):
    factory = _FactoryStub()
    old = _RecordingMqtt()

    class _NewPub(_RecordingMqtt):
        pass

    monkeypatch.setattr(
        "adn_server.infrastructure.twisted_adapters.report.mqtt_publisher.create_report_mqtt_publisher_from_settings",
        lambda _s, **_kw: _NewPub(),
    )
    result = reconcile_mqtt_publisher(
        factory,
        old,
        _settings("mqtt://127.0.0.1:1883"),
        _settings("mqtt://other:1883"),
        report_enabled=True,
    )
    assert old.stopped
    assert isinstance(result, _NewPub)
    assert result.started


def test_mqtt_settings_detect_enabled_toggle():
    off = {"GLOBAL": {"SERVER_ID": 1}, "REPORTS": {}}
    on = {
        "GLOBAL": {"SERVER_ID": 1},
        "REPORTS": {"MQTT": {"ENABLED": True, "URL": "mqtt://b:1883"}},
    }
    assert mqtt_settings_from_config(off) is None
    assert mqtt_settings_from_config(on) is not None
