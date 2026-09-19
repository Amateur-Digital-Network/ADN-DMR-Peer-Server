"""Tests for config_validator."""

from __future__ import annotations

import pytest

from adn_server.domain.errors import ConfigError
from adn_server.infrastructure.config_validator import validate_config


def _minimal_config(**global_overrides) -> dict:
    return {
        "GLOBAL": {
            "PING_TIME": 10,
            "SERVER_ID": 73010,
            **global_overrides,
        },
        "REPORTS": {"REPORT": True},
        "LOGGER": {"LOG_HANDLERS": "console-timed"},
        "SYSTEMS": {},
    }


def test_accepts_string_port_security() -> None:
    validate_config(_minimal_config(PORT_SECURITY="7070", URL_SECURITY="10.0.0.1", PASS_SECURITY="x"))


def test_rejects_int_port_security() -> None:
    with pytest.raises(ConfigError) as exc:
        validate_config(
            _minimal_config(PORT_SECURITY=7070, URL_SECURITY="10.0.0.1", PASS_SECURITY="x"),
            config_path="/tmp/adn-server.yaml",
        )
    msg = str(exc.value)
    assert "GLOBAL.PORT_SECURITY" in msg
    assert "expected string" in msg
    assert "int" in msg


def test_reports_missing_security_fields_when_url_set() -> None:
    with pytest.raises(ConfigError) as exc:
        validate_config(_minimal_config(URL_SECURITY="10.0.0.1"), config_path="adn-server.yaml")
    msg = str(exc.value)
    assert "GLOBAL.PORT_SECURITY" in msg
    assert "GLOBAL.PASS_SECURITY" in msg


def test_rejects_proxy_and_obp_proxy_same_port() -> None:
    config = _minimal_config()
    config["SYSTEMS"] = {
        "MASTER1": {"MODE": "MASTER"},
        "OBP1": {
            "MODE": "OPENBRIDGE",
            "NETWORK_ID": 73044,
            "PASSPHRASE": "x",
            "TARGET_IP": "127.0.0.1",
            "TARGET_PORT": 62044,
        },
    }
    config["PROXY"] = {"TARGET_SYSTEM": "MASTER1", "LISTEN_PORT": 62032}
    config["OBP_PROXY"] = {"LISTEN_PORT": 62032}
    config["DATABASE"] = {"DB_NAME": "x", "DB_USERNAME": "x"}
    with pytest.raises(ConfigError) as exc:
        validate_config(config)
    assert "PROXY.LISTEN_PORT and OBP_PROXY.LISTEN_PORT" in str(exc.value)


def test_collects_multiple_errors() -> None:
    with pytest.raises(ConfigError) as exc:
        validate_config(
            {
                "GLOBAL": {
                    "URL_SECURITY": 1,
                    "PORT_SECURITY": 7070,
                    "PING_TIME": "ten",
                },
                "REPORTS": {"REPORT": "yes"},
                "LOGGER": {"LOG_HANDLERS": 123},
                "SYSTEMS": {"ECHO": {"MODE": "MASTER", "PORT": "bad", "ENABLED": "True"}},
            },
            config_path="bad.yaml",
        )
    msg = str(exc.value)
    assert "bad.yaml" in msg
    assert msg.count("  - ") >= 4
