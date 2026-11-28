# ADN DMR Peer Server - alias dictionary health check surfaces fail-closed rejection risk

from __future__ import annotations

import logging

from adn_server.infrastructure.bootstrap.peer_server import _log_alias_health
from adn_server.infrastructure.persistence.alias_loader import FIXED_SUBSCRIBER_IDS

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


def test_warns_when_only_the_fixed_subscribers_are_loaded(caplog) -> None:
    """Startup adds D-APRS and SC to every subscriber list, loaded or not."""
    config = {"_PEER_IDS": {}, "_SUB_IDS": dict(FIXED_SUBSCRIBER_IDS), "_LOCAL_SUBSCRIBER_IDS": {}, "GLOBAL": {}}
    systems_cfg = {"SYSTEM": {"ENABLED": True, "ALLOW_UNREG_ID": False}}
    with caplog.at_level(logging.ERROR):
        _log_alias_health(config, systems_cfg, logger)
    assert any("ALL EMPTY" in r.message for r in caplog.records)


def test_warns_when_the_local_list_is_the_subscriber_list(caplog) -> None:
    """Without LOCAL_SUBSCRIBER_FILE the loader hands back the same dict for both."""
    subs = dict(FIXED_SUBSCRIBER_IDS)
    config = {"_PEER_IDS": {}, "_SUB_IDS": subs, "_LOCAL_SUBSCRIBER_IDS": subs, "GLOBAL": {}}
    systems_cfg = {"SYSTEM": {"ENABLED": True, "ALLOW_UNREG_ID": False}}
    with caplog.at_level(logging.ERROR):
        _log_alias_health(config, systems_cfg, logger)
    assert any("ALL EMPTY" in r.message for r in caplog.records)
