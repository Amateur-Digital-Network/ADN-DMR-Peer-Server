# Test layout

One **topic per file** — run only what you need while developing or validating a change.

```bash
# Install into the pyenv site-packages (not ~/.local); Cursor/VS Code pytest sets PYTHONNOUSERSITE=1
python3 -m pip install --no-user -e ".[dev]"
python3 -m pytest tests/<path>/test_<name>.py -q          # single file
python3 -m pytest tests/<path>/test_<name>.py::test_foo -q # single test
python3 -m pytest tests/routing/ -q                        # whole domain
python3 -m pytest tests/ -q                                # full suite
python3 -m pytest tests/ -q -m "not mqtt"                 # skip MQTT-heavy tests
```

Use the project interpreter, e.g. `/opt/.pyenv/versions/3.11.8/bin/python3`.

## Directories

| Directory | What it covers |
|-----------|----------------|
| `routing/` | Static TG, startup subscriptions, unit data, CRC dedup, echo reset, private voice, config reload |
| `hbp/` | HBP ingress, loop control, rate limit, timeout/collision, master maintenance |
| `obp/` | OpenBridge loop, rate limit, unit-data loop |
| `voice/` | Announcements, TTS schedule, broadcast queue, disconnected voice, in-band signalling |
| `talker_alias/` | Encode/decode, passthrough, MMDVM wire, routing inject (DeterministicScenario) |
| `echo/` | Recording timers, playback loop, seq preservation, ingress path |
| `replay/` | JSONL session replay |
| `schemas/` | Report v2 JSON Schema validation (`jsonschema` dev dep) |
| `application/` | Report payloads, monitor topology, proxy use cases, subscription store/router |
| `fakes/` | Re-exports for application tests (`InMemorySubscriptionStore` shim) — not run as tests |
| `infrastructure/` | Logging reload, ACL router, **HBP REPEAT + proxy fan-in integration**, MQTT |
| `smoke/` | Quick routing smoke |
| `trunking/` | Tier III: wire format, config, registry, legs, DMRT signalling, site destinations; real-stack site login, uplink, downlink, site to site, echo, private calls |
| `support/` | Shared stacks (`hbp_repeat_stack`, `trunk_site_stack`, `dmrtc_wire`, monitor sim) — not run as tests |
| `harness/` | Shared fakes (`DeterministicScenario`, assertions) — not run as tests |
| `benchmarks/` | `bench_dmrd.py`: µs and function calls per voice frame through `dmrd_received`, usage in its docstring — not run as tests |

## Integration vs harness

Most routing/voice tests inject packets via **`DeterministicScenario`** (`routing.dmrd_received` on fakes). That is fast but **skips** `udp_hbp` REPEAT rewrite and proxy UDP fan-in.

For regressions on those paths, use:

| File | Topic |
|------|-------|
| `infrastructure/test_hbp_repeat_talker_alias.py` | Real `HBPProtocol` REPEAT + embedded TA |
| `infrastructure/test_proxy_repeat_e2e.py` | Proxy fan-in → REPEAT downlink |
| `infrastructure/test_proxy_reload.py` | Hot reload keeps UDP listener |

Mark new stack tests with `@pytest.mark.integration`.

## Files by domain

### routing/

| File | Tests | Topic |
|------|-------|-------|
| `test_config_reload.py` | 1 | Merge system config on reload |
| `test_crc_dedup.py` | 3 | HBP/OBP CRC dedup, seq=0 |
| `test_echo_subscription_reset.py` | 5 | Echo leg after subscription reset / OPTIONS |
| `test_options_config_loop.py` | 3 | OPTIONS paths (RPTO/startup; no 26s loop) |
| `test_peer_options_override.py` | — | RPTO SINGLE/TIMER override, inject proxy |
| `test_private_voice.py` | 3 | Private call routing |
| `test_startup_subscriptions.py` | 4 | Startup subscriptions + voice E2E |
| `test_static_tg_options.py` | 4 | Static TG from peer OPTIONS |
| `test_subscription_router_dmrd.py` | — | `dmrd_received` via `SubscriptionRouter` |
| `test_unit_data_ingress.py` | 4 | Unit headers, CSBK, reports |
| `test_unit_data_routing.py` | 5 | SUB_MAP, hotspot, gateway, OBP fanout |
| `test_hbp_source_without_protocol.py` | — | Group call from an HBP system with no protocol (no packet control) |

