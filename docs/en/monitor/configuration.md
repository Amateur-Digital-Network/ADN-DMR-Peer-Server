# Configuration (`adn-monitor.yaml`)

This document describes **`adn-monitor.yaml`**, used by **`monitor/monitor.py`** (FastAPI: REST, WebSocket, report ingest). Default path is usually **`monitor/adn-monitor.yaml`** (override with **`ADN_CONFIG_PATH`**).

**Hotspot proxy** is configured in **`adn-server.yaml`** (`PROXY` + `SELF_SERVICE`) — see [Hotspot proxy (integrated)](../server/user-guide/hotspot-proxy.md). **`SELF_SERVICE`** (MySQL / PBKDF2) must stay **identical** between **`adn-server.yaml`** and **`adn-monitor.yaml`**.

The example shipped in the **adn-monitor** repo (`monitor/adn-monitor.yaml.example`) is the template for the monitor; keys below match that file and `monitor/src/adn_monitor/infrastructure/config_loader.py` (internal names may differ).

---

## `GLOBAL`

| Key | Meaning |
|-----|---------|
| **BRIDGES_INC** | Show bridge status in the dashboard when `true`. |
| **HOMEBREW_INC** | Include Homebrew (HBP) peer/master status. |
| **LASTHEARD_INC** | Enable Last Heard features / tables. |
| **LASTHEARD_ROWS** | Row count for Last Heard widgets. |
| **EMPTY_MASTERS** | Whether to show masters with no peers. |
| **TGCOUNT_INC** | Enable TG count page / stats. |
| **TGCOUNT_ROWS** | Rows for TG count display. |
| **TIMEZONE** | IANA timezone name (e.g. `America/Santiago`) for display; empty uses server local time. |

---

## `ADN_CONNECTION` {#adn_connection}

Must match the **ADN DMR Peer Server** reporting configuration.

| Key | Meaning |
|-----|---------|
| **ADN_IP** | Host/IP where the **peer server’s report TCP listener** is bound (from the monitor’s network view). |
| **ADN_PORT** | TCP port — must equal **`REPORTS.REPORT_PORT`** on the server and be reachable. |
| **HELLO_TIMEOUT_MS** | After TCP connect, how long to wait for opcode **`0xFF` HELLO** (JSON) from **ADN DMR Server**. If nothing arrives in time, the monitor treats the peer as **legacy** (pickled CONFIG/BRIDGE only). Default **1500** ms. See [Monitoring and reports](../server/user-guide/monitoring.md). |

---

## `SELF_SERVICE`

MySQL credentials and PBKDF2 parameters for **login**, **self-service**, and **`Clients`** table access. **PBKDF2_SALT** and **PBKDF2_ITERATIONS** must match **`hotspot_proxy_self_service.py`** (or your password-registration tool) so stored password hashes verify in the monitor API and **adn-server** integrated proxy.

| Key | Meaning |
|-----|---------|
| **DB_SERVER**, **DB_USERNAME**, **DB_PASSWORD**, **DB_NAME**, **DB_PORT** | MySQL connection for **`Clients`** (and related) tables. |

If MySQL is unavailable, **auth** and **self-service** API routes are not registered.

---

## `OPB_FILTER`

Comma-separated **network IDs** (as strings). Traffic from those OpenBridge sources can be **hidden** from certain dashboard persistence paths (see monitor controller `OPB_FILTER` handling).

---

## `ALIASES`

Similar idea to the peer server: download **peer / subscriber / TGID** JSON and optional checksums. Keys include **PATH**, **\*_FILE**, **\*_URL**, **STALE_HOURS**, **REVIEW_INTERVAL_MINUTES**, **CHECKSUM_***, **TG_LIST_URL**, **BRIDGE_LIST_URL** (API proxy for frontend pages).

---

## `LOGGER`

| Key | Meaning |
|-----|---------|
| **LOG_PATH** | Directory for log files. |
| **LOG_FILE** | Monitor log filename (e.g. `adn-monitor.log`). |
| **LOG_LEVEL** | e.g. `INFO`, `DEBUG`. |

---


## `MONITOR_APP`

| Key | Meaning |
|-----|---------|
| **LISTEN_HOST** | Bind address (`""` = all IPv4). |
| **LISTEN_PORT** | HTTP port (e.g. `8080`): `/api/*` and `/ws`. |
| **INGEST** | `tcp` (client to `ADN_CONNECTION`) or `mqtt` (broker topics). |
| **MQTT** | Required when `INGEST: mqtt` (`URL`, `TOPIC_PREFIX`, `QOS`). |
| **FREQUENCY** | Background periodic resync (seconds); live updates are event-driven. |
| **CLIENT_TIMEOUT** | Drop idle WS clients after N seconds (`0` = disable). |
| **CORS_ORIGINS** | Allowed origins for dev (optional). |

