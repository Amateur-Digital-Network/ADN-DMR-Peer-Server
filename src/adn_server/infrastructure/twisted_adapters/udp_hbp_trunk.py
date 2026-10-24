# ADN DMR Peer Server - infrastructure twisted adapters udp hbp trunk
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

"""HBP MASTER for Tier III sites (DMRGateway TrunkingEnabled=1): DTC* login, 71-byte DMRD and DMRT."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from typing import Any

from ...application.routing.helpers import frame_lc, is_unit_data_ingress, record_slot_rx
from ...application.trunking.ports import SiteLink
from ...application.trunking.registry import Site
from ...application.trunking.sites import TrunkSites
from ...domain import int_id
from ...domain.trunking import (
    DMRT,
    DTCACK,
    DTCC,
    DTCC_23CM_LEN,
    DTCCL,
    DTCG,
    DTCK,
    DTCL,
    DTCNAK,
    DTCO,
    DTCPING,
    DTCPONG,
    LOGIN_LEN,
    site_dmrd,
    split_site_dmrd,
)
from ..hbp_constants import (
    DMRA,
    DMRD,
    HBPF_DATA_SYNC,
    HBPF_SLT_VTERM,
    MSTNAK,
    RPTC,
    RPTK,
    RPTL,
    RPTO,
    RPTP,
    RPTPING,
)
from ..options_redaction import redact_pass_in_options
from .udp_hbp import HBPProtocol

logger = logging.getLogger(__name__)

_LOGINS = ((DTCPING, RPTPING), (DTCL, RPTL), (DTCK, RPTK), (DTCC, RPTC))
_IDLE_S = 60.0


class TrunkSiteProtocol(HBPProtocol):
    """Every peer is a Tier III site; it assumes HBPProtocol's RPT* login, admit_dmrd, peer queries, drop_peer, STATUS
    and its _on_peer_disconnected, _reset_if_no_peers and _master_maintenance_loop hooks."""

    REPLY_ACK, REPLY_NAK, REPLY_PONG, REPLY_CLOSE = DTCACK, DTCNAK, DTCPONG, DTCCL

    def __init__(
        self,
        system_name: str,
        config: dict[str, Any],
        report_factory: Any = None,
        *,
        sites: TrunkSites,
        dmrd_received: Callable[..., Any],
        on_talker_alias_stream_end: Callable[[str, bytes], None] | None = None,
        **hbp_kwargs: Any,
    ) -> None:
        super().__init__(system_name, config, report_factory, **hbp_kwargs)
        self._sites = sites
        self._route = dmrd_received
        self._stream_end = on_talker_alias_stream_end
        self._rows: dict[tuple[bytes, bytes], dict[str, Any]] = {}

    def send_dmrt(self, peer_id: bytes, payload: bytes) -> bool:
        addr = self.logged_in_addr(peer_id)
        if addr is None:
            return False
        self.transport.write(payload, addr)
        return True

    def site_connected(self, peer_id: bytes) -> bool:
        return self.logged_in_addr(peer_id) is not None

    def datagramReceived(self, data: bytes, addr: tuple[str, int]) -> None:
        command = data[:4]
        if command == DMRD:
            self._site_dmrd(data, addr)
            return
        if command == DMRT:
            self._site_dmrt(data, addr)
            return
        if data[:5] == DTCCL and len(data) == LOGIN_LEN[DTCCL]:
            self._site_closing(data[5:9], addr)
            return
        size = len(data)
        for name, rpt in _LOGINS:
            if data[: len(name)] == name and (size == LOGIN_LEN[name] or name == DTCC and size == DTCC_23CM_LEN):
                self._site_login(rpt + data[len(name):], data[len(name): len(name) + 4], addr)
                return
        if command == DTCO:
            self._site_options(data, addr)
        elif command in (DTCG, DMRA):
            logger.debug("(%s) %s from a Tier III site ignored", self._system, command.decode())
        elif command in (RPTL, RPTK, RPTC, RPTO, RPTP):
            self.transport.write(MSTNAK + (data[7:11] if command == RPTP else data[4:8]), addr)
            logger.warning(
                "(%s) classic HBP login refused on a TRUNKING system (DMRGateway needs TrunkingEnabled=1)",
                self._system,
            )
        elif command in (DTCL, DTCK, DTCC) or data[:7] == DTCPING:
            logger.debug("(%s) %s of %d bytes ignored", self._system, command.decode(), len(data))
        else:
            super().datagramReceived(data, addr)

    def _site_login(self, translated: bytes, peer_id: bytes, addr: tuple[str, int]) -> None:
        was_in = self.logged_in_addr(peer_id) is not None
        super().datagramReceived(translated, addr)
        if not was_in and self.logged_in_addr(peer_id) is not None:
            self._sites.site_logged_in(self._system, peer_id)

    def _site_closing(self, peer_id: bytes, addr: tuple[str, int]) -> None:
        if self.logged_in_addr(peer_id) != addr:
            return
        logger.info("(%s) Tier III site %s is closing down", self._system, int_id(peer_id))
        self.drop_peer(peer_id)

    def _site_options(self, data: bytes, addr: tuple[str, int]) -> None:
        peer_id = data[4:8]
        if self.logged_in_addr(peer_id) != addr:
            self.transport.write(self.REPLY_NAK + peer_id, addr)
            return
        self.transport.write(self.REPLY_ACK + peer_id, addr)
        logger.debug(
            "(%s) Tier III site %s options not applied: %s",
            self._system, int_id(peer_id), redact_pass_in_options(data[8:]),
        )

    def _site_dmrt(self, data: bytes, addr: tuple[str, int]) -> None:
        peer_id = self.logged_in_peer_at(addr)
        if peer_id is None:
            logger.debug("(%s) DMRT from %s:%s ignored: no Tier III site logged in there", self._system, *addr)
            return
        self._sites.dmrt_received(self._system, peer_id, data, time.time())

    def _site_dmrd(self, data: bytes, addr: tuple[str, int]) -> None:
        split = split_site_dmrd(data)
        if split is None:
            logger.debug("(%s) DMRD of %d bytes from a Tier III site ignored (71 expected)", self._system, len(data))
            return
        frame, uuid = split
        peer_id = frame[11:15]
        if self.logged_in_addr(peer_id) != addr:
            logger.debug(
                "(%s) DMRD from %s:%s ignored: site %s not logged in there", self._system, *addr, int_id(peer_id),
            )
            return
        seq = frame[4]
        rf_src = frame[5:8]
        dst_id = frame[8:11]
        int_dst = int_id(dst_id)
        now = time.time()
        admitted = self.admit_dmrd(frame, peer_id, rf_src, dst_id, int_dst, now)
        if admitted is None:
            return
        slot, call_type, frame_type, dtype_vseq, stream_id = admitted
        unit = call_type == "unit"
        # In-band TG 4000 idles every leg of the slot, the site's subscriptions included.
        if not unit and int_dst == 4000:
            return
        row = self._rows.get((peer_id, stream_id))
        if row is None:
            lc = frame_lc(frame_type, dtype_vseq, frame[20:53], dst_id, rf_src)
            row = self._rows[(peer_id, stream_id)] = {"RX_PEER": peer_id, "RX_LC": lc}
            self._sites.stream_started(self._system, peer_id, stream_id, uuid, rf_src, dst_id, unit, now)
        # Not RX_TIME: routing reads that as a live call on the row before the stream is accepted.
        row["_seen"] = now
        unit_data = is_unit_data_ingress(call_type, dtype_vseq, stream_id, row.get("RX_STREAM_ID"))
        # None refuses as False does (udp_hbp's _accepted); site REPEAT comes after, behind routing's packet control.
        if not self._route(
            self._system, peer_id, rf_src, dst_id, seq, slot, call_type, frame_type, dtype_vseq, stream_id, frame,
            ingress_pkt_time=now, rx_row=row,
        ):
            return
        row["RX_STREAM_ID"] = stream_id
        suppressed = row.get("_suppress_uplink")
        vterm = frame_type == HBPF_DATA_SYNC and dtype_vseq == HBPF_SLT_VTERM
        if not suppressed and not unit_data:
            # Other systems' golden rule, LoopControl and loop guard read the flat slot row, as on a shared MASTER.
            st = self.STATUS[slot]
            record_slot_rx(st, peer_id, seq, rf_src, dst_id, call_type, frame_type, dtype_vseq, stream_id, now)
            st["RX_STREAM_ID"] = stream_id
        if not suppressed and self._config.get("REPEAT", True):
            for target in self._sites.site_targets(self._system, rf_src, dst_id, unit, now, sender=peer_id):
                self._write_site(target, frame, now)
        if vterm:
            if self._stream_end:
                self._stream_end(self._system, stream_id)
            self._sites.stream_ended(self._system, peer_id, rf_src, dst_id, unit, now)

    def send_peers(
        self,
        _packet: bytes,
        _hops: bytes = b"",
        _ber: bytes = b"\x00",
        _rssi: bytes = b"\x00",
        _source_server: bytes = b"\x00\x00\x00\x00",
        _source_rptr: bytes = b"\x00\x00\x00\x00",
    ) -> None:
        if len(_packet) < 54:
            _packet = b"".join([_packet, _ber, _rssi])
        now = time.time()
        for peer_id in self._sites.site_targets(
            self._system, _packet[5:8], _packet[8:11], bool(_packet[15] & 0x40), now,
        ):
            self._write_site(peer_id, _packet, now)

    def send_peer(self, _peer: bytes, _packet: bytes, *, _skip_dual_expand: bool = False) -> None:
        if _packet[:4] == DMRD:
            self._write_site(_peer, _packet, time.time())
        else:
            super().send_peer(_peer, _packet)

    def _write_site(self, peer_id: bytes, packet: bytes, now: float) -> None:
        addr = self.logged_in_addr(peer_id)
        if addr is None:
            return
        self.transport.write(site_dmrd(packet, peer_id, self._sites.call_uuid(packet[16:20], packet[5:8], now)), addr)

    def send_dmra_to_peers(
        self,
        packets: list[bytes],
        exclude_peer: bytes | None = None,
        *,
        slot: int | None = None,
        tgid: int | None = None,
    ) -> int:
        """DMRGateway logs a master's DMRA as an unknown packet."""
        return 0

    def _on_peer_disconnected(self, peer_id: bytes) -> None:
        super()._on_peer_disconnected(peer_id)
        self._sites.site_left(self._system, peer_id, time.time())

    def _reset_if_no_peers(self) -> None:
        """Sites release their own talkgroups; a bridge reset would also clear a returning site's new ones."""

    def _master_maintenance_loop(self) -> None:
        super()._master_maintenance_loop()
        idle = time.time() - _IDLE_S
        for key in [k for k, row in self._rows.items() if row["_seen"] < idle]:
            del self._rows[key]


class ProtocolSiteLink(SiteLink):
    def __init__(self, protocols: Mapping[str, object]) -> None:
        self._protocols = protocols

    def send(self, site: Site, payload: bytes) -> bool:
        proto = self._protocols.get(site.system)
        return isinstance(proto, TrunkSiteProtocol) and proto.send_dmrt(site.peer_id, payload)

    def connected(self, site: Site) -> bool:
        proto = self._protocols.get(site.system)
        return isinstance(proto, TrunkSiteProtocol) and proto.site_connected(site.peer_id)
