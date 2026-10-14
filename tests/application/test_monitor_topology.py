# ADN DMR Peer Server - tests application monitor topology
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

"""Monitor topology parity for inject-only proxy (legacy SYSTEM-N report shape)."""

from __future__ import annotations

import pytest

from adn_server.application.report.monitor_topology import (
    expand_inject_proxy_systems,
    remap_inject_proxy_voice_event,
    remap_inject_proxy_voice_event_for_peer,
    remap_inject_proxy_voice_events,
)
from adn_server.application.report.payloads import build_topology
from adn_server.domain.value_objects import bytes_4


def _peer(*, connected: bool = True, options: bytes | None = None) -> dict:
    row = {
        "CONNECTION": "YES" if connected else "NO",
        "CONNECTED": 1_700_000_000 if connected else 0,
        "IP": "203.0.113.10",
        "PORT": 62031,
        "CALLSIGN": b"CE5RPY  ",
        "RX_FREQ": b"145625000",
        "TX_FREQ": b"145625000",
    }
    if options is not None:
        row["OPTIONS"] = options
    return row


def _proxy_config(
    peers: dict[bytes, dict],
    *,
    max_peers: int = 102,
    base_port: int = 56400,
) -> dict:
    return {
        "PROXY": {"TARGET_SYSTEM": "SYSTEM"},
        "SYSTEMS": {
            "SYSTEM": {
                "MODE": "MASTER",
                "ENABLED": True,
                "MAX_PEERS": max_peers,
                "_REPORT_BASE_PORT": base_port,
                "PEERS": peers,
            }
        },
    }


def test_expand_inject_proxy_fans_peers_into_system_n() -> None:
    peer_a = bytes_4(730039101)
    peer_b = bytes_4(7301896)
    config = _proxy_config({peer_a: _peer(), peer_b: _peer()})
    config["SYSTEMS"]["ECHO"] = {"MODE": "MASTER", "ENABLED": True, "PEERS": {}}
    expanded = expand_inject_proxy_systems(
        config,
        config["SYSTEMS"],
        {peer_a: 2, peer_b: 66},
    )
    assert "SYSTEM" not in expanded
    assert expanded["SYSTEM-2"]["PORT"] == 56402
    assert expanded["SYSTEM-66"]["PORT"] == 56466
    assert list(expanded["SYSTEM-2"]["PEERS"]) == [peer_a]
    assert list(expanded["SYSTEM-66"]["PEERS"]) == [peer_b]
    assert "ECHO" in expanded


def test_build_topology_after_expand_matches_monitor_shape() -> None:
    peer = bytes_4(730039101)
    config = _proxy_config({peer: _peer()})
    expanded = expand_inject_proxy_systems(config, config["SYSTEMS"], {peer: 2})
    doc = build_topology(expanded, seq=1)
    names = {system["name"] for system in doc["systems"]}
    assert "SYSTEM" not in names
    assert "SYSTEM-2" in names
    system = next(item for item in doc["systems"] if item["name"] == "SYSTEM-2")
    assert system["port"] == 56402
    assert len(system["peers"]) == 1
    row = system["peers"][0]
    assert row["id"] == 730039101
    assert row["connected"] is True
    assert row["ip"] == "203.0.113.10"
    assert row["callsign"] == "CE5RPY"


def test_expand_inject_clears_merged_system_static_on_virtual() -> None:
    peer = bytes_4(730039101)
    config = _proxy_config({peer: _peer(options=b"TS2=7305;")})
    config["SYSTEMS"]["SYSTEM"]["TS2_STATIC"] = "730,7305,214091"
    expanded = expand_inject_proxy_systems(config, config["SYSTEMS"], {peer: 2})
    assert expanded["SYSTEM-2"]["TS2_STATIC"] == ""
    assert expanded["SYSTEM-2"]["TS1_STATIC"] == ""


