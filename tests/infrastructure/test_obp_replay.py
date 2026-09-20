# ADN DMR Peer Server - tests infrastructure obp replay
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

"""Replaying a capture: read the pcap, ask the engine, report the verdict."""

from __future__ import annotations

import struct
import time

import pytest

from adn_server.domain import bytes_3, bytes_4
from adn_server.infrastructure.hbp_constants import BCKA, DMRD
from adn_server.infrastructure.mesh.dmre_v5 import build_dmre
from adn_server.infrastructure.mesh.obp_v1 import build_bcka, build_dmrd_v1
from adn_server.infrastructure.obp_replay import format_report, replay, run_replay
from adn_server.infrastructure.pcap import CaptureError, read_udp

_PASSPHRASE = (b"test-passphrase" + b"\x00" * 20)[:20]
_FR = ("82.65.127.86", 62201)
_PT = ("85.241.222.7", 62268)
_NOW = 1_800_000_000.0


# --- building a capture on the fly -------------------------------------------


def _udp_packet(source: tuple[str, int], destination: tuple[str, int], payload: bytes) -> bytes:
    src_ip = bytes(int(part) for part in source[0].split("."))
    dst_ip = bytes(int(part) for part in destination[0].split("."))
    udp = struct.pack(">HHHH", source[1], destination[1], 8 + len(payload), 0) + payload
    total = 20 + len(udp)
    ip = struct.pack(">BBHHHBBH", 0x45, 0, total, 0, 0, 64, 17, 0) + src_ip + dst_ip
    ethernet = b"\x02" * 6 + b"\x03" * 6 + b"\x08\x00"
    return ethernet + ip + udp


def write_pcap(path, frames: list[tuple[float, tuple, tuple, bytes]]) -> None:
    with open(path, "wb") as fh:
        fh.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 262144, 1))
        for when, source, destination, payload in frames:
            packet = _udp_packet(source, destination, payload)
            fh.write(struct.pack("<IIII", int(when), int(when % 1 * 1_000_000), len(packet), len(packet)))
            fh.write(packet)


def _voice(*, src: int = 2130001, dst: int = 214, bits: int = 0x21, stream: int = 0x1234) -> bytes:
    body = b"".join(
        [
            DMRD,
            bytes([1]),
            bytes_3(src),
            bytes_3(dst),
            bytes_4(20840),
            bytes([bits]),
            bytes_4(stream),
            b"\x00" * 33,
        ]
    )
    return build_dmrd_v1(body, bytes_4(20840), _PASSPHRASE)


def _voice_v5(*, dst: int = 214, age: float = 0.0, now: float = _NOW) -> bytes:
    body = b"".join(
        [
            DMRD,
            bytes([1]),
            bytes_3(2130001),
            bytes_3(dst),
            bytes_4(20840),
            bytes([0x21]),
            bytes_4(0x4321),
            b"\x00" * 33,
        ]
    )
    packet = build_dmre(
        body,
        server_id=bytes_4(20840),
        ber=b"\x00",
        rssi=b"\x00",
        embedded_ver=5,
        timestamp_ns=int((now - age) * 1_000_000_000),
        source_server=bytes_4(2084),
        source_rptr=bytes_4(0),
        hops=b"\x00",
        passphrase=_PASSPHRASE,
        extended_layout=True,
    )
    assert packet is not None
    return packet


def _config() -> dict:
    return {
        "GLOBAL": {
            "SERVER_ID": bytes_4(21310),
            "USE_ACL": False,
            "SUB_ACL": (True, []),
            "TG1_ACL": (True, []),
        },
        "SYSTEMS": {
            "OBP-FR": {
                "MODE": "OPENBRIDGE",
                "ENABLED": True,
                "NETWORK_ID": bytes_4(20840),
                "PASSPHRASE": _PASSPHRASE,
                "TARGET_IP": _FR[0],
                "TARGET_PORT": _FR[1],
                "TARGET_SOCK": _FR,
                "RELAX_CHECKS": False,
                "VER": 1,
                "ENHANCED_OBP": True,
                "_REPORT_PORT": 62201,
            },
            "HOTSPOT": {"MODE": "MASTER", "ENABLED": True},
        },
        "_SERVER_IDS": set(),
    }


# --- the pcap reader ----------------------------------------------------------