In production, Nginx proxies `/api` and `/ws` to **LISTEN_PORT**. No separate WebSocket port is needed.

Obsolete **`WEBSOCKET_SERVER`** YAML (Twisted on a separate port) is ignored; use **`MONITOR_APP`**.

---

## `DASHBOARD`

| Key | Meaning |
|-----|---------|
| **DASHTITLE** | Header title. |
| **BACKGROUND** | Use `bk.jpg` background if `true`. |
| **LANGUAGE** | Default UI language (`en`, `es`, …). |
| **SELF_SERVICE** | If `true`, the UI can show the **Self-service** nav entry (monitor API + MySQL required). |
| **SHOW_CONSOLE** | Show console page (call start/end messages). |
| **MIN_DURATION** | Minimum call duration (seconds) for **dashboard** Last Heard table (Last Heard page may still show shorter). |
| **SERVER_STATUS_URL** | JSON feed for the network server list (`/serverstatus`), proxied at `GET /api/servers/status`. Default `https://adn.systems/servers/status.json`. |
| **nav_links**, **footer**, **news** | Optional structured links / marquee items. |

Changes to **`DASHBOARD`** and **`MAP`** apply without a restart: `/api/config/dashboard` rebuilds them when `adn-monitor.yaml` changes on disk and keeps the last good version if a save leaves the file broken. Visitors get them on their next page load. Ports, database and ingest are still read only at start.

---

## `MAP`

Maps on **Home** (a flag over each country with a call on air) and **Linked Systems** (the connected systems). Markers come from the coordinates each peer sends when it logs in (RPTC); systems that report none are left out, or placed on their country with `APPROX_BY_COUNTRY`.

| Key | Meaning |
|-----|---------|
| **ENABLED** | Show both maps (default `true`). |
| **STYLE** | `1` (default) raster tiles from `TILE_URL`; `2` vector world: country shapes with their codes, active countries outlined, no tile provider. |
| **TILE_URL**, **TILE_URL_DARK**, **ATTRIBUTION**, **MAX_ZOOM** | Raster tiles for style 1. By default OpenStreetMap, darkened with a CSS filter in the dark theme; `TILE_URL_DARK` uses a real dark basemap instead. Keep `ATTRIBUTION` as the tile provider asks. |
| **ZOOM**, **CENTER** | First view when the visitor has no saved one. `auto` (default) fits the whole world to each map, phone or desktop; a number (quarter steps, e.g. `6` or `7.5`) fixes the zoom around `CENTER` `[lat, lon]`. |
| **HOTSPOT_PRECISION**, **REPEATER_PRECISION** | Decimals kept in the coordinates sent to the browser: `2` is about 1 km, `3` about 100 m, `null` keeps them as reported. Hotspots default to `2`, since they usually sit at someone's home; repeaters to `null`. |
| **APPROX_BY_COUNTRY** | Place systems with no coordinates on their country (from the DMR ID prefix), drawn dashed and labelled approximate (default `true`). |
| **GPS_URL**, **GPS_LABEL**, **GPS_REFRESH_SEC** | Optional extra layer from your own feed (APRS gateway, GPS service): GeoJSON points, a list of `{callsign, lat, lon}` or `{"stations": [...]}`. Not offered while `GPS_URL` is empty. |
| **OVERRIDES** | Fixed coordinates for systems that report none, as `peer ID: [lat, lon]`. |

---

## Database schema and migrations

**adn-monitor** ships SQL migrations in `monitor/src/adn_monitor/infrastructure/persistence/schema.py`. Apply with **`db_bootstrap --update`** (or equivalent) when upgrading.

| Migration | Table / change |
|-----------|----------------|
| **`004_peer_dynamic_tgs`** | **`peer_dynamic_tgs`** — per-peer dynamic TG rows written by **adn-server 2.0.0-rc.3+** (shared schema). |

**adn-server** also ensures **`peer_dynamic_tgs`** exists on startup (idempotent). Either path is sufficient; both can run against the same **`hbmon`** database.

---

## Environment

- **`ADN_CONFIG_PATH`**: Absolute path to **`adn-monitor.yaml`** for **`monitor.py`**.
- Project root **`.env`**: `VITE_API_BASE`, `VITE_DEFAULT_LANGUAGE` (frontend build); auto-loaded by `monitor.py` and `db_bootstrap.py`.

---

## See also

- [Documentation home](../README.md)
- [Architecture](architecture.md)
- [Self-service](self-service.md)
- Peer server **`REPORTS`**: [Monitoring and reports](../server/user-guide/monitoring.md), [Server configuration](../server/user-guide/configuration.md) (section **`REPORTS`**).
