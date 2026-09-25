# ADN DMR Peer Server - tests harness voice helpers
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

"""Shared fakes for VoiceUseCases tests."""

from __future__ import annotations

from typing import Any, Iterator

from adn_server.domain import bytes_3, bytes_4
from tests.harness.deterministic import DeterministicScenario, FakeHbpProtocol, active_routing_table


class FakeVoiceProvider:
    def get_ambe_words(self, languages: str, audio_path: str) -> dict[str, dict[str, Any]]:
        return {languages: {"silence": b"\x00" * 7}}

    def pkt_gen(
        self,
        rf_src: bytes,
        dst_id: bytes,
        peer: bytes,
        slot: int,
        phrase: list[Any],
    ) -> Iterator[bytes]:
        del phrase
        ts_bit = 0x80 if slot else 0
        stream_id = bytes_4(0xA0A0A0A0)
        for seq in range(3):
            yield b"DMRD" + bytes([seq]) + rf_src[:3] + dst_id[:3] + peer[:4] + bytes([ts_bit | 0x10]) + stream_id + b"\x00" * 33 + b"\x00\x00"

    def read_single_file(self, audio_path: str, lang: str, file_number: str) -> list:
        del audio_path, lang, file_number
        return [b"\x00" * 7]

class FakeMasterForVoice(FakeHbpProtocol):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self._system = name
        self.sent: list[bytes] = []

    def send_system(self, packet: bytes) -> None:
        self.sent.append(packet)


def voice_master_scenario(tg: int = 91) -> tuple[DeterministicScenario, FakeMasterForVoice]:
    config = DeterministicScenario().config
    master = FakeMasterForVoice("MASTER-A")
    config["PROXY"] = {"TARGET_SYSTEM": "MASTER-A"}
    config["SYSTEMS"]["MASTER-A"]["PEERS"] = {
        "1001": {"CALLSIGN": "TEST", "IP": "127.0.0.1", "PORT": 62040},
    }
    bridges = active_routing_table(tg, (("MASTER-A", 2), ("MASTER-B", 2)))
    scenario = DeterministicScenario(config=config, routing_table=bridges)
    scenario.protocols["MASTER-A"] = master
    master.STATUS[2] = {
        "RX_TYPE": 2,
        "TX_TYPE": 2,
        "RX_STREAM_ID": b"\x00" * 4,
    }
    return scenario, master


def reflector_routing_entry(system: str = "MASTER-A", reflector: int = 310) -> dict[str, list[dict[str, Any]]]:
    return {
        "#{}".format(reflector): [
            {
                "SYSTEM": system,
                "TS": 2,
                "TGID": bytes_3(9),
                "ACTIVE": True,
                "TIMEOUT": 600,
                "TO_TYPE": "ON",
                "ON": [bytes_3(reflector)],
                "OFF": [],
                "RESET": [],
                "TIMER": 1000.0,
            }
        ]
    }