def test_reading_udp_out_of_a_capture(tmp_path) -> None:
    capture = tmp_path / "obp.pcap"
    write_pcap(capture, [(_NOW, _FR, ("10.0.0.1", 62201), b"hello")])
    datagrams = list(read_udp(capture))
    assert len(datagrams) == 1
    assert datagrams[0].source == _FR
    assert datagrams[0].destination == ("10.0.0.1", 62201)
    assert datagrams[0].payload == b"hello"
    assert int(datagrams[0].timestamp) == int(_NOW)


def test_a_file_that_is_not_a_capture(tmp_path) -> None:
    path = tmp_path / "nope.pcap"
    path.write_bytes(b"not a capture at all, really" * 2)
    with pytest.raises(CaptureError):
        list(read_udp(path))


def test_pcapng_says_how_to_convert_it(tmp_path) -> None:
    path = tmp_path / "new.pcapng"
    path.write_bytes(b"\x0a\x0d\x0d\x0a" + b"\x00" * 40)
    with pytest.raises(CaptureError, match="editcap"):
        list(read_udp(path))


def test_reading_a_linux_cooked_v2_capture(tmp_path) -> None:
    """``tcpdump -i any`` on a recent libpcap writes SLL2, not Ethernet."""
    capture = tmp_path / "any.pcap"
    packet = _udp_packet(_FR, ("10.0.0.1", 62201), b"hello")[14:]  # drop the ethernet header
    sll2 = struct.pack(">HHIHBB", 0x0800, 0, 2, 1, 0, 6) + b"\x02" * 8
    with open(capture, "wb") as fh:
        fh.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 262144, 276))
        frame = sll2 + packet
        fh.write(struct.pack("<IIII", int(_NOW), 0, len(frame), len(frame)))
        fh.write(frame)
    datagrams = list(read_udp(capture))
    assert [d.payload for d in datagrams] == [b"hello"]
    assert datagrams[0].source == _FR


def _write_raw(path, linktype: int, frame: bytes) -> None:
    with open(path, "wb") as fh:
        fh.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 262144, linktype))
        fh.write(struct.pack("<IIII", int(_NOW), 0, len(frame), len(frame)))
        fh.write(frame)


def _ip_udp() -> bytes:
    return _udp_packet(_FR, ("10.0.0.1", 62201), b"hello")[14:]


def test_reading_a_raw_ip_capture(tmp_path) -> None:
    capture = tmp_path / "raw.pcap"
    _write_raw(capture, 101, _ip_udp())
    assert [d.payload for d in read_udp(capture)] == [b"hello"]


def test_reading_a_loopback_capture(tmp_path) -> None:
    capture = tmp_path / "null.pcap"
    _write_raw(capture, 0, struct.pack("<I", 2) + _ip_udp())
    assert [d.payload for d in read_udp(capture)] == [b"hello"]


def test_reading_a_vlan_tagged_frame(tmp_path) -> None:
    capture = tmp_path / "vlan.pcap"
    tagged = b"\x02" * 6 + b"\x03" * 6 + b"\x81\x00" + b"\x00\x64" + b"\x08\x00" + _ip_udp()
    _write_raw(capture, 1, tagged)
    assert [d.payload for d in read_udp(capture)] == [b"hello"]


def test_reading_an_ipv6_datagram(tmp_path) -> None:
    capture = tmp_path / "v6.pcap"
    payload = b"hello"
    udp = struct.pack(">HHHH", _FR[1], 62201, 8 + len(payload), 0) + payload
    header = struct.pack(">IHBB", 0x60000000, len(udp), 17, 64)
    source = bytes.fromhex("20010db8000000000000000000000001")
    destination = bytes.fromhex("20010db8000000000000000000000002")
    _write_raw(capture, 101, header + source + destination + udp)
    datagrams = list(read_udp(capture))
    assert datagrams[0].payload == payload
    assert datagrams[0].source == ("2001:db8:0:0:0:0:0:1", _FR[1])


def test_frames_that_are_not_udp_are_skipped(tmp_path) -> None:
    capture = tmp_path / "tcp.pcap"
    packet = bytearray(_ip_udp())
    packet[9] = 6  # TCP
    _write_raw(capture, 101, bytes(packet))
    assert list(read_udp(capture)) == []


def test_an_unknown_link_layer_is_skipped(tmp_path) -> None:
    capture = tmp_path / "weird.pcap"
    _write_raw(capture, 999, _ip_udp())
    assert list(read_udp(capture)) == []


