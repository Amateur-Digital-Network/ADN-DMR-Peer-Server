# ADN Systems documentation

This site covers **ADN DMR Peer Server** and **ADN Monitor** as one operational stack. Content is organized by **product** (`server/` vs `monitor/`) under the active **locale**.

- **English:** [`docs/en/`](README.md) (this tree) — build with `mkdocs.yml` → `site/en/`.
- **Spanish:** same content under `docs/es/` — `mkdocs build -f mkdocs.es.yml` → `site/es/` (see `docs/es/README.md` in the repository).

## ADN DMR Peer Server

The **ADN DMR Peer Server** is a [GPL-3.0](https://www.gnu.org/licenses/gpl-3.0.html) conference bridge for digital mobile radio (DMR). It is structured in **clean architecture** layers (domain, application, infrastructure).

### What the server does

- Terminates **HBP** (HomeBrew Protocol) links to **MASTER** and **PEER** systems (hotspots, repeaters).
- Terminates **OpenBridge** links to other servers over UDP — **DMRE v5** (recommended on ADN) or **DMRD** v1 compatibility mode.
- Runs **bridge routing** (`BRIDGES`): forwards group voice, loop control, ACLs, optional **BCSQ** / **BCKA**.
- Supports **private calls** (`SUB_MAP`), **voice**, **TTS**, **recording**, and **TCP reporting** to the monitor.

### Where to start (server)

| I want to… | Start here |
|------------|------------|
| Run and configure | [Introduction](server/user-guide/introduction.md), [Configuration](server/user-guide/configuration.md) |
| Write a drop-in plugin | [Plugins](server/user-guide/plugins.md), skeleton `plugins/example/` |
| TG 4000, 999x, echo | [Special numbers](server/user-guide/special-numbers.md) |
| Private calls | [Private calls](server/user-guide/private-calls.md) |
| Voice / TTS | [Voice, announcements, and TTS](server/user-guide/voice-and-tts.md) |
| Legacy dashboard + server 2.x | [Report proxy](server/user-guide/report-proxy.md) |
| OpenBridge / DMRE | [OpenBridge](server/protocols/openbridge.md), [DMRE v5](server/protocols/dmre-v5.md), [OBP proxy](server/user-guide/obp-proxy.md) |
| HBP | [HBP](server/protocols/hbp.md) |
| Code layout | [Architecture](server/development/architecture.md), [Behaviour and timers](server/development/behaviour-and-timers.md) |
| Credits, license, lineage | [Credits & license](server/user-guide/attribution.md) |

### Quick start (server)

```bash
pip install -r requirements.txt
cp adn-server.example.yaml adn-server.yaml
# Edit DATABASE (MariaDB) and secrets before production start
python adn-server.py -c adn-server.yaml
```

More: [Introduction](server/user-guide/introduction.md).

---

## ADN Monitor

Dashboard, WebSocket live view, FastAPI API, **MySQL** self-service — see [Monitor overview](monitor/index.md). **Hotspot proxy** is integrated in **adn-server**.

| I want to… | Start here |
|------------|------------|
| `adn-server.yaml` — integrated `PROXY` / `SELF_SERVICE` | [Hotspot proxy (integrated)](server/user-guide/hotspot-proxy.md) |
| `adn-server.yaml` — `OBP_PROXY` OpenBridge fan-in | [OBP proxy](server/user-guide/obp-proxy.md) |
| `adn-monitor.yaml`, layout | [Monitor configuration](monitor/configuration.md) |
| Integrated hotspot proxy | [Hotspot proxy](server/user-guide/hotspot-proxy.md) |
| Standalone hotspot proxy (removed) | [Hotspot proxy — moved](monitor/hotspot-proxy.md) |
| Self-service | [Self-service](monitor/self-service.md) |
| How it connects to the server | [Monitoring and reports](server/user-guide/monitoring.md) |

---

## Locales

- **English** — **`docs/en/`** (this build).
- **Spanish** — **`docs/es/`**.
