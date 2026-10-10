#!/usr/bin/env python3
# ADN DMR Peer Server - hotspot probe
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

"""Listen-only hotspot probe: what a MASTER actually delivers to one hotspot.

Logs in to a MASTER like a hotspot (HBP), subscribes to talkgroups through OPTIONS
and never transmits. For every group voice stream it receives it writes one CSV row:
when it started and ended, slot, TG, source, stream ID, frames received, the longest
silence between two frames, and whether it ended with a terminator or just stopped.

Compare the rows with the server's ``*CALL START*`` / ``*CALL END*`` lines for the same
stream ID to see whether a cut or a silence was already there when the call came in,
or appeared on the way to the hotspot.

Usage::

    ADN_PROBE_PASSWORD=... python3 tools/hotspot_probe.py --callsign CALL HOST PORT PEER_ID \\
        "TS1=214;TS2=3340,9140;" probe.csv
    python3 tools/hotspot_probe.py --summary probe.csv

Use a hotspot ID of your own (your DMR ID + two digits), the password that ID logs
in with and your callsign (a MASTER with ALLOW_UNREG_ID false checks it against the
subscriber database). Standard library only.
"""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import os
import socket
import struct
import sys
import time

FRAME_S = 0.06  # one DMR voice burst
STREAM_IDLE_S = 5.0  # no frame for this long: the stream stopped without a terminator
PING_S = 5.0
MASTER_SILENT_S = 30.0
FIELDS = ["start_utc", "end_utc", "slot", "tg", "src", "stream", "frames", "max_gap_s", "terminated"]


class Probe:
    def __init__(self, host: str, port: int, peer_id: int, password: bytes, options: str, out: str,
                 callsign: str = "") -> None:
        self.dst = (host, port)
        self.callsign = callsign
        self.pid = struct.pack(">I", peer_id)
        self.password = password
        self.options = options.encode()
        self.sock: socket.socket | None = None
        self.streams: dict[int, dict] = {}
        new = not os.path.exists(out) or os.path.getsize(out) == 0
        self.fh = open(out, "a", newline="")
        self.csv = csv.writer(self.fh)
        if new:
            self.csv.writerow(FIELDS)

    def close(self) -> None:
        if self.sock is not None:
            self.sock.close()
        self.fh.close()

    def _send(self, data: bytes) -> None:
        assert self.sock is not None
        self.sock.sendto(data, self.dst)

    def _rptc(self) -> bytes:
        def f(text: str, n: int) -> bytes:
            return text.encode().ljust(n)[:n]

        return (b"RPTC" + self.pid + f(self.callsign, 8) + f("000000000", 9) + f("000000000", 9) + f("01", 2)
                + f("01", 2) + f("0.0", 8) + f("0.0", 9) + f("000", 3) + f("hotspot probe", 20)
                + f("listen only", 19) + f("4", 1) + f("", 124) + f("hotspot_probe", 40) + f("adn-tools", 40))

    def login(self) -> bool:
        """RPTL -> RPTK -> RPTC -> RPTO on a fresh socket, so nothing late from a previous session interferes."""
        if self.sock is not None:
            self.sock.close()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(0.5)
        self._send(b"RPTL" + self.pid)
        step, deadline = "salt", time.time() + 10
        while time.time() < deadline:
            try:
                data, _ = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            if data.startswith(b"MSTNAK"):
                print(f"{time.strftime('%F %T')} login refused at step {step}", file=sys.stderr, flush=True)
                return False
            if not data.startswith(b"RPTACK"):
                continue
            if step == "salt":
                if len(data) != 10 or data[6:10] == self.pid:
                    continue  # not the challenge
                self._send(b"RPTK" + self.pid + hashlib.sha256(data[6:10] + self.password).digest())
                step = "key"
            elif step == "key":
                self._send(self._rptc())
                step = "config"
            else:
                self._send(b"RPTO" + self.pid + self.options)
                return True
        return False

    def _close(self, sid: int, terminated: bool) -> None:
        s = self.streams.pop(sid)

        def ts(t: float) -> str:
            return time.strftime("%H:%M:%S", time.gmtime(t)) + f".{int(t % 1 * 10)}"

        self.csv.writerow([ts(s["t0"]), ts(s["t1"]), s["slot"], s["tg"], s["src"], sid, s["n"],
                           f"{s['gap']:.2f}", int(terminated)])
        self.fh.flush()

    def _frame(self, data: bytes, now: float) -> None:
        if len(data) < 53 or data[15] & 0x40:  # too short, or not group voice
            return
        bits = data[15]
        sid = int.from_bytes(data[16:20], "big")
        s = self.streams.get(sid)
        if s is None:
            s = self.streams[sid] = {"t0": now, "t1": now, "slot": 2 if bits & 0x80 else 1,
                                     "tg": int.from_bytes(data[8:11], "big"),
                                     "src": int.from_bytes(data[5:8], "big"), "n": 0, "gap": 0.0}
        else:
            s["gap"] = max(s["gap"], now - s["t1"])
        s["n"] += 1
        s["t1"] = now
        if (bits >> 4) & 0x3 == 2 and bits & 0xF == 2:  # data sync + voice terminator
            self._close(sid, True)

    def run(self) -> None:
        while True:
            wait = 10
            while not self.login():
                time.sleep(wait)
                wait = min(wait * 2, 300)
            print(f"{time.strftime('%F %T')} logged in", file=sys.stderr, flush=True)
            last_ping = last_rx = time.time()
            while True:
                now = time.time()
                if now - last_ping > PING_S:
                    self._send(b"RPTPING" + self.pid)
                    last_ping = now
                if now - last_rx > MASTER_SILENT_S:
                    print(f"{time.strftime('%F %T')} master silent, logging in again", file=sys.stderr, flush=True)
                    break
                for sid in [k for k, s in self.streams.items() if now - s["t1"] > STREAM_IDLE_S]:
                    self._close(sid, False)
                try:
                    data, _ = self.sock.recvfrom(2048)  # type: ignore[union-attr]
                except socket.timeout:
                    continue
                last_rx = time.time()
                if data.startswith(b"MSTNAK"):
                    print(f"{time.strftime('%F %T')} master dropped us, logging in again", file=sys.stderr, flush=True)
                    break
                if data.startswith(b"DMRD"):
                    self._frame(data, last_rx)


