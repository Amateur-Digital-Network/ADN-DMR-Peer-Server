# ADN DMR Peer Server - application ports
# Copyright (C) 2026  Rodrigo Pérez, CE5RPY <ce5rpy@qmd.cl>
#
# Derived from ADN DMR Server / FreeDMR  / HBlink. Original license:
###############################################################################
# Copyright (C) 2020 Simon Adlem, G7RZU <g7rzu@gb7fr.org.uk>
# Copyright (C) 2016-2019 Cortney T. Buffington, N0MJS <n0mjs@me.com>
#
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

"""Abstract ports (interfaces) for infrastructure adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from adn_server.domain.dynamic_tg import DynamicTgEntry
    from adn_server.domain.subscription import AudioChannel, Subscription, SubscriptionId, SubscriptionPhase, SystemId


class ConfigLoader(ABC):
    """Load and validate config (YAML at project root). Returns same semantic structure as legacy INI."""

    @abstractmethod
    def load(self, path: str | None = None) -> dict[str, Any]:
        """Load config; path None = default (project root adn-server.yaml)."""
        ...

    @abstractmethod
    def reload_voice_config(self, config: dict[str, Any], voice_path: str | None = None) -> None:
        """Hot-reload adn-voice.yaml into config["VOICE"]."""
        ...


class AliasLoader(ABC):
    """Load and refresh alias dicts (peer_ids, subscriber_ids, talkgroup_ids, server_ids, checksums)."""

    @abstractmethod
    def load_aliases(self, config: dict[str, Any]) -> tuple[
        dict[int, str],  # peer_ids
        dict[int, str],  # subscriber_ids
        dict[int, str],  # talkgroup_ids
        dict[int, str],  # local_subscriber_ids
        dict[str, str],  # server_ids
        dict[str, str],  # checksums
    ]:
        """Build alias dicts from config (downloads + files). Same as legacy mk_aliases."""
        ...


class SubMapStore(ABC):
    """Persist and load SUB_MAP (bytes_3(peer) -> (callsign, slot, time))."""

    @abstractmethod
    def load(self, path: str) -> dict[bytes, tuple[str, int, float]]:
        """Load SUB_MAP from pickle file."""
        ...

    @abstractmethod
    def save(self, path: str, sub_map: dict[bytes, tuple[str, int, float]]) -> None:
        """Save SUB_MAP to pickle file."""
        ...


class KeysStore(ABC):
    """Load/save keys JSON (e.g. system API key)."""

    @abstractmethod
    def load(self, path: str) -> dict[str, Any]:
        """Load keys from JSON."""
        ...

    @abstractmethod
    def save(self, path: str, keys: dict[str, Any]) -> None:
        """Save keys to JSON."""
        ...


class ReportWireEncoder(ABC):
    """Outbound port: encode one report protocol variant into zero or more TCP frames."""

    @abstractmethod
    def hello_frames(self, systems: dict[str, Any]) -> tuple[bytes, ...]:
        """HELLO (0xFF) frame(s) for this variant."""
        ...

    @abstractmethod
    def config_frames(self, systems: dict[str, Any], *, full_snapshot: bool) -> tuple[bytes, ...]:
        """CONFIG_SND / TOPOLOGY_SND / delta frames (empty if nothing to send)."""
        ...

    @abstractmethod
    def bridge_frames(self, bridges: dict[str, Any], *, full_snapshot: bool) -> tuple[bytes, ...]:
        """BRIDGE_SND / ROUTING_TABLE_SND / delta frames."""
        ...

    @abstractmethod
    def bridge_event_frames(self, event: str) -> tuple[bytes, ...]:
        """BRDG_EVENT / VOICE_EVENT_SND frames."""
        ...


class ReportMqttPublisher(ABC):
    """Optional second sink: publish the same report v2 JSON payloads to an MQTT broker."""

    @abstractmethod
    def start(
        self,
        wire: ReportWireEncoder,
        get_systems: Any,
        routing_table_for_report: Any,
    ) -> None:
        """Connect to broker and publish bootstrap snapshots (hello + full topology + routing)."""
        ...

    @abstractmethod
    def publish_frames(self, frames: tuple[bytes, ...]) -> None:
        """Publish zero or more wire frames (opcode + JSON) to MQTT topics."""
        ...

    @abstractmethod
    def publish_dashboard(self, systems: dict[str, Any]) -> None:
        """Publish slim ``dashboard_state`` (linked systems only)."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Disconnect from broker."""
        ...


