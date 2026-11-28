"""VALIDATE_SERVER_IDS checks against the server list, not against its absence.

An empty list means the download never succeeded (the startup and every reload
log it as an error). Refusing every 4-5 digit source server on it cut the whole
mesh off over one failed download; a loaded list still refuses unknown servers.
"""

from __future__ import annotations

from tests.harness.obp_ingress import observe


def _case(**overrides: object) -> dict:
    return {"kind": "v5", "name": "unloaded", "desc": "unloaded", "validate_server_ids": True,
            "source_server": 2131, **overrides}


def test_a_list_that_never_loaded_lets_source_servers_through() -> None:
    assert observe(_case(server_ids={}))["delivered"]


def test_a_loaded_list_still_refuses_an_unknown_server() -> None:
    effects = observe(_case(server_ids={"2084": "ADN_2084_France"}))
    assert not effects["delivered"]
    assert any("not in list" in text for _, text in effects["log"])