def test_expand_inject_proxy_emits_all_virtual_masters() -> None:
    peer = bytes_4(730039101)
    config = _proxy_config({peer: _peer()}, max_peers=4)
    expanded = expand_inject_proxy_systems(config, config["SYSTEMS"], {peer: 2})
    for slot in range(4):
        assert f"SYSTEM-{slot}" in expanded
        assert expanded[f"SYSTEM-{slot}"]["PORT"] == 56400 + slot
    assert list(expanded["SYSTEM-2"]["PEERS"]) == [peer]
    assert expanded["SYSTEM-0"]["PEERS"] == {}
    assert expanded["SYSTEM-1"]["PEERS"] == {}


@pytest.mark.parametrize(
    "raw,peer_specs,slot_map,expect",
    [
        pytest.param(
            "GROUP VOICE,START,RX,SYSTEM,3262598598,730039101,730039101,2,730444",
            [(730039101,)],
            {730039101: 4},
            {"startswith": "GROUP VOICE,START,RX,SYSTEM-4,"},
            id="rx_to_virtual_master",
        ),
        pytest.param(
            "GROUP VOICE,START,TX,SYSTEM,4100887026,9990,730039101,2,730444",
            [(730039101,)],
            {730039101: 4},
            {"parts": {3: "SYSTEM-4", 5: "9990"}},
            id="tx_echo_keeps_9990_for_hotspot_rx",
        ),
        pytest.param(
            "GROUP VOICE,START,TX,SYSTEM,4100887026,730039101,730039101,2,9990",
            [(730039101,)],
            {730039101: 4},
            {"parts": {3: "SYSTEM-4", 5: "9990"}},
            id="tx_echo_peer_id_in_field5_dst_9990",
        ),
        pytest.param(
            "GROUP VOICE,START,RX,SYSTEM,4100887026,73003,7300392,2,9990",
            [(730039101,)],
            {730039101: 4},
            {"parts": {3: "SYSTEM-4", 5: "730039101"}},
            id="rx_normalizes_field5_to_hotspot_radio_id",
        ),
        pytest.param(
            "GROUP VOICE,START,TX,SYSTEM,2693411696,9990,7300392,2,9990",
            [(730039101,)],
            {730039101: 4},
            {"parts": {3: "SYSTEM-4", 5: "9990"}},
            id="single_hotspot_user_prefix",
        ),
        pytest.param(
            "GROUP VOICE,START,TX,SYSTEM,2693411696,9990,7300391,2,9990",
            [(730039101,), (730039102,)],
            {730039101: 4, 730039102: 5},
            {"unchanged": True},
            id="ambiguous_user_multiple_hotspots",
        ),
        pytest.param(
            "GROUP VOICE,START,TX,SYSTEM,4100887026,9990,730039102,2,730444",
            [(730039101,), (730039102,)],
            {730039101: 4, 730039102: 5},
            {"parts": {3: "SYSTEM-5", 5: "9990"}},
            id="full_radio_id_with_sibling_hotspots",
        ),
    ],
)
def test_remap_voice_event_inject_proxy(
    raw: str,
    peer_specs: list[tuple[int, ...]],
    slot_map: dict[int, int],
    expect: dict,
) -> None:
    peers = {bytes_4(rid): _peer() for spec in peer_specs for rid in spec}
    peer_slots = {bytes_4(rid): slot_map[rid] for spec in peer_specs for rid in spec}
    config = _proxy_config(peers)
    remapped = remap_inject_proxy_voice_event(
        raw, config, config["SYSTEMS"], peer_slots
    )
    if expect.get("unchanged"):
        assert remapped == raw
        return
    if prefix := expect.get("startswith"):
        assert remapped.startswith(prefix)
        return
    parts = remapped.split(",")
    for idx, value in expect.get("parts", {}).items():
        assert parts[idx] == value


