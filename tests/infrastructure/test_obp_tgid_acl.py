# ADN DMR Peer Server - tests infrastructure OpenBridge TGID_ACL
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

"""TGID_ACL on an OpenBridge was never applied: TG1_ACL came from TGID_TS1_ACL or PERMIT:ALL."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from adn_server.infrastructure.acl_router import InMemoryAclRouter
from adn_server.infrastructure.config_loader import YamlConfigLoader
from adn_server.infrastructure.config_reload import prepare_incoming_config

EXAMPLE = Path(__file__).resolve().parents[2] / "adn-server.example.yaml"
TGID_ACL = "DENY:0-82,92-199,800-899,9990-9999,730999"


def _load(tmp_path: Path, **obp: object) -> dict:
    data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    sys_cfg = data["SYSTEMS"]["OBP-TEST"]
    sys_cfg.pop("TGID_TS1_ACL", None)
    sys_cfg.update(obp)
    data["SYSTEMS"]["SYSTEM"]["TGID_TS1_ACL"] = "DENY:0-89"
    path = tmp_path / "adn-server.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return prepare_incoming_config(YamlConfigLoader(), str(path), logging.getLogger("test"))


def _permits(config: dict, system: str, tg: int) -> bool:
    return InMemoryAclRouter().acl_check(tg, config["SYSTEMS"][system]["TG1_ACL"])


def test_an_openbridge_applies_its_tgid_acl(tmp_path: Path) -> None:
    config = _load(tmp_path, TGID_ACL=TGID_ACL)

    for tg in (9990, 730999, 150, 850):
        assert not _permits(config, "OBP-TEST", tg)
    for tg in (91, 730, 214, 730170):
        assert _permits(config, "OBP-TEST", tg)


def test_tgid_ts1_acl_does_not_replace_it_on_an_openbridge(tmp_path: Path) -> None:
    config = _load(tmp_path, TGID_ACL=TGID_ACL, TGID_TS1_ACL="DENY:0-89")

    assert not _permits(config, "OBP-TEST", 9990)
    assert not _permits(config, "OBP-TEST", 730999)


def test_a_master_keeps_its_slot_acl(tmp_path: Path) -> None:
    config = _load(tmp_path, TGID_ACL=TGID_ACL)

    assert not _permits(config, "SYSTEM", 85)
    assert _permits(config, "SYSTEM", 9990)
