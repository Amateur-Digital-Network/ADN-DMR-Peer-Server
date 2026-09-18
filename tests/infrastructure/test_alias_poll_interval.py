# ADN DMR Peer Server - ALIASES.POLL_INTERVAL_SEC resolution never aborts startup

from __future__ import annotations

import logging

from adn_server.infrastructure.bootstrap.peer_server import (
    _DEFAULT_ALIAS_POLL_INTERVAL_SEC,
    _resolve_alias_poll_interval,
)

logger = logging.getLogger(__name__)


def test_missing_key_defaults_to_900() -> None:
    assert _resolve_alias_poll_interval({}, logger) == _DEFAULT_ALIAS_POLL_INTERVAL_SEC


def test_empty_string_defaults_to_900() -> None:
    assert _resolve_alias_poll_interval({"POLL_INTERVAL_SEC": ""}, logger) == 900.0


def test_valid_value_is_used() -> None:
    assert _resolve_alias_poll_interval({"POLL_INTERVAL_SEC": 60}, logger) == 60.0


def test_valid_string_value_is_parsed() -> None:
    assert _resolve_alias_poll_interval({"POLL_INTERVAL_SEC": "120"}, logger) == 120.0


def test_non_numeric_value_falls_back_without_raising() -> None:
    assert _resolve_alias_poll_interval({"POLL_INTERVAL_SEC": "900s"}, logger) == 900.0


def test_zero_falls_back_without_raising() -> None:
    assert _resolve_alias_poll_interval({"POLL_INTERVAL_SEC": 0}, logger) == 900.0


def test_negative_falls_back_without_raising() -> None:
    assert _resolve_alias_poll_interval({"POLL_INTERVAL_SEC": -5}, logger) == 900.0


def test_none_type_falls_back_without_raising() -> None:
    assert _resolve_alias_poll_interval({"POLL_INTERVAL_SEC": None}, logger) == 900.0
