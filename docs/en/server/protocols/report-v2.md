# Report protocol v2 (JSON)

**Status:** schema + TCP slim wire (`STATE_SND`) shipped on adn-server 2.x; monitor 2.x consumer required for production pairing.

## Goals

Replace monitor snapshots that today use **pickle** (`CONFIG_SND`, `BRIDGE_SND`) and **CSV strings** (`BRDG_EVENT`) with **typed JSON** that any client can decode.

**Release policy:** **adn-server 1.0.x** + **adn-monitor 1.0.x** = report v1 (frozen tag pair). **2.x** server sends **HELLO** with `report_protocol: 2` and v2 JSON on TCP. The monitor connect path is **`HELLO` → `STATE_SND` (`dashboard_state`)** plus async **`ROUTING_TABLE_SND` / `DELTA_SND`** for UA/SINGLE_TS chips and **`VOICE_EVENT_SND`** for live calls. **`topology` is internal-only** (used to build `dashboard_state`, never sent on TCP). **adn-monitor 2.x** decodes v1 wire from legacy peers and v2 JSON from **adn-server 2.x**. **adn-monitor 1.0.0** cannot decode v2 opcodes — upgrade the monitor line for **adn-server 2.x**.

## Transport (unchanged)

- TCP **netstring** frames (Twisted `NetstringReceiver`), same port as v1 (`REPORTS.REPORT_PORT`).
- Each frame: **1-byte opcode** + **UTF-8 JSON payload** (v2) or pickle/CSV (v1).

### Opcodes

| Opcode | Hex | v1 payload | v2 payload |
|--------|-----|------------|------------|
| `HELLO` | `0xFF` | JSON hello (`protocol`: 1) | JSON hello (`report_protocol`: 2) |
| `CONFIG_SND` | `0x01` | pickle SYSTEMS | — (use `STATE_SND`) |
| `BRIDGE_SND` | `0x03` | pickle BRIDGES | — (use `ROUTING_TABLE_SND`) |
| `BRDG_EVENT` | `0x07` | CSV text | — (use `VOICE_EVENT_SND`) |
| `STATE_SND` | `0x14` | — | JSON `dashboard_state` |
| `TOPOLOGY_SND` | `0x10` | — | *(internal schema only — not emitted on TCP)* |
| `ROUTING_TABLE_SND` | `0x11` | — | JSON `routing_table` |
| `VOICE_EVENT_SND` | `0x12` | — | JSON `voice_event` |
| `DELTA_SND` | `0x13` | — | JSON `delta` |

```mermaid
flowchart TB
  subgraph v1 [Report v1 — adn-server 1.0.x + monitor 1.0.x]
    H1[HELLO protocol 1] --> C1[CONFIG_SND pickle]
    C1 --> B1[BRIDGE_SND pickle]
    B1 --> V1[BRDG_EVENT CSV]
  end

  subgraph v2 [Report v2 — adn-server 2.x + monitor 2.x]
    H2[HELLO report_protocol 2] --> S2[STATE_SND dashboard_state]
    S2 --> R2[ROUTING_TABLE_SND JSON async]
    R2 --> V2[VOICE_EVENT_SND JSON]
    R2 -.-> D2[DELTA_SND optional]
  end
```

## Handshake (`hello`)

On connect the server sends **`HELLO` (`0xFF`)** first (same as today). v2 clients inspect `report_protocol`:

```json
{
  "type": "hello",
  "server": "adn-server",
  "version": "2.0.0-alpha.1",
  "report_protocol": 2,
  "features": ["INGRESS", "END_TX_FORWARD", "PUSH_ON_CONNECT", "REPORT_V2", "ROUTING_TABLE_JSON", "VOICE_EVENT_JSON", "DELTA_UPDATES"],
  "systems": ["MASTER-A", "OBP-CL"]
}
```

| Field | Notes |
|-------|--------|
| `report_protocol` | **2** for this schema. Distinct from legacy field `protocol: 1`. |
| `features` | v1 tokens unchanged; v2 adds `REPORT_V2` and payload capabilities. |

Monitor **1.0.x** does not speak this wire; use the **1.0.x** server tag for that pair.

## Message types