class ReportSender(ABC):
    """Send config and bridge state to report TCP clients (CONFIG_SND, BRIDGE_SND, BRDG_EVENT)."""

    @abstractmethod
    def set_systems(self, systems: dict[str, Any]) -> None:
        """Update cached SYSTEMS snapshot used by the wire encoder."""
        ...

    @abstractmethod
    def set_routing_table(self, bridges: dict[str, Any]) -> None:
        """Update cached BRIDGES snapshot used by the wire encoder."""
        ...

    @abstractmethod
    def send_config(self, systems: dict[str, Any], *, incremental: bool = False) -> None:
        """Send CONFIG_SND (pickle systems) or topology / delta JSON."""
        ...

    @abstractmethod
    def send_routing_table(self, bridges: dict[str, Any], *, incremental: bool = False) -> None:
        """Send BRIDGE_SND (pickle bridges) or routing_table / delta JSON."""
        ...

    @abstractmethod
    def send_routing_event(self, event: str) -> None:
        """Send BRDG_EVENT (opcode + event string)."""
        ...


class AclRouter(ABC):
    """ACL range checks for registration and voice ingress (legacy acl_check)."""

    @abstractmethod
    def acl_check(self, id_bytes_or_int: bytes | int, acl: tuple[bool, list[tuple[int, int]]]) -> bool:
        """Check ID against ACL; return True if permitted."""
        ...


class VoiceProvider(ABC):
    """AMBE voice packets, file announcements, TTS. Used for announcements and playback."""

    @abstractmethod
    def get_ambe_words(self, languages: str, audio_path: str) -> dict[str, dict[str, Any]]:
        """Load AMBE words by language (readAMBE.readfiles)."""
        ...

    @abstractmethod
    def pkt_gen(self, rf_src: bytes, dst_id: bytes, peer: bytes, slot: int, phrase: list[Any]) -> Any:
        """Generate HBP voice packets for a phrase (generator). Legacy mk_voice.pkt_gen."""
        ...

    def read_single_file(self, audio_path: str, lang: str, file_number: str) -> list:
        """Read one AMBE file (e.g. ondemand/{file_number}.ambe). Legacy readSingleFile for playFileOnRequest."""
        return []


class SecurityDownloader(ABC):
    """Periodic security downloads (passwords, encryption). Legacy security_downloader."""

    @abstractmethod
    def init_downloads(self, config: dict[str, Any]) -> None:
        """One-time init (e.g. create dirs)."""
        ...

    @abstractmethod
    def periodic_download(self, config: dict[str, Any]) -> bool:
        """Periodic password/encryption download. Returns True when passwords file was updated."""
        ...


class DmrEmbeddedLcEncoder(Protocol):
    """Encode a 9-byte DMR embedded LC into burst B–E BPTC fragments (vseq 1–4)."""

    def __call__(self, lc: bytes) -> dict[int, Any]:
        ...


class TalkerAliasEmblcEncoder(Protocol):
    """Encode Talker Alias into embedded-LC burst dicts for DMRD overlay."""

    def encode_text(
        self,
        text: str,
        *,
        text_formats: Sequence[str] | None = None,
    ) -> tuple[list[dict[int, Any]], int]:
        ...

    def encode_blocks(self, blocks: dict[int, bytes]) -> tuple[list[dict[int, Any]], int]:
        ...


class SubscriptionStore(ABC):
    """Authoritative in-memory subscription registry (Phase 2; replaces BRIDGES dict over time)."""

    @abstractmethod
    def get(self, sub_id: "SubscriptionId") -> "Subscription | None":
        ...

    @abstractmethod
    def upsert(self, subscription: "Subscription") -> None:
        ...

    @abstractmethod
    def remove(self, sub_id: "SubscriptionId") -> bool:
        ...

    @abstractmethod
    def clear(self) -> None:
        ...

    @abstractmethod
    def replace_all(self, subscriptions: Sequence["Subscription"]) -> None:
        ...

    @abstractmethod
    def snapshot(self) -> tuple["Subscription", ...]:
        ...

    @abstractmethod
    def list_by_channel(self, channel: "AudioChannel") -> tuple["Subscription", ...]:
        ...

    @abstractmethod
    def list_by_system(self, system: "SystemId") -> tuple["Subscription", ...]:
        ...

    @abstractmethod
    def list_active(self) -> tuple["Subscription", ...]:
        ...

    @abstractmethod
    def list_by_phase(self, phase: "SubscriptionPhase") -> tuple["Subscription", ...]:
        ...

    @abstractmethod
    def relay_tables_with_active_source(
        self,
        system: str,
        slot: int,
        dst_tgid: int,
    ) -> tuple[str, ...]:
        ...

    @abstractmethod
    def legs_in_table(self, table_key: str) -> tuple["Subscription", ...]:
        ...

    @abstractmethod
    def has_table(self, table_key: str) -> bool:
        """True when at least one leg belongs to ``table_key``; runs per datagram."""
        ...

    @abstractmethod
    def has_active_target_leg(self, system: str, slot: int, tgid: int) -> bool:
        ...


