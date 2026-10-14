# Hotspot proxy (integrated)

**ADN DMR Peer Server** includes an **integrated hotspot proxy**: one process (`adn-server.py`) accepts Homebrew (HBP) from many hotspots on a single UDP port and **injects** traffic into a configured **MASTER** system. You do **not** need a separate **`adn-proxy`** process when this mode is enabled.

Configuration lives in **`adn-server.yaml`** under **`PROXY`** and optional **`SELF_SERVICE`** (same MySQL **`Clients`** table as **adn-monitor**).

---

## When to use it

| Deployment | What to run |
|------------|-------------|
| **Typical ADN stack** (monitor + dashboard + many Pi-Star hotspots) | **`adn-server.py`** with **`PROXY`** + **`SELF_SERVICE`**. |

The integrated proxy uses **fan-in**: hotspots only need **`PROXY.LISTEN_PORT`** (e.g. **62031**). The target **MASTER** is **inject-only** — it does **not** bind its own UDP port for that system (no per-hotspot port range on the server host).

```mermaid
flowchart LR
  HS1[Hotspot A] -->|UDP HBP| LP[PROXY LISTEN_PORT]
  HS2[Hotspot B] -->|UDP HBP| LP
  LP -->|inject| MASTER[TARGET_SYSTEM MASTER]
```

---

## Optional dependency (self-service)

MySQL self-service requires **`mysqlclient`**:

```bash
pip install -e ".[selfservice]"
```

If **`USE_SELFSERVICE: true`** but **`mysqlclient`** is missing, startup fails with a clear error. Set **`USE_SELFSERVICE: false`** to run the proxy without DB (no dashboard-driven **RPTO** updates).

---

## `PROXY` keys

| Key | Role |
|-----|------|
| **LISTEN_PORT** | UDP port where **hotspots** connect (the address users configure on the hotspot). |
| **LISTEN_IP** | Bind address; empty = all interfaces. |
| **TARGET_SYSTEM** | Name of the **`SYSTEMS`** **MASTER** entry that receives injected HBP (must exist and be **ENABLED**). |
| **TIMEOUT** | Idle session timeout (seconds); expired sessions are torn down on the MASTER. |
| **DEBUG** | Verbose packet logging. |
| **CLIENT_INFO** | Log connect/disconnect per radio ID. |
| **BLACK_LIST** | Block listed radio IDs. |
| **IP_BLACK_LIST** | Block source IPs (with optional expiry). |

There is **no** **`MASTER`**, **`PORT`**, or **`GENERATOR`** in integrated **`PROXY`** — those belong to the legacy standalone proxy. The target MASTER uses **`MAX_PEERS`** (not a UDP port range) to cap concurrent hotspot sessions.

Example (from `adn-server.example.yaml`):

```yaml
PROXY:
  LISTEN_PORT: 62031
  LISTEN_IP: ""
  TARGET_SYSTEM: SYSTEM
  TIMEOUT: 30
  DEBUG: false
  CLIENT_INFO: true
  BLACK_LIST: []
  IP_BLACK_LIST: {}
```

### Inject-only target MASTER

When **`PROXY.TARGET_SYSTEM`** points at a system (e.g. **`SYSTEM`**), startup **removes** **`IP`** / **`PORT`** from that MASTER block. Hotspots never connect directly to the conference port; all HBP enters via **`LISTEN_PORT`**.

Set **`MAX_PEERS`** on the target MASTER to the maximum concurrent proxied hotspots (e.g. **102**). Other MASTER systems (e.g. **ECHO**, **D-APRS**) keep normal **`IP`** / **`PORT`** binds if they are not the proxy target.

---

## `SELF_SERVICE` keys

Same semantics as **`adn-monitor.yaml`** — shared **`Clients`** table, **`modified`** flag, **RPTO** toward the MASTER.

| Key | Role |
|-----|------|
| **USE_SELFSERVICE** | Enable MySQL-backed options sync (`true` / `false`). |
| **PBKDF2_SALT**, **PBKDF2_ITERATIONS** | Must **match** monitor/backend for password hashing. |