def test_local_hotspot_rx_fans_out_tx_only_to_peers_with_matching_tg() -> None:
    """REPEAT companion TX only for hotspots that have the TG in OPTIONS."""
    peers = {
        bytes_4(7301795): _peer(options=b"TS2=730444;"),
        bytes_4(7300444): _peer(options=b"TS2=730444;"),
        bytes_4(730039101): _peer(options=b"TS2=91;"),
    }
    config = _proxy_config(peers)
    peer_slots = {
        bytes_4(7301795): 1,
        bytes_4(7300444): 3,
        bytes_4(730039101): 4,
    }
    raw = "GROUP VOICE,START,RX,SYSTEM,3262598598,7301795,7301795,2,730444"
    events = remap_inject_proxy_voice_events(
        raw, config, config["SYSTEMS"], peer_slots
    )
    assert len(events) == 2
    by_system = {ev.split(",")[3]: ev for ev in events}
    assert "SYSTEM-1" in by_system
    assert by_system["SYSTEM-1"].startswith("GROUP VOICE,START,RX,SYSTEM-1,")
    assert "SYSTEM-3" in by_system
    assert by_system["SYSTEM-3"].startswith("GROUP VOICE,START,TX,SYSTEM-3,")
    assert "SYSTEM-4" not in by_system


def test_remap_inject_proxy_skips_announcement_even_if_rf_src_matches_peer() -> None:
    """Announcement rf_src (field 6) coincidentally matching a real connected peer's

    login id must not be attributed/remapped to that peer's SYSTEM-N row — the
    trailing ``is_announcement=1`` field short-circuits the fuzzy peer match in
    ``_peer_key_from_voice_csv``.
    """
    real_peer = bytes_4(1000001)
    peers = {real_peer: _peer()}
    config = _proxy_config(peers)
    peer_slots = {real_peer: 1}
    raw = "GROUP VOICE,START,RX,SYSTEM,3262598598,9990001,1000001,2,91,1"
    events = remap_inject_proxy_voice_events(raw, config, config["SYSTEMS"], peer_slots)
    assert events == [raw]


def test_obp_tx_single_hotspot_remaps_dynamic_tg_not_in_static() -> None:
    """One HS online: downlink/monitor must remap TX even when TG is UA-only (not in OPTIONS)."""
    peer = bytes_4(730039101)
    peers = {peer: _peer(options=b"TS2=730,7305;")}
    config = _proxy_config(peers)
    peer_slots = {peer: 2}
    raw = "GROUP VOICE,START,TX,SYSTEM,4100887026,73010,7000002,2,730444"
    bridges = {
        "730444": [
            {
                "SYSTEM": "SYSTEM",
                "TS": 2,
                "TGID": 730444,
                "ACTIVE": True,
                "TO_TYPE": "ON",
            }
        ],
    }
    events = remap_inject_proxy_voice_events(
        raw, config, config["SYSTEMS"], peer_slots, bridges
    )
    assert len(events) == 1
    assert events[0].startswith("GROUP VOICE,START,TX,SYSTEM-2,")


def test_obp_bridge_tx_fans_out_only_to_peers_with_matching_static_tg() -> None:
    """OBP downlink TX must not light hotspots without the TG in OPTIONS."""
    peers = {
        bytes_4(7300444): _peer(options=b"TS2=730444;"),
        bytes_4(7301795): _peer(options=b"TS2=730444;"),
        bytes_4(730039101): _peer(options=b"TS2=91;"),
    }
    config = _proxy_config(peers)
    peer_slots = {bytes_4(7300444): 3, bytes_4(7301795): 1, bytes_4(730039101): 4}
    raw = "GROUP VOICE,START,TX,SYSTEM,4100887026,73010,7000002,2,730444"
    bridges = {
        "730444": [
            {
                "SYSTEM": "SYSTEM",
                "TS": 2,
                "ACTIVE": True,
                "TO_TYPE": "ON",
            }
        ],
    }
    events = remap_inject_proxy_voice_events(
        raw, config, config["SYSTEMS"], peer_slots, bridges
    )
    systems = {ev.split(",")[3] for ev in events}
    assert systems == {"SYSTEM-1", "SYSTEM-3"}
    assert all(ev.split(",")[5] == "73010" for ev in events)


def test_remap_voice_event_passes_through_non_proxy_systems() -> None:
    raw = "GROUP VOICE,START,RX,ECHO,1,9990,730039101,2,9990"
    assert remap_inject_proxy_voice_event(raw, {}, {}) == raw