class ProxySlotStore(ABC):
    """Hotspot session registry keyed by peer_id (Phase 3)."""

    @abstractmethod
    def bind(self, slot: "ClientSlot") -> None:
        ...

    @abstractmethod
    def update_client(self, peer_id: bytes, host: str, port: int) -> None:
        ...

    @abstractmethod
    def unbind(self, peer_id: bytes) -> "ClientSlot | None":
        ...

    @abstractmethod
    def get_by_peer(self, peer_id: bytes) -> "ClientSlot | None":
        ...

    @abstractmethod
    def list_slots(self) -> tuple["ClientSlot", ...]:
        ...


class PendingRptoQueue(ABC):
    """Pending RPTO payloads for self-service / login options push."""

    @abstractmethod
    def enqueue(self, peer_id: bytes, payload: bytes) -> None:
        ...

    @abstractmethod
    def dequeue(self) -> tuple[bytes, bytes] | None:
        ...


class ProxySelfServiceStore(ABC):
    """Self-service ``Clients`` table (legacy adn-proxy / hotspot_proxy_self_service)."""

    @abstractmethod
    def test_db(self) -> Any:
        """Verify DB connectivity. Returns Twisted Deferred."""

    @abstractmethod
    def ins_conf(
        self,
        int_id: int,
        peer_id_bytes: bytes,
        callsign: str,
        host: str,
        mode: str,
    ) -> None:
        ...

    @abstractmethod
    def updt_tbl(
        self,
        action: str,
        peer_id_bytes: bytes,
        *,
        psswd: str | None = None,
    ) -> None:
        ...

    @abstractmethod
    def slct_opt(self, peer_id_bytes: bytes) -> Any:
        """Returns Deferred firing with row list, e.g. ``((options_str,),)``."""

    @abstractmethod
    def slct_db(self) -> Any:
        """Returns Deferred firing with ``(dmr_id, options)`` rows for ``modified=1``."""

    @abstractmethod
    def updt_lstseen(self, dmrid_list: list[tuple[bytes, ...]]) -> None:
        ...

    @abstractmethod
    def reconcile_logged_in(self, connected_peer_ids: list[bytes]) -> Any:
        """logged_in=1 for connected peers, 0 for the rest. Returns Deferred."""


class DynamicTgStore(ABC):
    """Persist per-peer user-activated dynamic TGs (``peer_dynamic_tgs`` table)."""

    @abstractmethod
    def upsert(self, entry: "DynamicTgEntry") -> None:
        ...

    @abstractmethod
    def replace_single_slot(self, entry: "DynamicTgEntry") -> None:
        ...

    @abstractmethod
    def delete_peer_slot(self, int_id: int, system_name: str, slot: int) -> None:
        ...

    @abstractmethod
    def delete_peer(self, int_id: int, system_name: str) -> None:
        ...

    @abstractmethod
    def load_peer(self, int_id: int, system_name: str) -> Any:
        """Returns Deferred firing with ``list[DynamicTgEntry]``."""

    @abstractmethod
    def select_need_reload(self) -> Any:
        """Returns Deferred firing with ``list[tuple[int, str]]`` (int_id, system_name)."""

    @abstractmethod
    def purge_expired(self, now: float) -> None:
        ...


class ProxyIpBlacklist(ABC):
    """Temporary IP blocks (legacy proxy ``ip_black_list`` / PRBL)."""

    @abstractmethod
    def block_until(self, host: str, expire_at: float) -> None:
        ...

    @abstractmethod
    def is_blocked(self, host: str, now: float) -> bool:
        ...


class ProxyMasterSink(Protocol):
    """Inject hotspot datagrams into the target MASTER (in-process)."""

    def inject(self, data: bytes, client_addr: tuple[str, int]) -> None:
        ...


class ProxyClientSender(Protocol):
    """Send datagrams to hotspot clients via LISTEN_PORT."""

    def send_to_client(self, data: bytes, client: "ClientEndpoint") -> None:
        ...


class MasterPeerRegistry(Protocol):
    """Drop MASTER peer state when proxy session ends."""

    def remove_peer(self, peer_id: bytes) -> None:
        ...


class PeerTransport(Protocol):
    """Built-in mesh codec (dmre_v5, obp_v1) — decode datagrams to ``MeshIngress``, encode ``MeshEgress``."""

    @property
    def name(self) -> str:
        ...

    def try_decode(self, datagram: bytes, config: "PeerMeshConfig") -> "MeshIngress | None":
        ...

    def encode(self, egress: "MeshEgress", config: "PeerMeshConfig") -> bytes | None:
        ...


if TYPE_CHECKING:
    from adn_server.domain.mesh_routing import MeshEgress, MeshIngress, PeerMeshConfig
    from adn_server.domain.proxy import ClientEndpoint, ClientSlot
