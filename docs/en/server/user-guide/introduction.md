# Introduction

## Purpose

This service is a **DMR peer and bridge**. It implements:

- **HBP** over UDP to **MASTER** and **PEER** systems (DMRD frames, authentication, pings).
- **OpenBridge** over UDP to other networks — **DMRE v5** (embedded version 5, BLAKE2b, hops) is the **recommended** inter-server mode on ADN; **DMRD** v1 remains available for compatibility (see [OpenBridge](../protocols/openbridge.md#dmre-and-openbridge-v5)).
- **DMR Tier III trunking** on a MASTER with `TRUNKING: true`: dmrtc sites behind DMRGateway `TrunkingEnabled=1` (Homebrew DMR trunking protocol v0.6) subscribe their talkgroups, register their radios and carry many calls at once (see [DMR Tier III trunking](tier3-trunking.md)).

Configuration is **YAML** (`adn-server.yaml`), merged at runtime with optional voice settings (`adn-voice.yaml`). The shipped template is `adn-server.example.yaml`.

## Design

Routing, timers, OpenBridge loop control, and protocol handling are implemented in **application** and **infrastructure** modules behind stable **ports**; the **domain** layer holds types and rules without I/O. This keeps the system easier to reason about and extend.

## Major subsystems

| Subsystem | Role |
|-----------|------|
| **Bridge router** | `BRIDGES` table: which systems forward which TG on which slot; dynamic bridges; static/stat bridges; **MariaDB dynamic TG restore** on reconnect. |
| **HBP protocol** | Authentication, DMRD ingress/egress, repeat to peers, TG filters, **per-peer UA session** tracking. |
| **OpenBridge** | DMRE ingress, hop limit, loop control (`min(1ST)`), BCSQ/BCKA when enabled. |
| **Voice** | AMBE files, scheduled announcements, TTS pipeline, on-demand playback (TG 9991–9999). |
| **Reporting** | TCP netstring channel to **adn-monitor** (and compatible dashboards): config, bridge state, call events (report v2 JSON). |
| **Hotspot proxy** | Optional integrated UDP fan-in (`PROXY` in `adn-server.yaml`) plus MySQL **self-service** (`SELF_SERVICE`) for dashboard-driven hotspot options. |
| **OBP proxy** | Optional integrated OpenBridge UDP fan-in (`OBP_PROXY` in `adn-server.yaml`; default when OPENBRIDGE systems exist). |

## Related programs

- **Echo / playback** — `adn-server.py --echo` with minimal `adn-echo.yaml`; see [Echo](echo.md).
- **Integrated hotspot proxy** — `PROXY` in **`adn-server.yaml`**; see [Hotspot proxy](hotspot-proxy.md).
- **Integrated OBP proxy** — `OBP_PROXY` fan-in for OpenBridge; see [OBP proxy](obp-proxy.md).
- **Report proxy (legacy dashboards)** — optional **[ADN-report-proxy](https://github.com/ce5rpy/ADN-report-proxy)** so **adn-server 2.x** can feed old HBMonitor / FDMR-style monitors (v1 wire); see [Report proxy](report-proxy.md). Not used with **adn-monitor 2.x**.

## Next steps

- [Configuration](configuration.md) — files, `GLOBAL`, **MASTER** / **PEER** / **OPENBRIDGE**, ACLs, **`DATABASE`**, reports, **`PROXY`**, **`SELF_SERVICE`**, aliases, voice merge.
- [Bridges and talkgroups](bridges-and-talkgroups.md) — how `BRIDGES` works.
- [Voice routing and contention](../development/routing-and-contention.md) — the full packet flow, contention rules, SINGLE, slot mapping, and divergences.
- [Special numbers](special-numbers.md) — TG 4000, information services, echo.
- [Hotspot proxy](hotspot-proxy.md) — integrated **`PROXY`** / **`SELF_SERVICE`** in `adn-server.yaml`.
- [OBP proxy](obp-proxy.md) — integrated **`OBP_PROXY`** OpenBridge fan-in.
- [ADN Monitor](../../monitor/index.md) — dashboard, `adn-monitor.yaml`, self-service UI (separate repo, deployed with the server).
- [Performance (2.x)](../development/performance.md) — CPU/RAM improvements in this release and what causes them.
- [Credits & license](attribution.md) — ADN → FreeDMR → hblink3, license.