### hbp/

| File | Tests | Topic |
|------|-------|-------|
| `test_hbp_loop_control.py` | 2 | HBP loop winner/loser |
| `test_hbp_rate_limit.py` | 2 | Ingress rate drop + stall |
| `test_ingress.py` | 3 | RX start, rate drop, OBP loop loser |
| `test_master_maintenance.py` | 2 | Peer timeout / shared PEERS dict |
| `test_timeout_collision.py` | 3 | 180s timeout, collision, rekey |
| `test_admit_dmrd.py` | — | DMRD admission shared by MASTER and PEER: flags byte, null stream, ACL, SUB_MAP |
| `test_slot_rx_record.py` | — | Slot row of an accepted frame and a new call's LC (MASTER, PEER) |
| `test_logged_in_peers.py` | — | `logged_in_addr`, `logged_in_peer_at`, `drop_peer` |
| `test_master_replies.py` | — | Every MASTER reply through `REPLY_ACK`/`NAK`/`PONG`/`CLOSE` |

### obp/

| File | Tests | Topic |
|------|-------|-------|
| `test_loop_control.py` | 4 | OBP loop, VTERM, BCSQ |
| `test_obp_rate_limit.py` | 1 | Rate limit epoch |
| `test_unit_data_loop.py` | 2 | Unit data loop loser |

### voice/

| File | Tests | Topic |
|------|-------|-------|
| `test_disconnected_voice.py` | 3 | Not-linked / reflector prompts |
| `test_in_band_signalling.py` | 5 | Reflector / single-mode VTERM |
| `test_play_file_on_request.py` | 3 | On-demand file playback |
| `test_prompt_holds_slot.py` | 4 | Server prompts hold TS2 while they play |

Scheduled announcements, TTS and beacons are tested with their plugin: `plugins/voice-announcements/tests/`.

### talker_alias/

| File | Tests | Topic |
|------|-------|-------|
| `test_routing_inject.py` | 3 | TA inject on routing VHEAD (harness) |
| `test_embed_ta.py` | 4 | Embedded LC modes |
| `test_encode_decode.py` | 8 | Domain encode/decode |
| `test_format.py` | 2 | Format from subscriber profile |
| `test_mmdvm_wire.py` | 10 | MMDVM wire blocks |
| `test_passthrough.py` | 9 | Passthrough / both modes |

### echo/

| File | Tests | Topic |
|------|-------|-------|
| `test_playback_ingress.py` | 2 | `ingress_pkt_time`, record→playback |
| `test_playback_logging.py` | 1 | Duration log format |
| `test_playback_send_loop.py` | 4 | Send loop, max duration, interval |
| `test_recording_timers.py` | 2 | Idle timeout, VTERM commit |
| `test_rekey_playback.py` | 4 | Seq preservation past 255 / 30s |

### trunking/

Real-stack files (`test_site_*`) use `support/trunk_site_stack.py`: real `TrunkSiteProtocol` and hotspot `HBPProtocol` over one `RoutingUseCases`, with sites driven by `support/dmrtc_wire.py` (DMRGateway login state machine, dmrtc DMRT layouts).