def test_private_voice_tx_remaps_to_dest_peer_id_not_tg_subscription() -> None:
    """Regression: a PRIVATE VOICE TX event was silently dropped -- the generic TX
    path treats field 8 as a talkgroup and fans out via TG-subscription eligibility
    (_peers_receiving_tgid), which a subscriber-id destination never matches. The
    trailing dest_peer_id field (routing_use_cases._pvt_call_received) must route it
    directly to that one hotspot's SYSTEM-N row instead."""
    peers = {
        bytes_4(730039110): _peer(),
        bytes_4(730039101): _peer(),
    }
    peer_slots = {bytes_4(730039110): 1, bytes_4(730039101): 8}
    config = _proxy_config(peers)
    raw = "PRIVATE VOICE,START,TX,SYSTEM,1610544978,730039110,7300391,2,7300392,730039101"
    events = remap_inject_proxy_voice_events(raw, config, config["SYSTEMS"], peer_slots)
    assert len(events) == 1
    parts = events[0].split(",")
    assert parts[3] == "SYSTEM-8"
    assert parts[2] == "TX"
    assert parts[-1] == "730039101"


def test_private_voice_tx_dropped_when_dest_peer_not_connected() -> None:
    peers = {bytes_4(730039110): _peer()}
    peer_slots = {bytes_4(730039110): 1}
    config = _proxy_config(peers)
    raw = "PRIVATE VOICE,START,TX,SYSTEM,1610544978,730039110,7300391,2,7300392,730039101"
    events = remap_inject_proxy_voice_events(raw, config, config["SYSTEMS"], peer_slots)
    assert events == []


def test_private_voice_tx_without_dest_peer_id_falls_back_to_legacy_tg_fanout() -> None:
    """Old-server-format event (no trailing peer id) -- must not crash, and since a
    subscriber id is never a static TG, no companion TX is fabricated."""
    peers = {
        bytes_4(730039110): _peer(),
        bytes_4(730039101): _peer(),
    }
    peer_slots = {bytes_4(730039110): 1, bytes_4(730039101): 8}
    config = _proxy_config(peers)
    raw = "PRIVATE VOICE,START,TX,SYSTEM,1610544978,730039110,7300391,2,7300392"
    events = remap_inject_proxy_voice_events(raw, config, config["SYSTEMS"], peer_slots)
    assert events == []


def test_private_voice_end_tx_remaps_to_dest_peer_id_with_duration_field() -> None:
    peers = {
        bytes_4(730039110): _peer(),
        bytes_4(730039101): _peer(),
    }
    peer_slots = {bytes_4(730039110): 1, bytes_4(730039101): 8}
    config = _proxy_config(peers)
    raw = "PRIVATE VOICE,END,TX,SYSTEM,1610544978,730039110,7300391,2,7300392,5.87,730039101"
    events = remap_inject_proxy_voice_events(raw, config, config["SYSTEMS"], peer_slots)
    assert len(events) == 1
    parts = events[0].split(",")
    assert parts[3] == "SYSTEM-8"
    assert parts[-1] == "730039101"


