# ADN DMR Peer Server - alias dictionary health check surfaces fail-closed rejection risk

from __future__ import annotations

import logging

from adn_server.infrastructure.bootstrap.peer_server import _log_alias_health

logger = logging.getLogger(__name__)


def test_warns_when_id_tables_empty_and_registration_enforced(caplog) -> None:
    config = {"_PEER_IDS": {}, "_SUB_IDS": {}, "_LOCAL_SUBSCRIBER_IDS": {}, "GLOBAL": {}}
    systems_cfg = {"SYSTEM": {"ENABLED": True, "ALLOW_UNREG_ID": False}}
    with caplog.at_level(logging.ERROR):
        _log_alias_health(config, systems_cfg, logger)
    assert any("ALL EMPTY" in r.message for r in caplog.records)


def test_no_warning_when_unregistered_ids_allowed(caplog) -> None:
    config = {"_PEER_IDS": {}, "_SUB_IDS": {}, "_LOCAL_SUBSCRIBER_IDS": {}, "GLOBAL": {}}
    systems_cfg = {"SYSTEM": {"ENABLED": True, "ALLOW_UNREG_ID": True}}
    with caplog.at_level(logging.ERROR):
        _log_alias_health(config, systems_cfg, logger)
    assert not any("ALL EMPTY" in r.message for r in caplog.records)


def test_no_warning_when_id_tables_populated(caplog) -> None:
    config = {"_PEER_IDS": {1: "X"}, "_SUB_IDS": {}, "_LOCAL_SUBSCRIBER_IDS": {}, "GLOBAL": {}}
    systems_cfg = {"SYSTEM": {"ENABLED": True, "ALLOW_UNREG_ID": False}}
    with caplog.at_level(logging.ERROR):
        _log_alias_health(config, systems_cfg, logger)
    assert not any("ALL EMPTY" in r.message for r in caplog.records)


def test_warns_when_server_ids_empty_and_validation_enabled(caplog) -> None:
    config = {
        "_PEER_IDS": {1: "X"},
        "_SUB_IDS": {2: "Y"},
        "_LOCAL_SUBSCRIBER_IDS": {},
        "_SERVER_IDS": {},
        "GLOBAL": {"VALIDATE_SERVER_IDS": True},
    }
    with caplog.at_level(logging.ERROR):
        _log_alias_health(config, {}, logger)
    assert any("server_ids is EMPTY" in r.message for r in caplog.records)


def test_disabled_system_does_not_trigger_warning(caplog) -> None:
    config = {"_PEER_IDS": {}, "_SUB_IDS": {}, "_LOCAL_SUBSCRIBER_IDS": {}, "GLOBAL": {}}
    systems_cfg = {"SYSTEM": {"ENABLED": False, "ALLOW_UNREG_ID": False}}
    with caplog.at_level(logging.ERROR):
        _log_alias_health(config, systems_cfg, logger)
    assert not any("ALL EMPTY" in r.message for r in caplog.records)