| File | Topic |
|------|-------|
| `test_trunk_wire.py` | DMRT parse/build at exact lengths, 71-byte DMRD, call UUID, subscribable TGs |
| `test_trunk_config.py` | `TRUNKING` validator rules, `trunk_systems` |
| `test_trunk_registry.py` | Registration cap, TTL, roaming, holders, UUID and echo windows, restore |
| `test_trunk_legs.py` | STATIC legs on both slots, release, re-arm after a bridge reset, no store scan |
| `test_trunk_signalling.py` | DMRT 0x01–0x04: ACLs, caps, SUB_MAP pin, roaming 0x42 |
| `test_recent_keys.py` | `RecentKeys`: once per window, expiry from the oldest end, cap |
| `test_trunk_sites.py` | Site destinations, call UUIDs, tick, persistence |
| `test_site_login.py` | DTC* login, DTCACK/DTCNAK/DTCPONG/DTCCL, refusals, truncated frames |
| `test_site_uplink.py` | 71-byte DMRD only, per-stream rows, TG 4000, flat mirror, row trim |
| `test_site_downlink.py` | Concurrent calls to a site, two-slot legs sent once, no DMRA |
| `test_site_to_site.py` | Same-system delivery, REPEAT, cross-system bridges |
| `test_site_echo.py` | Per-site parrot playback |
| `test_site_private.py` | Private calls and unit data to registered radios |
| `test_site_hotspots_unchanged.py` | Hotspot writes byte-identical with a trunk system present |

Related, outside `trunking/`: `routing/test_per_stream_rows.py` (per-stream receive rows and concurrent targets), `infrastructure/test_trunk_reload.py` (`TRUNKING` flip on reload), `infrastructure/test_trunk_registration_store.py` (JSON store, writes in a worker thread), `infrastructure/test_trunk_bootstrap.py` (Tier III built only for a `TRUNKING` system, lazy imports, reload wiring, loops, shutdown write).

### infrastructure (integration highlights)

| File | Topic |
|------|-------|
| `test_hbp_repeat_talker_alias.py` | REPEAT embed TA through real HBP |
| `test_proxy_repeat_e2e.py` | Proxy → REPEAT E2E |
| `test_proxy_reload.py` | Proxy hot reload |
| `test_udp_fanin.py` | UDP fan-in routing |
| `test_report_server_wire.py` | Report server wire opcodes |
| `test_logging_reload.py` | Log level reload |
| `test_acl_router.py` | ACL range checks (`acl_check` parity) |

### smoke/ · application/

| File | Topic |
|------|-------|
| `smoke/test_routing.py` | Static TG forward smoke |
| `application/test_monitor_topology.py` | Inject-only proxy monitor remap |
| `application/test_runtime_context.py` | RuntimeContext holder, SIGHUP swap prep |
| `application/test_subscription_router.py` | `SubscriptionRouter` vs legacy scan |
| `application/test_routing_table_export.py` | Monitor export shim from store |

## Harness API (post-rename)

| Symbol | Role |
|--------|------|
| `DeterministicScenario` | Wires `RoutingUseCases` + `InMemorySubscriptionStore` + `InMemoryAclRouter` |
| `scenario.routing` | Use-case facade (`dmrd_received`, timers, OPTIONS) |
| `scenario.seed_routing_table()` | Seed store from legacy monitor-shaped dict |
| `active_routing_table()` | Build minimal ACTIVE routing table for harness |
| `patch_routing_wall_time()` | Patches wall clock on OBP/unit paths |

Session replay JSONL meta accepts `routing_table` (preferred) or legacy `bridges`; `apply_startup_subscriptions` or legacy `apply_startup_bridges`.

## Examples (copy-paste)

```bash
# After changing unit-data routing
python3 -m pytest tests/routing/test_unit_data_routing.py -q

# After echo seq fix
python3 -m pytest tests/echo/test_rekey_playback.py -q

# Talker Alias REPEAT (real stack)
python3 -m pytest tests/infrastructure/test_hbp_repeat_talker_alias.py -q

# HBP loop + rate (common RF regressions)
python3 -m pytest tests/hbp/test_hbp_loop_control.py tests/hbp/test_hbp_rate_limit.py -q

# One test by name
python3 -m pytest tests/routing/test_startup_subscriptions.py::test_startup_bridge_routes_voice_after_apply -q
```

## Policy

- **New tests:** add a new file (or extend the smallest existing file for the same topic). Avoid large multi-topic modules.
- **Harness:** shared code lives in `harness/` and `support/` only.
- **Stack regressions:** prefer `infrastructure/test_*_e2e.py` with real adapters over duplicating in `DeterministicScenario`.
- Full audit: `docs-priv/en/test-audit.md` (maintainer checkout).