def test_hangtime_blocks_monitor_tx_fanout_to_blocked_peer() -> None:
    """Companion TX / OBP fan-out must not light peers blocked by GROUP_HANGTIME."""
    import time

    from adn_server.application.routing.downlink import (
        DownlinkContext,
        end_peer_voice_slot,
        touch_peer_voice_slot,
    )
    from adn_server.domain import HBPF_SLT_VTERM, bytes_3, bytes_4

    def _empty_slot() -> dict:
        return {
            "RX_TIME": 0.0,
            "TX_TIME": 0.0,
            "RX_TYPE": HBPF_SLT_VTERM,
            "TX_TYPE": HBPF_SLT_VTERM,
            "RX_TGID": b"\x00\x00\x00",
            "TX_TGID": b"\x00\x00\x00",
            "RX_STREAM_ID": b"",
            "TX_STREAM_ID": b"",
            "RX_PEER": b"\x00\x00\x00\x00",
            "TX_PEER": b"\x00\x00\x00\x00",
        }

    peer_hs_a = bytes_4(730039101)
    peer_hs_b = bytes_4(730039210)
    peers = {
        peer_hs_a: _peer(options=b"TS2=730,7305;SINGLE=0;"),
        peer_hs_b: _peer(options=b"TS2=7305;"),
    }
    config = _proxy_config(peers)
    config["SYSTEMS"]["SYSTEM"]["GROUP_HANGTIME"] = 10
    peer_slots = {peer_hs_a: 7, peer_hs_b: 8}
    sys_cfg = config["SYSTEMS"]["SYSTEM"]
    ctx = DownlinkContext(
        config=config,
        system_name="SYSTEM",
        sys_cfg=sys_cfg,
        peers=peers,
        status={1: _empty_slot(), 2: _empty_slot()},
        connected_count=2,
    )
    now = time.time()
    touch_peer_voice_slot(
        ctx, peer_hs_a, 2, bytes_4(0x1111), bytes_3(7306), pkt_time=now,
    )
    end_peer_voice_slot(
        ctx, peer_hs_a, 2, bytes_4(0x1111), bytes_3(7306), pkt_time=now + 0.5,
    )

    rx_raw = "GROUP VOICE,START,RX,SYSTEM,3262598598,730039210,730039210,2,7305"
    rx_events = remap_inject_proxy_voice_events(
        rx_raw, config, config["SYSTEMS"], peer_slots, downlink_ctx=ctx,
    )
    rx_systems = {ev.split(",")[3] for ev in rx_events}
    assert "SYSTEM-8" in rx_systems
    assert "SYSTEM-7" not in rx_systems

    tx_raw = "GROUP VOICE,START,TX,SYSTEM,4100887026,73010,7000002,2,7305"
    tx_events = remap_inject_proxy_voice_events(
        tx_raw, config, config["SYSTEMS"], peer_slots, downlink_ctx=ctx,
    )
    tx_systems = {ev.split(",")[3] for ev in tx_events}
    assert tx_systems == {"SYSTEM-8"}


def test_remap_voice_event_for_single_peer() -> None:
    peer = bytes_4(730039101)
    peers = {peer: _peer(options=b"TS2=7305;")}
    config = _proxy_config(peers)
    peer_slots = {peer: 7}
    raw = "GROUP VOICE,START,TX,SYSTEM,4100887026,73010,7000002,2,7305"
    mapped = remap_inject_proxy_voice_event_for_peer(
        raw, config, config["SYSTEMS"], peer, peer_slots,
    )
    assert mapped is not None
    assert mapped.startswith("GROUP VOICE,START,TX,SYSTEM-7,")


def test_remap_announcement_tx_fans_out_to_ua_only_peer() -> None:
    """Inject START,TX on SYSTEM reaches peers with dynamic UA but no static OPTIONS TG."""
    peer = bytes_4(730039210)
    peers = {peer: _peer(options=b"TS2=730500;SINGLE=0;")}
    config = _proxy_config(peers)
    sys_cfg = config["SYSTEMS"]["SYSTEM"]
    pk = bytes_4(730039210)
    sys_cfg.setdefault("_PEER_UA_MULTI_TGS", {}).setdefault(pk, {})[2] = {730600}
    peer_slots = {peer: 7}
    raw = "GROUP VOICE,START,TX,SYSTEM,4100887026,1000001,1000001,2,730600"
    events = remap_inject_proxy_voice_events(
        raw, config, config["SYSTEMS"], peer_slots,
    )
    systems = {ev.split(",")[3] for ev in events}
    assert systems == {"SYSTEM-7"}


def test_non_proxy_systems_pass_through_unchanged() -> None:
    systems = {
        "ECHO": {"MODE": "MASTER", "ENABLED": True, "PEERS": {}},
    }
    assert expand_inject_proxy_systems({"PROXY": {"TARGET_SYSTEM": "SYSTEM"}}, systems) is systems