| `type` | Replaces | Purpose |
|--------|----------|---------|
| `dashboard_state` | `CONFIG_SND` (slim) | Linked masters/peers/openbridges snapshot (`STATE_SND`). **Full snapshot** — monitor prunes absent masters. |
| `topology` | `CONFIG_SND` (full) | Internal schema / ops only — **not** sent on TCP monitor wire. |
| `routing_table` | `BRIDGE_SND` | Active bridge legs per talkgroup / reflector key (UA/SINGLE_TS chips). |
| `voice_event` | `BRDG_EVENT` | Structured call start/end/ingress. |
| `delta` | — | Incremental `routing_table` patch since `since_seq`. |

## Reference payloads

Each frame payload is one JSON object with a required `type`. Examples below use anonymized IDs.

### `dashboard_state`

```json
{
  "type": "dashboard_state",
  "ts": 1717555200.0,
  "server_id": "7302",
  "ctable": {
    "MASTERS": {
      "MASTER-A": {
        "mode": "MASTER",
        "ip": "10.0.0.1",
        "port": 62030,
        "peers": {
          "3120001": {
            "id": 3120001,
            "connected": true,
            "ip": "10.0.0.50",
            "port": 62031,
            "callsign": "CE5RPY"
          }
        }
      }
    },
    "PEERS": {},
    "OPENBRIDGES": {
      "OBP-CL": {
        "mode": "OPENBRIDGE",
        "network_id": 73010,
        "ip": "44.31.61.68",
        "port": 62999,
        "streams": {}
      }
    }
  }
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `ts` | float | yes | Epoch time of snapshot. |
| `server_id` | string | no | `GLOBAL.SERVER_ID` (MQTT retained state). |
| `ctable.MASTERS` | object | yes | Masters with **connected** peers only. |
| `ctable.PEERS` | object | yes | Connected upstream PEER/XLXPEER systems. |
| `ctable.OPENBRIDGES` | object | yes | Enabled OpenBridge legs (`streams` empty; live chips from `voice_event`). |

Committed example: `schemas/examples/dashboard_state.json`.

### `topology`

```json
{
  "type": "topology",
  "seq": 1,
  "ts": 1717555200.0,
  "systems": [
    {
      "name": "MASTER-A",
      "mode": "MASTER",
      "enabled": true,
      "ip": "10.0.0.1",
      "port": 62030,
      "repeat": true,
      "peers": [
        { "id": 3120001, "connected": true, "ip": "10.0.0.50", "port": 62031 }
      ]
    },
    {
      "name": "OBP-CL",
      "mode": "OPENBRIDGE",
      "enabled": true,
      "ip": "10.0.0.2",
      "port": 62044,
      "enhanced_obp": true,
      "peers": []
    }
  ]
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `seq`, `ts` | int, float | yes | Monotonic sequence and epoch time. |
| `systems[].name` | string | yes | System key (matches config). |
| `systems[].mode` | string | yes | `MASTER`, `PEER`, or `OPENBRIDGE`. |
| `systems[].enabled` | bool | yes | Config enabled flag. |
| `systems[].ip`, `port` | string, int | no | Listen/connect endpoint. |
| `systems[].repeat` | bool | no | Master repeat flag. |
| `systems[].enhanced_obp` | bool | no | OpenBridge enhanced mode. |
| `systems[].peers[]` | array | no | `{id, connected, ip?, port?}` per peer radio. |
| `systems[].peers[].latitude`, `longitude`, `height` | string | no | Site data as the peer sent it in RPTC. |
| `systems[].peers[].options` | string | no | OPTIONS line with the static TGs the peer actually gets (its own plus the MASTER's `TS1_STATIC` / `TS2_STATIC`, yaml ones first); `PASS=` stripped. |
| `systems[].peers[].ts1_static`, `ts2_static` | array of string | no | Static TGs the peer actually gets per slot, same order. |
| `systems[].peers[].single_mode` | bool | no | `SINGLE=1` from the peer's OPTIONS, else the MASTER's `SINGLE_MODE`. |
| `systems[].peers[].ua_timer_min` | number | no | `TIMER` from the peer's OPTIONS (minutes), else `DEFAULT_UA_TIMER`; omitted when infinite. |

No passwords or encryption material are included (unlike legacy pickle).

### `routing_table`

```json
{
  "type": "routing_table",
  "seq": 42,
  "ts": 1717555260.5,
  "routes": [
    {
      "relay_table_key": "52090",
      "legs": [
        {
          "system": "MASTER-A",
          "ts": 2,
          "tgid": 52090,
          "active": true,
          "to_type": "ON",
          "timer_expires_at": 1717555320.0
        },
        {
          "system": "MASTER-B",
          "ts": 2,
          "tgid": 52090,
          "active": true,
          "to_type": "ON"
        }
      ]
    },
    {
      "relay_table_key": "#310",
      "legs": [
        {
          "system": "MASTER-A",
          "ts": 2,
          "tgid": 310,
          "active": false,
          "to_type": "NONE"
        }
      ]
    }
  ]
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `routes[].relay_table_key` | string | yes | Talkgroup id or reflector key (`#nnn`). |
| `legs[].system` | string | yes | Target system name. |
| `legs[].ts` | 1 \| 2 | yes | Timeslot (legacy `TS`). |
| `legs[].tgid` | int | yes | Talkgroup (1–16777215). |
| `legs[].active` | bool | yes | Leg active in BRIDGES table. |
| `legs[].to_type` | string | yes | `ON`, `OFF`, `STAT`, or `NONE`. |
| `legs[].timer_expires_at` | float | no | `rule_timer` expiry (legacy `TIMER`). |

### `voice_event`

Legacy CSV:

```text
GROUP VOICE,START,RX,MASTER-A,2155905152,1001,3120001,2,52090
```

v2 equivalent:

```json
{
  "type": "voice_event",
  "ts": 1717555201.234,
  "call_family": "GROUP",
  "phase": "START",
  "direction": "RX",
  "system": "MASTER-A",
  "stream_id": 2155905152,
  "peer_id": 1001,
  "src_id": 3120001,
  "slot": 2,
  "dst_id": 52090,
  "duration_s": null,
  "is_announcement": false
}
```

| Field | Type | Required | Values / notes |
|-------|------|----------|----------------|
| `call_family` | string | yes | `GROUP`, `PRIVATE`, `UNIT`, `VCSSBK`. |
| `phase` | string | yes | `INGRESS`, `START`, `END`. |
| `direction` | string | yes | `RX`, `TX`. |
| `stream_id` | int | yes | 32-bit HBP stream id. |
| `peer_id`, `src_id`, `dst_id` | int | yes | DMR ids (1–16777215). |
| `slot` | int | yes | `1` or `2`. |
| `duration_s` | float \| null | no | Set on `END`. |
| `is_announcement` | bool | yes | `true` when the call is the announcement/TTS synthetic PTT, not a real connected peer — the reported `peer_id`/`src_id` must not be attributed to any logged-in bridge. |

OpenBridge **INGRESS** vs **START** semantics match [Monitoring and reports](../user-guide/monitoring.md#openbridge-monitor-semantics): use `phase: "INGRESS"` for first sight, `phase: "START"` after loop control.

### `delta`

```json
{
  "type": "delta",
  "seq": 43,
  "ts": 1717555261.0,
  "since_seq": 42,
  "patch": {
    "type": "routing_table",
    "seq": 43,
    "ts": 1717555261.0,
    "routes": [
      {
        "relay_table_key": "52090",
        "legs": [
          {
            "system": "MASTER-A",
            "ts": 2,
            "tgid": 52090,
            "active": false,
            "to_type": "ON"
          }
        ]
      }
    ]
  }
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `since_seq` | int | yes | Last `seq` the client applied. |
| `patch` | object | yes | Partial `topology` or `routing_table` (same shape). |

## Sequencing

- `topology` and `routing_table` messages carry monotonic `seq` (uint) and `ts` (float epoch).
- Clients track last applied `seq`; `delta` messages set `since_seq` to the client watermark.
- Full snapshots may still be sent on connect and on `REPORTS.REPORT_INTERVAL` (same triggers as v1 CONFIG/BRIDGE).

## Configuration

```yaml
REPORTS:
  REPORT: true
  REPORT_PORT: 4321
```

No `PROTOCOL` switch on **2.x** — wire is always JSON (`infrastructure/twisted_adapters/report/wire.py`). Payload mapping: **`application/report/`**.

### Optional MQTT mirror

By default the server sends reports **only** over TCP netstring (adn-monitor and other TCP clients). MQTT is **disabled** unless you explicitly enable it.

**Enable** only when both are set:

1. `REPORTS.MQTT.ENABLED: true` (boolean `true`, not merely present)
2. `REPORTS.MQTT.URL` — broker URL (`mqtt://host:1883` or `mqtts://host:8883`)

```yaml
REPORTS:
  REPORT: true
  REPORT_PORT: 4321
  MQTT:
    ENABLED: true
    URL: mqtt://127.0.0.1:1883
    TOPIC_PREFIX: adn/73010   # optional; default adn/{GLOBAL.SERVER_ID}
    USERNAME: my-mqtt-user     # optional; overrides user in URL
    PASSWORD: my-mqtt-secret   # optional; overrides password in URL
    CAFILE: /path/to/ca.pem    # optional; broker TLS trust store (mqtts://)
    QOS: 0                    # optional, 0–2
```

**Client ID:** auto-generated at startup as `adn-server-{GLOBAL.SERVER_ID}-{random}` (not configurable; random suffix avoids broker session collisions on restart).

**Authentication:** username/password via `USERNAME` and `PASSWORD`, or embedded in the URL (`mqtt://user:pass@host:1883`). YAML credentials override URL userinfo. Password may be empty if the broker allows it. With `mqtts://`, set `CAFILE` when the broker uses a private CA.

Requires optional dependency: `pip install 'adn-server[mqtt]'` (paho-mqtt). If MQTT is enabled but the library is missing, the server logs an error and continues with TCP only.

**MQTT wire (fixed, not configurable):** only **`voice_event`** (telemetry) and **`state`** (snapshot). TCP-only types (`topology`, `routing_table`, `delta`, `hello`) are **not** published on MQTT.

**Topic convention** (shared under `{prefix}`):

| Topic | Direction | JSON `type` | Retain |
|-------|-----------|-------------|--------|
| `voice_event` | server → broker | `voice_event` | no |
| `state` | server → broker | `dashboard_state` | yes |

`state` carries masters with connected peers, homebrew peers, and openbridges (monitor WebSocket `conf,lnksys` + `conf,opb` intent). It is **retained** so new subscribers receive the last snapshot without requesting it. **Topology-driven refreshes** republish `{prefix}/state` when the dashboard changes (dedup).

**Triggers:** live `voice_event`; retained `{prefix}/state` on topology changes and after MQTT connect (dedup).

**Example** (`SERVER_ID` 7302):

```bash
mosquitto_sub -h BROKER -p 1883 -u USER -P PASS -t 'adn/7302/state' -v
mosquitto_sub -h BROKER -p 1883 -u USER -P PASS -t 'adn/7302/voice_event' -v
```

**Broker ACL:** consumers need **subscribe** on `adn/7302/state` and `adn/7302/voice_event`; the server `client_id` needs **publish** on those two topics only (no server-side subscribe).

**Reload (`systemctl reload` / SIGHUP):** when `REPORTS.MQTT` changes (enable/disable, URL, credentials, TLS, `TOPIC_PREFIX`, `QOS`), the server disconnects the old MQTT client and connects with the new settings, or stays offline if `ENABLED` becomes false.

**QoS:** configurable via `MQTT.QOS` (default `0`).

**`rule_timer`**, **`stat_trimmer`**, and **`bridgeDebug`** use **routing deltas** when only part of `BRIDGES` changed; connect, `REPORT_INTERVAL`, reload, and client `CONFIG_REQ` / `BRIDGE_REQ` send **full** snapshots.

## Version pairing (supported combinations)

| Server | Monitor | Report wire | Notes |
|--------|---------|-------------|--------|
| **1.0.x** | **1.0.x** | v1 (pickle/CSV) | Frozen pair; no cross-upgrade of report protocol |
| **2.0.0-alpha.\*** | **2.x** (dev) | v2 only | Current `develop` line |
| **2.0.0** | **2.0.x** | v2 only | GA pair; monitor 1.0.x **not** supported |

Do **not** run monitor 1.0.x against server 2.0.0 or monitor 2.x against server 1.0.x for production.

See also [Monitoring and reports](../user-guide/monitoring.md).
