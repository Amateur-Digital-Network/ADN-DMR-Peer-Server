# ADN DMR Peer Server - tests domain mesh session
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

"""Live OpenBridge session state: peer address, keepalive and source quench."""

from __future__ import annotations

from adn_server.domain import bytes_3, bytes_4
from adn_server.domain.mesh_session import (
    MeshSessionStore,
    ObpBridgeSession,
    mesh_sessions,
    obp_session,
)

_CONFIGURED = ("82.65.127.86", 62201)
_ELSEWHERE = ("85.241.222.7", 62268)
_NOW = 1_800_000_000.0


def _session(configured: tuple = _CONFIGURED) -> ObpBridgeSession:
    return ObpBridgeSession(system_name="OBP-FR", configured_peer=configured)


def _config(**overrides) -> dict:
    sys_cfg = {
        "MODE": "OPENBRIDGE",
        "ENABLED": True,
        "TARGET_IP": _CONFIGURED[0],
        "TARGET_PORT": _CONFIGURED[1],
        "TARGET_SOCK": _CONFIGURED,
    }
    sys_cfg.update(overrides)
    return {"SYSTEMS": {"OBP-FR": sys_cfg}}


# --- peer address ------------------------------------------------------------


def test_a_fresh_session_sends_to_the_configured_peer() -> None:
    session = _session()
    assert session.peer == _CONFIGURED
    assert session.peer_known


def test_learning_a_peer_moves_egress_but_not_the_configuration() -> None:
    session = _session()
    assert session.learn_peer(_ELSEWHERE, at=_NOW) is True
    assert session.peer == _ELSEWHERE
    assert session.configured_peer == _CONFIGURED
    assert session.learned_at == _NOW


def test_learning_the_same_address_twice_reports_no_move() -> None:
    session = _session()
    assert session.learn_peer(_CONFIGURED, at=_NOW) is False
    session.learn_peer(_ELSEWHERE, at=_NOW)
    assert session.learn_peer(_ELSEWHERE, at=_NOW + 1) is False
    assert session.learned_at == _NOW


def test_an_empty_address_is_not_learned() -> None:
    session = _session()
    assert session.learn_peer(("", 0), at=_NOW) is False
    assert session.learned_peer is None


def test_forgetting_falls_back_to_the_configured_peer() -> None:
    session = _session()
    session.learn_peer(_ELSEWHERE, at=_NOW)
    session.forget_learned_peer()
    assert session.peer == _CONFIGURED


def test_a_peer_that_never_resolved_is_not_known() -> None:
    assert _session((None, 62201)).peer_known is False


# --- keepalive ---------------------------------------------------------------


def test_keepalive_starts_unseen() -> None:
    session = _session()
    assert session.keepalive_seen is False
    assert session.keepalive_age(_NOW) is None
    assert session.keepalive_stale(_NOW) is False  # never seen is not the same as stale
    assert session.keepalive_ok(_NOW) is False


def test_a_fresh_keepalive_is_ok_and_an_old_one_is_stale() -> None:
    session = _session()
    session.note_keepalive(_NOW - 10)
    assert session.keepalive_ok(_NOW) is True
    assert session.keepalive_stale(_NOW) is False
    assert session.keepalive_age(_NOW) == 10
    session.note_keepalive(_NOW - 61)
    assert session.keepalive_stale(_NOW) is True
    assert session.keepalive_ok(_NOW) is False


def test_the_keepalive_timeout_can_be_narrowed() -> None:
    session = _session()
    session.note_keepalive(_NOW - 30)
    assert session.keepalive_stale(_NOW, timeout=10) is True


# --- source quench -----------------------------------------------------------


def test_a_quenched_stream_is_reported_for_its_talkgroup() -> None:
    session = _session()
    session.quench(bytes_3(214), b"strm")
    assert session.quenches(bytes_3(214), b"strm") is True
    assert session.quenches(bytes_3(214), b"other") is False
    assert session.quenches(bytes_3(215), b"strm") is False


def test_a_talkgroup_matches_whatever_width_it_arrives_in() -> None:
    """TGs travel as 3 bytes here and 4 bytes elsewhere; the number is the same."""
    session = _session()
    session.quench(bytes_4(214), b"strm")
    assert session.quenches(bytes_3(214), b"strm") is True


def test_nothing_is_quenched_on_a_fresh_session() -> None:
    assert _session().quenches(bytes_3(214), b"strm") is False