def test_echo_tx_uses_rx_peer_when_rf_src_fuzzy_matches_sibling() -> None:
    """Echo TX must resolve to RX_PEER from STATUS, not fuzzy-match rf_src.

    Reproduces the monitor display bug: user 7140023 has two hotspots,
    714000103 (base 7140001) and 714002301 (base 7140023). The echo playback
    arrives with peer_id=9990, rf_src=7140023. Fuzzy matching (// 100) resolves
    714002301 instead of the true originator 714000103 held in STATUS.RX_PEER.
    """
    from adn_server.application.routing.downlink import DownlinkContext

    peer_origin = bytes_4(714000103)  # hotspot that transmitted to 9990
    peer_sibling = bytes_4(714002301)  # same user, base == rf_src
    peers = {peer_origin: _peer(), peer_sibling: _peer()}
    config = _proxy_config(peers)
    peer_slots = {peer_origin: 1, peer_sibling: 5}
    status = {
        2: {
            "RX_PEER": peer_origin,
            "RX_TGID": bytes_4(9990)[1:],
        }
    }
    ctx = DownlinkContext(
        config=config,
        system_name="SYSTEM",
        sys_cfg=config["SYSTEMS"]["SYSTEM"],
        peers=peers,
        status=status,
        connected_count=2,
    )
    # Echo playback TX: peer_id=9990, rf_src=7140023 (user DMR ID), dst=9990 slot 2
    raw = "GROUP VOICE,START,TX,SYSTEM,2693411696,9990,7140023,2,9990"
    events = remap_inject_proxy_voice_events(
        raw, config, config["SYSTEMS"], peer_slots, downlink_ctx=ctx,
    )
    assert len(events) == 1
    parts = events[0].split(",")
    # Must remap to SYSTEM-1 (peer_origin slot), NOT SYSTEM-5 (peer_sibling)
    assert parts[3] == "SYSTEM-1"
    assert parts[5] == "9990"  # echo id preserved for TX chip display


def test_echo_tx_falls_back_without_downlink_ctx() -> None:
    """Without STATUS (no downlink_ctx), echo TX cannot resolve when rf_src
    does not fuzzy-match any connected peer — event passes through unchanged."""
    peer = bytes_4(714000103)
    peers = {peer: _peer()}
    config = _proxy_config(peers)
    peer_slots = {peer: 1}
    raw = "GROUP VOICE,START,TX,SYSTEM,2693411696,9990,7140023,2,9990"
    events = remap_inject_proxy_voice_events(
        raw, config, config["SYSTEMS"], peer_slots,
    )
    # rf_src 7140023 does not match peer 714000103 via // 100 or prefix;
    # legacy leaves the event unchanged when the hotspot cannot be resolved.
    assert len(events) == 1
    assert events[0] == raw


def test_expand_inject_proxy_never_deep_copies_the_peers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every report copied every peer once per virtual master (MAX_PEERS of them) on the
    reactor: ~0.2 s with 26 hotspots and MAX_PEERS 200, a gap hotspots hear as loss."""
    import copy

    peers = {bytes_4(730039100 + i): _peer(options=b"TS2=730;") for i in range(26)}
    config = _proxy_config(peers, max_peers=200)

    def refuse(*_a, **_k):
        raise AssertionError("deepcopy on the report path")

    monkeypatch.setattr(copy, "deepcopy", refuse)
    out = expand_inject_proxy_systems(config, config["SYSTEMS"])
    assert sum(len(out[f"SYSTEM-{n}"]["PEERS"]) for n in range(200)) == 26


def test_expand_inject_proxy_leaves_the_live_system_untouched() -> None:
    peers = {bytes_4(730039101): _peer(options=b"TS2=730;")}
    config = _proxy_config(peers, max_peers=3)
    sys_cfg = config["SYSTEMS"]["SYSTEM"]
    sys_cfg.update(TS1_STATIC="214", TS2_STATIC="730", PORT=62031)
    out = expand_inject_proxy_systems(config, config["SYSTEMS"])
    out["SYSTEM-0"]["PEERS"].clear()
    assert sys_cfg["PEERS"] is peers and len(peers) == 1
    assert (sys_cfg["TS1_STATIC"], sys_cfg["TS2_STATIC"], sys_cfg["PORT"]) == ("214", "730", 62031)
    assert out["SYSTEM-1"]["PORT"] != out["SYSTEM-0"]["PORT"]
