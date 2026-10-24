# ADN DMR Peer Server - tests support dmrtc wire
#
# Copyright (C) 2026  Rodrigo Pérez, CE5RPY <ce5rpy@qmd.cl>
# Copyright (C) 2026  Joaquin Madrid Belando, EA5GVK <ea5gvk@gmail.com>
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

"""What a Tier III site sends (DMRGateway TrunkingEnabled=1 and dmrtc byte layouts), and how it reads the answers."""

from __future__ import annotations

from collections.abc import Sequence
from hashlib import sha256


def dtcl(peer_id: bytes) -> bytes:
    return b"DTCL" + peer_id


def dtck(peer_id: bytes, salt: bytes, password: bytes) -> bytes:
    """sha256(salt + password), as CDMRNetwork::writeAuthorisation."""
    return b"DTCK" + peer_id + sha256(salt + password).digest()


def dtcc(peer_id: bytes, callsign: bytes = b"CE1TEST") -> bytes:
    """8 + 294 bytes: the RPTC layout DMRGateway fills from its [Info] section."""
    body = callsign.ljust(8) + b"438800000" + b"431200000" + b"01" + b"01" + b"+40.4168" + b"-003.7038"
    body += b"001" + b"Valencia".ljust(20) + b"Tier III site".ljust(19) + b"4" + b"".ljust(124)
    body += b"DMRGateway".ljust(40) + b"dmrtc".ljust(40)
    return b"DTCC" + peer_id + body


def dtco(peer_id: bytes, options: bytes) -> bytes:
    return b"DTCO" + peer_id + options


def dtcping(peer_id: bytes) -> bytes:
    return b"DTCPING" + peer_id


def dtccl(peer_id: bytes) -> bytes:
    return b"DTCCL" + peer_id


def site_dmrd(frame: bytes, uuid: bytes) -> bytes:
    """A 55-byte DMRD as a site sends it: 71 bytes, the call UUID last."""
    assert len(frame) == 55 and len(uuid) == 16
    return frame + uuid


class GatewayLink:
    """CDMRNetwork::clock() of DMRGateway with TrunkingEnabled=1, for the master's login replies only."""

    def __init__(self, peer_id: bytes, password: bytes, *, callsign: bytes = b"CE1TEST", options: bytes = b"") -> None:
        self.peer_id = peer_id
        self.password = password
        self.callsign = callsign
        self.options = options
        self.status = "WAITING_LOGIN"
        # DMRT 0xC1 queued for dmrtc: only a final DTCACK does that, and dmrtc then sends its subscriptions.
        self.master_login = False

    def login(self) -> bytes:
        self.status = "WAITING_LOGIN"
        self.master_login = False
        return dtcl(self.peer_id)

    def received(self, data: bytes) -> bytes | None:
        """The next datagram the gateway sends for ``data``, or None."""
        if data[:6] in (b"MSTNAK", b"DTCNAK"):
            self.status = "WAITING_LOGIN" if self.status == "RUNNING" else "FAILED"
        elif data[:5] in (b"MSTCL", b"DTCCL"):
            self.status = "CLOSED"
        elif data[:6] in (b"RPTACK", b"DTCACK"):
            return self._acked(data, data[:6] == b"DTCACK")
        return None

    def _acked(self, data: bytes, dtc: bool) -> bytes | None:
        if self.status == "WAITING_LOGIN":
            self.status = "WAITING_AUTHORISATION"
            return dtck(self.peer_id, data[6:10], self.password)
        if self.status == "WAITING_AUTHORISATION":
            self.status = "WAITING_CONFIG"
            return dtcc(self.peer_id, self.callsign)
        if self.status == "WAITING_CONFIG" and self.options:
            self.status = "WAITING_OPTIONS"
            return dtco(self.peer_id, self.options)
        if self.status in ("WAITING_CONFIG", "WAITING_OPTIONS"):
            self.status = "RUNNING"
            self.master_login = dtc
        return None


def _addresses(values: Sequence[int]) -> bytes:
    return b"".join(value.to_bytes(3, "big") for value in values)


def _read_addresses(msg: bytes, offset: int, count: int) -> list[int]:
    return [int.from_bytes(msg[i:i + 3], "big") for i in range(offset, offset + 3 * count, 3)]


def dmrt_registration(op: int, ids: Sequence[int], ts: int) -> bytes:
    """0x01 or 0x02: unix time (uint64 LE) at [5..12], count at [13], ids from [14]."""
    return b"DMRT" + bytes((op,)) + ts.to_bytes(8, "little") + bytes((len(ids),)) + _addresses(ids)


def dmrt_subscription(op: int, tgs: Sequence[int]) -> bytes:
    """0x03 or 0x04: count at [5], talkgroups from [6]."""
    return b"DMRT" + bytes((op, len(tgs))) + _addresses(tgs)


def dmrt_udt(uuid: bytes, src: int, dst: int, text: bytes, *, group: bool = False) -> bytes:
    """0x05 as createUDTTransferMessage: UUID, type (bit 7 group, bit 6 private, format 4), size, src, dst, text."""
    return b"DMRT\x05" + uuid + bytes(((0x80 if group else 0x40) | 4, len(text))) + _addresses((src, dst)) + text


def dmrt_call_setup(uuid: bytes, src: int, dst: int, options: int = 0) -> bytes:
    """0x07 as createPrivateCallSetupMessage: UUID, service options, src, dst (28 bytes)."""
    return b"DMRT\x07" + uuid + bytes((options,)) + _addresses((src, dst))


def dmrt_reply(op: int, uuid: bytes, src: int, dst: int) -> bytes:
    """0x06, 0x08 or 0x09: the UUID they answer, src, dst (27 bytes)."""
    return b"DMRT" + bytes((op,)) + uuid + _addresses((src, dst))


def dmrt_status(uuid: bytes, src: int, dst: int, status: int, *, group: bool = False) -> bytes:
    """0x0A as createStatusTransferMessage: UUID, bit 7 group + 7-bit status, src, dst (28 bytes)."""
    return b"DMRT\x0a" + uuid + bytes(((0x80 if group else 0) | status,)) + _addresses((src, dst))


def read_registration_confirmation(msg: bytes) -> tuple[int, list[tuple[int, bool]]]:
    """0x41: its time and (id, accepted) per address; dmrtc reads bit i of [13] for address i."""
    count = msg[14]
    assert msg[:5] == b"DMRT\x41" and len(msg) == 15 + 3 * count
    accepted = [bool(msg[13] >> i & 1) for i in range(count)]
    return int.from_bytes(msg[5:13], "little"), list(zip(_read_addresses(msg, 15, count), accepted))


def read_deregistration(msg: bytes) -> list[int]:
    """0x42, laid out as a site's 0x02."""
    count = msg[13]
    assert msg[:5] == b"DMRT\x42" and len(msg) == 14 + 3 * count
    return _read_addresses(msg, 14, count)


def read_subscription_confirmation(msg: bytes) -> tuple[int, list[int]]:
    """0x43 or 0x44: the opcode, and the talkgroups with 0 where one was refused."""
    count = msg[5]
    assert msg[:4] == b"DMRT" and len(msg) == 6 + 3 * count
    return msg[4], _read_addresses(msg, 6, count)