def test_a_finished_stream_releases_its_quench() -> None:
    session = _session()
    session.quench(bytes_3(214), b"strm")
    session.quench(bytes_3(215), b"other")
    session.release_stream(b"strm")
    assert session.quenches(bytes_3(214), b"strm") is False
    assert session.quenches(bytes_3(215), b"other") is True


def test_a_bridge_stunned_by_its_peer_stays_stunned() -> None:
    session = _session()
    assert session.stunned is False
    session.stun()
    assert session.stunned is True


# --- the store ---------------------------------------------------------------


def test_a_session_is_created_from_the_system_configuration() -> None:
    store = MeshSessionStore()
    session = store.session("OBP-FR", _config()["SYSTEMS"]["OBP-FR"])
    assert session.configured_peer == _CONFIGURED
    assert store.session("OBP-FR") is session


def test_a_session_falls_back_to_target_ip_and_port() -> None:
    store = MeshSessionStore()
    cfg = {"TARGET_IP": "10.0.0.1", "TARGET_PORT": "62044"}
    assert store.session("OBP-XX", cfg).configured_peer == ("10.0.0.1", 62044)


def test_an_unusable_target_port_falls_back_to_the_default() -> None:
    store = MeshSessionStore()
    assert store.session("OBP-XX", {"TARGET_IP": "10.0.0.1", "TARGET_PORT": "?"}).configured_peer == (
        "10.0.0.1",
        62044,
    )


def test_a_quench_key_that_is_not_a_talkgroup_is_ignored() -> None:
    session = _session()
    session.quenched[b"xx"] = b"strm"  # short key, as a bad peer could send
    assert session.quenches(bytes_3(214), b"strm") is False


def test_sync_registers_enabled_openbridges_only() -> None:
    config = _config()
    config["SYSTEMS"]["HOTSPOT"] = {"MODE": "MASTER", "ENABLED": True}
    config["SYSTEMS"]["OBP-OFF"] = {"MODE": "OPENBRIDGE", "ENABLED": False}
    store = MeshSessionStore()
    store.sync(config)
    assert "OBP-FR" in store
    assert "HOTSPOT" not in store
    assert "OBP-OFF" not in store
    assert len(store) == 1


def test_a_reload_that_moves_the_peer_wins_over_what_was_learned() -> None:
    """Editing TARGET_IP in the YAML is the way out of a bad learned address."""
    config = _config()
    store = MeshSessionStore()
    store.sync(config)
    store.session("OBP-FR").learn_peer(_ELSEWHERE, at=_NOW)

    config["SYSTEMS"]["OBP-FR"]["TARGET_SOCK"] = ("44.31.61.66", 62032)
    store.sync(config)
    session = store.session("OBP-FR")
    assert session.configured_peer == ("44.31.61.66", 62032)
    assert session.learned_peer is None
    assert session.peer == ("44.31.61.66", 62032)


def test_a_reload_that_changes_nothing_keeps_what_was_learned() -> None:
    config = _config()
    store = MeshSessionStore()
    store.sync(config)
    store.session("OBP-FR").learn_peer(_ELSEWHERE, at=_NOW)
    store.session("OBP-FR").note_keepalive(_NOW)

    store.sync(config)
    session = store.session("OBP-FR")
    assert session.peer == _ELSEWHERE
    assert session.last_keepalive == _NOW


def test_a_bridge_removed_from_the_config_loses_its_session() -> None:
    config = _config()
    store = MeshSessionStore()
    store.sync(config)
    config["SYSTEMS"].pop("OBP-FR")
    store.sync(config)
    assert "OBP-FR" not in store


def test_dropping_a_session_by_hand() -> None:
    store = MeshSessionStore()
    store.session("OBP-FR")
    store.drop("OBP-FR")
    assert store.get("OBP-FR") is None


def test_the_store_lives_beside_the_other_runtime_tables() -> None:
    config = _config()
    store = mesh_sessions(config)
    assert config["_MESH_SESSIONS"] is store
    assert mesh_sessions(config) is store
    assert store.get("OBP-FR") is not None


def test_one_session_per_system_from_the_server_config() -> None:
    config = _config()
    session = obp_session(config, "OBP-FR")
    assert session.configured_peer == _CONFIGURED
    assert obp_session(config, "OBP-FR") is session


def test_asking_for_an_unknown_system_creates_an_empty_session() -> None:
    """Callers ask by name; a system with no OBP config simply has no peer."""
    session = obp_session(_config(), "NOPE")
    assert session.peer_known is False
    assert session.keepalive_seen is False