MariaDB connection settings live in the top-level **`DATABASE`** block (shared with dynamic TG persistence) — see [Configuration](configuration.md#database-mariadb).

On startup the server logs **`(SELF_SERVICE) Database connection test: OK`** and **`(SELF_SERVICE) Enabled`** when the pool connects. Self-service runs **asynchronously**; voice forwarding is not blocked on DB latency.

Details of the dashboard flow: [Self-service](../../monitor/self-service.md).

---

## Multi-hotspot behaviour

- Each authenticated hotspot is a **peer** on the inject-only MASTER with its own **OPTIONS** (static TGs). **Repeat** and monitor fan-out respect **per-peer OPTIONS** — traffic for a TG is not sent to peers that did not select it.
- **`TS1_STATIC`** / **`TS2_STATIC`** on the MASTER are added to every connected hotspot's statics, on top of its OPTIONS (TGs 0–5, 9 and 9990–9999 are ignored). A **SIGHUP** updates connected hotspots and the MASTER's bridge legs; the monitor shows each hotspot's resulting list, yaml TGs first.
- Hotspots talking at once on the same MASTER slot are tracked **per stream**: each one times out on its own and gets its own END with its real duration.
- A failed login, a callsign mismatch or one hotspot leaving no longer resets the MASTER's bridges; the reset (default OPTIONS restored, its bridge legs set idle) happens only when its **last** peer is gone.
- **Parrot / echo** talkgroups **9990–9999** are **point-to-point**: they bypass the OPTIONS filter and return **only** to the exact peer that originated the call (`RX_PEER` on the slot), never to other hotspots of the same user. With a single connected peer, it is delivered to that peer (legacy behaviour). See [Echo — Multi-hotspot behaviour](echo.md#multi-hotspot-behaviour-inject-only-proxy) and [Special numbers](special-numbers.md).

---

## OPTIONS line behaviour

After login (RPTL → RPTK → RPTC), the proxy starts a **10 s timer** waiting for
the hotspot's **RPTO** packet with its **OPTIONS** line. What the hotspot sends
(or does not send) determines **who is the source of truth** for the peer's
static talkgroups:

| Hotspot sends in the RPTO | Who defines the TGs | Behaviour |
|---|---|---|
| `OPTIONS=PASS=xxxxxx;` | **Self-service** (dashboard) | The proxy processes the `PASS=`, verifies the individual password (PBKDF2 against `Clients.psswd`), marks the peer as authenticated, cancels the 10 s timer, and pushes the DB-configured TGs to the master. The user **can** log in by password and auto-login by IP on the dashboard. |
| `OPTIONS=` (empty) | **Self-service** (dashboard) | The proxy reads the TGs from the DB and injects them to the master. The user can **only** use auto-login by IP on the dashboard (no password). |
| **No RPTO at all** (10 s timer expires) | **Self-service** (dashboard) | The proxy assumes the hotspot has no OPTIONS of its own and falls back to the DB. Same effect as `OPTIONS=` empty. |
| `OPTIONS=TS2=730444;SINGLE=0;` (content without `PASS=`) | **The hotspot itself** | The master takes the TGs **directly from the OPTIONS line**. The DB is ignored. The user can **only** use auto-login by IP on the dashboard (no password). |

**Key rule:** self-service is the source of truth **unless** the hotspot sends
explicit content (TGs, SINGLE, TIMER, etc.) in its OPTIONS line. In that case,
what the hotspot says **takes precedence** and self-service is ignored.

### Individual password and dashboard login

- If the hotspot **never** sends `PASS=` in its RPTO, the user **cannot** log in
  to the dashboard with a password. They can only use **auto-login by IP**
  (if their IP matches `Clients.host`).
- To enable password login on the dashboard, the hotspot must send
  `OPTIONS=PASS=your_password;` in its configuration (Pi-Star / WPSD / MMDVM
  `optsfile`). The password must match the PBKDF2 hash stored in `Clients.psswd`.
- The `PASS=` flow is what activates bidirectional sync: the proxy stores the
  hash, notes the `modified` flag, and pushes the DB-configured TGs to the master.

---

## Hot reload (`SIGHUP`)

**Applied without restart** (active proxy sessions stay up):

- **`PROXY`:** **TIMEOUT**, **DEBUG**, **CLIENT_INFO**, **BLACK_LIST**, **IP_BLACK_LIST**
- **`SELF_SERVICE`:** merged into config (credential changes take effect on new DB operations; loops are not restarted on reload)

**Requires full process restart:**

- **`PROXY.LISTEN_PORT`** / **`LISTEN_IP`** (bind change is logged and ignored at reload)
- **`PROXY.TARGET_SYSTEM`**
- Enabling or disabling **`USE_SELFSERVICE`** after startup (start/stop MySQL loops)

See [Configuration — hot reload](configuration.md#hot-reload-adn-serveryaml).

---

## See also

- [Configuration](configuration.md) — full **`adn-server.yaml`** reference.
- [Monitoring and reports](monitoring.md) — TCP reports, dashboard, log rotation.
- [Self-service](../../monitor/self-service.md) — **`Clients`**, **RPTO** timing.
