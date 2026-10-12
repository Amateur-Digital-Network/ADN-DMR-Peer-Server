# ADN DMR Peer Server - tests tools hotspot probe
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

"""tools/hotspot_probe.py: logs in like a hotspot and records what reaches it."""

from __future__ import annotations

import csv
import importlib.util
import socket
import struct
import threading
from pathlib import Path

from adn_server.infrastructure.twisted_adapters.udp_hbp import _calc_hash

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "hotspot_probe.py"
_spec = importlib.util.spec_from_file_location("hotspot_probe", _TOOL)
hotspot_probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hotspot_probe)  # type: ignore[union-attr]

PEER = 213003599
PASSWORD = b"probe-pass"


def _fake_master(sock: socket.socket, seen: list[bytes]) -> None:
    """Challenge, check the key with the server's own hash, ack config and options."""
    salt = struct.pack(">I", 0x12345678)
    while True:
        try:
            data, addr = sock.recvfrom(2048)
        except OSError:
            return
        seen.append(data[:4])
        peer = data[4:8]
        if data.startswith(b"RPTL"):
            sock.sendto(b"RPTACK" + salt, addr)
        elif data.startswith(b"RPTK"):
            ok = data[8:40] == _calc_hash(salt, PASSWORD)
            sock.sendto((b"RPTACK" if ok else b"MSTNAK") + peer, addr)
            if not ok:
                return
        elif data.startswith(b"RPTC"):
            assert len(data) == 302, "RPTC must be the 302-byte hotspot config"
            sock.sendto(b"RPTACK" + peer, addr)
        elif data.startswith(b"RPTO"):
            sock.sendto(b"RPTACK" + peer, addr)
            return


def _probe(tmp_path: Path, password: bytes = PASSWORD) -> tuple[bool, list[bytes]]:
    master = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    master.bind(("127.0.0.1", 0))
    seen: list[bytes] = []
    t = threading.Thread(target=_fake_master, args=(master, seen), daemon=True)
    t.start()
    probe = hotspot_probe.Probe("127.0.0.1", master.getsockname()[1], PEER, password, "TS2=3340;", str(tmp_path / "p.csv"))
    ok = probe.login()
    t.join(2)
    master.close()
    probe.close()
    return ok, seen


def test_login_completes_the_hbp_exchange(tmp_path: Path) -> None:
    ok, seen = _probe(tmp_path)
    assert ok
    assert seen == [b"RPTL", b"RPTK", b"RPTC", b"RPTO"]


def test_wrong_password_is_refused(tmp_path: Path) -> None:
    ok, seen = _probe(tmp_path, password=b"wrong")
    assert not ok
    assert seen == [b"RPTL", b"RPTK"]


def _dmrd(stream: int, bits: int, tg: int = 3340, src: int = 3340062) -> bytes:
    return (b"DMRD" + b"\x00" + src.to_bytes(3, "big") + tg.to_bytes(3, "big") + b"\x00" * 4
            + bytes([bits]) + stream.to_bytes(4, "big") + b"\x00" * 33)


def test_streams_are_recorded_with_their_longest_silence(tmp_path: Path) -> None:
    out = tmp_path / "p.csv"
    probe = hotspot_probe.Probe("127.0.0.1", 9, PEER, PASSWORD, "", str(out))
    header, voice, term = 0x80 | 0x20 | 1, 0x80 | 0x01, 0x80 | 0x20 | 2  # TS2 group: VHEAD, burst A, VTERM
    t = 1000.0
    probe._frame(_dmrd(7, header), t)
    for i in range(1, 11):
        probe._frame(_dmrd(7, voice), t + 0.06 * i)
    probe._frame(_dmrd(7, voice), t + 0.6 + 2.5)  # 2.5 s with nothing, then the rest
    probe._frame(_dmrd(7, term), t + 0.6 + 2.56)
    # a second stream that just stops: closed as not terminated once idle
    probe._frame(_dmrd(8, header, tg=9140), t + 10)
    probe._frame(_dmrd(8, voice, tg=9140), t + 10.06)
    probe._close(8, False)
    probe.close()
    with open(out) as fh:
        rows = list(csv.DictReader(fh))
    assert [(r["tg"], r["frames"], r["terminated"]) for r in rows] == [("3340", "13", "1"), ("9140", "2", "0")]
    assert float(rows[0]["max_gap_s"]) == 2.5
    assert rows[0]["slot"] == "2" and rows[0]["src"] == "3340062"


def test_private_calls_are_ignored(tmp_path: Path) -> None:
    probe = hotspot_probe.Probe("127.0.0.1", 9, PEER, PASSWORD, "", str(tmp_path / "p.csv"))
    probe._frame(_dmrd(9, 0x80 | 0x40 | 0x20 | 1), 1000.0)
    probe.close()
    assert probe.streams == {}