def test_a_truncated_record_ends_the_walk(tmp_path) -> None:
    capture = tmp_path / "cut.pcap"
    _write_raw(capture, 101, _ip_udp())
    with open(capture, "ab") as fh:
        fh.write(struct.pack("<IIII", int(_NOW), 0, 200, 200) + b"\x45" * 10)
    assert len(list(read_udp(capture))) == 1


# --- the replay ---------------------------------------------------------------


def _replay(frames: list[tuple], config: dict | None = None, **kwargs):
    from adn_server.infrastructure.pcap import CapturedDatagram

    datagrams = [
        CapturedDatagram(timestamp=when, source=src, destination=dst, payload=payload)
        for when, src, dst, payload in frames
    ]
    return replay(config or _config(), datagrams, **kwargs)


def test_a_good_frame_from_the_configured_peer_is_delivered() -> None:
    verdicts = _replay([(_NOW, _FR, ("10.0.0.1", 62201), _voice())])
    assert [v.outcome for v in verdicts] == ["delivered"]
    assert verdicts[0].system == "OBP-FR"
    assert (verdicts[0].rf_src, verdicts[0].dst_id) == (2130001, 214)
    assert verdicts[0].kind == "DMRD v1"


def test_a_local_talkgroup_is_reported_with_its_reason_and_quench() -> None:
    verdicts = _replay([(_NOW, _FR, ("10.0.0.1", 62201), _voice(dst=9))])
    assert verdicts[0].outcome == "dropped"
    assert verdicts[0].reason == "tg-filter"
    assert verdicts[0].quench is True


def test_a_frame_from_an_unexpected_address_is_refused_when_checks_are_strict() -> None:
    verdicts = _replay([(_NOW, ("9.9.9.9", 40000), ("10.0.0.1", 62201), _voice())])
    assert verdicts[0].outcome == "refused"
    assert "source" in verdicts[0].reason


def test_a_frame_no_bridge_can_verify_is_left_unmatched() -> None:
    config = _config()
    config["SYSTEMS"]["OBP-FR"]["PASSPHRASE"] = (b"another" + b"\x00" * 20)[:20]
    verdicts = _replay([(_NOW, _FR, ("10.0.0.1", 62201), _voice())], config)
    assert verdicts[0].outcome == "unmatched"
    assert verdicts[0].system is None


def test_a_v5_frame_is_replayed_with_the_time_it_was_captured() -> None:
    config = _config()
    config["SYSTEMS"]["OBP-FR"]["VER"] = 5
    verdicts = _replay([(_NOW, _FR, ("10.0.0.1", 62201), _voice_v5())], config)
    assert verdicts[0].kind == "DMRE v5"
    assert verdicts[0].outcome == "delivered"


def test_a_v5_frame_that_was_already_late_when_captured_is_dropped() -> None:
    config = _config()
    config["SYSTEMS"]["OBP-FR"]["VER"] = 5
    verdicts = _replay([(_NOW, _FR, ("10.0.0.1", 62201), _voice_v5(age=9.0))], config)
    assert verdicts[0].reason == "stale-packet"


def test_a_v1_frame_on_a_v5_link_asks_the_peer_for_its_version() -> None:
    config = _config()
    config["SYSTEMS"]["OBP-FR"]["VER"] = 5
    verdicts = _replay([(_NOW, _FR, ("10.0.0.1", 62201), _voice())], config)
    assert verdicts[0].reason == "proto-version"


def test_control_frames_are_named_not_judged() -> None:
    verdicts = _replay([(_NOW, _FR, ("10.0.0.1", 62201), build_bcka(_PASSPHRASE))])
    assert verdicts[0].kind == "BCKA"
    assert verdicts[0].outcome == "control"
    assert verdicts[0].datagram.payload[:4] == BCKA


def test_the_bridge_is_chosen_by_the_port_the_frame_arrived_on() -> None:
    """Two bridges, one passphrase: the local port is the evidence that tells them apart."""
    config = _config()
    config["SYSTEMS"]["OBP-PT"] = {
        **config["SYSTEMS"]["OBP-FR"],
        "TARGET_IP": _PT[0],
        "TARGET_PORT": _PT[1],
        "TARGET_SOCK": _PT,
        "RELAX_CHECKS": True,
        "_REPORT_PORT": 62268,
    }
    verdicts = _replay([(_NOW, ("203.0.113.9", 50000), ("10.0.0.1", 62268), _voice())], config)
    assert verdicts[0].system == "OBP-PT"