def summary(path: str, gap_s: float) -> None:
    """Per hour and TG: streams, streams with a silence over gap_s, streams that stopped without a terminator."""
    rows = collections.defaultdict(lambda: [0, 0, 0, 0])
    with open(path) as fh:
        records = list(csv.DictReader(fh))
    for r in records:
        k = (r["start_utc"][:2], r["tg"])
        rows[k][0] += 1
        rows[k][1] += float(r["max_gap_s"]) > gap_s
        rows[k][2] += r["terminated"] == "0"
        rows[k][3] += int(r["frames"])
    print(f"{'hour':>4} {'tg':>8} {'streams':>8} {'gap>' + str(gap_s) + 's':>9} {'no_term':>8} {'frames':>8}")
    for (hour, tg), (n, gaps, cuts, frames) in sorted(rows.items()):
        print(f"{hour:>4} {tg:>8} {n:>8} {gaps:>9} {cuts:>8} {frames:>8}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--summary", metavar="CSV", help="print a per-hour summary of a probe CSV and exit")
    ap.add_argument("--callsign", default="", help="your callsign, sent in the hotspot config (RPTC)")
    ap.add_argument("--gap", type=float, default=1.5, help="silence counted by --summary, seconds (default 1.5)")
    ap.add_argument("args", nargs="*", help="HOST PORT PEER_ID OPTIONS OUT.csv")
    a = ap.parse_args()
    if a.summary:
        summary(a.summary, a.gap)
        return
    if len(a.args) != 5:
        ap.error("expected HOST PORT PEER_ID OPTIONS OUT.csv")
    password = os.environ.get("ADN_PROBE_PASSWORD")
    if not password:
        ap.error("set ADN_PROBE_PASSWORD (kept out of the command line and shell history)")
    host, port, peer_id, options, out = a.args
    Probe(host, int(port), int(peer_id), password.encode(), options, out, a.callsign).run()


if __name__ == "__main__":
    main()