def test_only_the_system_that_was_asked_for() -> None:
    config = _config()
    config["SYSTEMS"]["OBP-PT"] = {**config["SYSTEMS"]["OBP-FR"], "_REPORT_PORT": 62268}
    verdicts = _replay([(_NOW, _FR, ("10.0.0.1", 62201), _voice())], config, system="OBP-PT")
    assert verdicts[0].system == "OBP-PT"
    with pytest.raises(KeyError):
        _replay([], config, system="OBP-NOPE")


def test_what_this_server_sent_is_not_judged_as_ingress() -> None:
    """An unfiltered capture carries both directions; egress is not ours to admit."""
    verdicts = _replay([(_NOW, ("10.0.0.1", 62201), _FR, _voice())])
    assert verdicts[0].outcome == "outbound"
    assert verdicts[0].system is None


def test_both_directions_can_be_judged_on_purpose() -> None:
    verdicts = _replay([(_NOW, ("10.0.0.1", 62201), _FR, _voice())], only_inbound=False)
    assert verdicts[0].outcome != "outbound"


def test_server_ids_are_not_validated_without_the_table() -> None:
    """The server-id list is loaded at runtime: offline it cannot be the reason."""
    config = _config()
    config["SYSTEMS"]["OBP-FR"]["VER"] = 5
    config["GLOBAL"]["VALIDATE_SERVER_IDS"] = True
    verdicts = _replay([(_NOW, _FR, ("10.0.0.1", 62201), _voice_v5())], config)
    assert verdicts[0].outcome == "delivered"
    config["_SERVER_IDS"] = {"9999"}
    verdicts = _replay([(_NOW, _FR, ("10.0.0.1", 62201), _voice_v5())], config)
    assert verdicts[0].reason == "source-server-unknown"


def test_the_report_tallies_what_happened() -> None:
    verdicts = _replay(
        [
            (_NOW, _FR, ("10.0.0.1", 62201), _voice(stream=1)),
            (_NOW, _FR, ("10.0.0.1", 62201), _voice(dst=9, stream=2)),
            (_NOW, _FR, ("10.0.0.1", 62201), _voice(dst=9990, stream=3)),
        ]
    )
    report = format_report(verdicts, capture="obp.pcap")
    assert "3 datagram(s)" in report
    assert "OBP-FR" in report
    assert "delivered" in report
    assert "dropped: tg-filter" in report


def test_the_report_can_skip_the_per_frame_lines() -> None:
    verdicts = _replay([(_NOW, _FR, ("10.0.0.1", 62201), _voice())])
    summary = format_report(verdicts, capture="obp.pcap", verbose=False)
    assert "delivered" in summary
    assert time.strftime("%H:%M:%S", time.localtime(_NOW)) not in summary


# --- the command ---------------------------------------------------------------


def test_run_replay_prints_a_report(tmp_path, capsys) -> None:
    capture = tmp_path / "obp.pcap"
    write_pcap(
        capture,
        [
            (_NOW, _FR, ("10.0.0.1", 62201), _voice(stream=1)),
            (_NOW + 1, _FR, ("10.0.0.1", 62201), _voice(dst=9, stream=2)),
        ],
    )
    assert run_replay(_config(), str(capture)) == 0
    printed = capsys.readouterr().out
    assert "2 datagram(s)" in printed
    assert "tg-filter" in printed


def test_run_replay_stops_at_the_limit(tmp_path, capsys) -> None:
    capture = tmp_path / "obp.pcap"
    write_pcap(capture, [(_NOW + i, _FR, ("10.0.0.1", 62201), _voice(stream=i)) for i in range(5)])
    assert run_replay(_config(), str(capture), limit=2) == 0
    assert "2 datagram(s)" in capsys.readouterr().out


def test_run_replay_reports_a_bad_capture(tmp_path, capsys) -> None:
    path = tmp_path / "broken.pcap"
    path.write_bytes(b"x" * 64)
    assert run_replay(_config(), str(path)) == 1
    assert "ERROR capture" in capsys.readouterr().err


def test_run_replay_reports_an_unknown_system(tmp_path, capsys) -> None:
    capture = tmp_path / "obp.pcap"
    write_pcap(capture, [(_NOW, _FR, ("10.0.0.1", 62201), _voice())])
    assert run_replay(_config(), str(capture), system="NOPE") == 1
    assert "ERROR system" in capsys.readouterr().err
