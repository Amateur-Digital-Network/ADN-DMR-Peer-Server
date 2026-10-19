# Protocolo de informes v2 (JSON)

**Estado:** esquema + wire TCP slim (`STATE_SND`) en adn-server 2.x; pareja de producción requiere adn-monitor 2.x.

## Objetivos

Sustituir instantáneas al monitor que hoy usan **pickle** (`CONFIG_SND`, `BRIDGE_SND`) y **CSV** (`BRDG_EVENT`) por **JSON tipado**.

**Política de releases:** **adn-server 1.0.x** + **adn-monitor 1.0.x** = report v1 (tags congelados). El servidor **2.x** emite **HELLO** con `report_protocol: 2` y JSON v2 en TCP. El flujo de conexión del monitor es **`HELLO` → `STATE_SND` (`dashboard_state`)** más **`ROUTING_TABLE_SND` / `DELTA_SND`** asíncronos para chips UA/SINGLE_TS y **`VOICE_EVENT_SND`** para llamadas en vivo. **`topology` es solo interno** (construye `dashboard_state`, no se emite en TCP). **adn-monitor 2.x** decodifica v1 de peers legacy y v2 de **adn-server 2.x**.

## Transporte (sin cambios)

- TCP **netstring** (Twisted `NetstringReceiver`), mismo puerto que v1 (`REPORTS.REPORT_PORT`).
- Cada trama: **1 byte de opcode** + payload **JSON UTF-8** (v2) o pickle/CSV (v1).

### Opcodes

| Opcode | Hex | Payload v1 | Payload v2 |
|--------|-----|------------|------------|
| `HELLO` | `0xFF` | JSON hello (`protocol`: 1) | JSON hello (`report_protocol`: 2) |
| `CONFIG_SND` | `0x01` | pickle SYSTEMS | — (`STATE_SND`) |
| `BRIDGE_SND` | `0x03` | pickle BRIDGES | — (`ROUTING_TABLE_SND`) |
| `BRDG_EVENT` | `0x07` | texto CSV | — (`VOICE_EVENT_SND`) |
| `STATE_SND` | `0x14` | — | JSON `dashboard_state` |
| `TOPOLOGY_SND` | `0x10` | — | *(solo esquema interno — no se emite en TCP)* |
| `ROUTING_TABLE_SND` | `0x11` | — | JSON `routing_table` |
| `VOICE_EVENT_SND` | `0x12` | — | JSON `voice_event` |
| `DELTA_SND` | `0x13` | — | JSON `delta` |

Los opcodes `0x10`–`0x13` están reservados en esta fase; pueden ajustarse antes de P1-002.

```mermaid
flowchart TB
  subgraph v1 [Informe v1 — adn-server 1.0.x + monitor 1.0.x]
    H1[HELLO protocol 1] --> C1[CONFIG_SND pickle]
    C1 --> B1[BRIDGE_SND pickle]
    B1 --> V1[BRDG_EVENT CSV]
  end

  subgraph v2 [Informe v2 — adn-server 2.x + monitor 2.x]
    H2[HELLO report_protocol 2] --> S2[STATE_SND dashboard_state]
    S2 --> R2[ROUTING_TABLE_SND JSON async]
    R2 --> V2[VOICE_EVENT_SND JSON]
    R2 -.-> D2[DELTA_SND opcional]
  end
```

## Handshake (`hello`)

Al conectar, el servidor envía **`HELLO` (`0xFF`)** primero. Clientes v2 miran `report_protocol`:

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

| Campo | Notas |
|-------|--------|
| `report_protocol` | **2** para este esquema. Distinto del campo legacy `protocol: 1`. |
| `features` | Tokens v1 sin cambios; v2 añade `REPORT_V2` y capacidades de payload. |

## Tipos de mensaje

| `type` | Sustituye | Uso |
|--------|-----------|-----|
| `dashboard_state` | `CONFIG_SND` (slim) | Instantánea de sistemas enlazados (`STATE_SND`). **Snapshot completo** — el monitor elimina masters ausentes. |
| `topology` | `CONFIG_SND` (completo) | Solo esquema interno / ops — **no** se envía en TCP al monitor. |
| `routing_table` | `BRIDGE_SND` | Piernas de bridge por TG / reflector (chips UA/SINGLE_TS). |
| `voice_event` | `BRDG_EVENT` | Inicio/fin/ingress de llamadas. |
| `delta` | — | Parche incremental de `routing_table` desde `since_seq`. |

## Payloads de referencia

Cada trama lleva un único objeto JSON con `type` obligatorio. Los ejemplos usan IDs anonimizados.

### `dashboard_state`

Ver ejemplo completo en `schemas/examples/dashboard_state.json` (mismo objeto que MQTT `{prefix}/state`).

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

| Campo | Tipo | Obligatorio | Notas |
|-------|------|-------------|-------|
| `seq`, `ts` | int, float | sí | Secuencia monótona y epoch. |
| `systems[].name` | string | sí | Clave de sistema (config). |
| `systems[].mode` | string | sí | `MASTER`, `PEER` u `OPENBRIDGE`. |
| `systems[].enabled` | bool | sí | Flag enabled de config. |
| `systems[].ip`, `port` | string, int | no | Endpoint de escucha/conexión. |
| `systems[].repeat` | bool | no | Repeat en master. |
| `systems[].enhanced_obp` | bool | no | OBP enhanced. |
| `systems[].peers[]` | array | no | `{id, connected, ip?, port?}` por radio peer. |
| `systems[].peers[].latitude`, `longitude`, `height` | string | no | Datos del sitio tal como los envió el peer en RPTC. |
| `systems[].peers[].options` | string | no | Línea OPTIONS con los TG estáticos que el peer recibe de verdad (los suyos más `TS1_STATIC` / `TS2_STATIC` del MASTER, primero los del yaml); sin `PASS=`. |
| `systems[].peers[].connected_at` | int | no | Hora Unix en que el peer terminó de iniciar sesión. |
| `systems[].peers[].ts1_static`, `ts2_static` | array de string | no | TG estáticos que el peer recibe de verdad por slot, en el mismo orden. |
| `systems[].peers[].single_mode` | bool | no | `SINGLE=1` de las OPTIONS del peer; si no, `SINGLE_MODE` del MASTER. |
| `systems[].peers[].ua_timer_min` | number | no | `TIMER` de las OPTIONS del peer (minutos); si no, `DEFAULT_UA_TIMER`; se omite si es infinito. |

Sin contraseñas ni material de cifrado (a diferencia del pickle legacy).

### `routing_table`

```json
{
  "type": "routing_table",
  "seq": 42,
  "ts": 1717555260.5,
  "routes": [
    {
      "bridge_key": "52090",
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
      "bridge_key": "#310",
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

| Campo | Tipo | Obligatorio | Notas |
|-------|------|-------------|-------|
| `routes[].bridge_key` | string | sí | TG o clave reflector (`#nnn`). |
| `legs[].system` | string | sí | Nombre del sistema destino. |
| `legs[].ts` | 1 \| 2 | sí | Timeslot (legacy `TS`). |
| `legs[].tgid` | int | sí | Talkgroup (1–16777215). |
| `legs[].active` | bool | sí | Pierna activa en BRIDGES. |
| `legs[].to_type` | string | sí | `ON`, `OFF`, `STAT` o `NONE`. |
| `legs[].timer_expires_at` | float | no | Expiración `rule_timer` (legacy `TIMER`). |

### `voice_event`

CSV legacy:

```text
GROUP VOICE,START,RX,MASTER-A,2155905152,1001,3120001,2,52090
```

Equivalente v2:

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

| Campo | Tipo | Obligatorio | Valores / notas |
|-------|------|-------------|-----------------|
| `call_family` | string | sí | `GROUP`, `PRIVATE`, `UNIT`, `VCSSBK`. |
| `phase` | string | sí | `INGRESS`, `START`, `END`. |
| `direction` | string | sí | `RX`, `TX`. |
| `stream_id` | int | sí | Stream HBP 32 bits. |
| `peer_id`, `src_id`, `dst_id` | int | sí | IDs DMR (1–16777215). |
| `slot` | int | sí | `1` o `2`. |
| `is_announcement` | bool | sí | `true` cuando la llamada es el PTT sintético de anuncio/TTS, no un peer real conectado — el `peer_id`/`src_id` reportado no debe atribuirse a ningún bridge logueado. |
| `duration_s` | float \| null | no | En eventos `END`. |

Semántica OpenBridge INGRESS/START: ver [Monitor e informes](../user-guide/monitoring.md).

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
        "bridge_key": "52090",
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

| Campo | Tipo | Obligatorio | Notas |
|-------|------|-------------|-------|
| `since_seq` | int | sí | Último `seq` aplicado por el cliente. |
| `patch` | object | sí | Parcial `topology` o `routing_table` (misma forma). |

## Secuenciación

- `topology` y `routing_table` llevan `seq` (uint) y `ts` (epoch float).
- `delta` indica `since_seq` respecto al último `seq` aplicado por el cliente.

## Configuración

```yaml
REPORTS:
  REPORT: true
  REPORT_PORT: 4321
```

Sin `PROTOCOL` en **2.x** — wire siempre JSON (`wire.py`). Mapeo: **`application/report/`**.

### Espejo MQTT opcional

Por defecto los informes salen **solo** por TCP netstring (adn-monitor y otros clientes TCP). MQTT queda **deshabilitado** salvo activación explícita.

**Activar** solo cuando se cumplen ambas condiciones:

1. `REPORTS.MQTT.ENABLED: true` (booleano `true`, no basta con existir la clave)
2. `REPORTS.MQTT.URL` — URL del broker (`mqtt://host:1883` o `mqtts://host:8883`)

```yaml
REPORTS:
  REPORT: true
  REPORT_PORT: 4321
  MQTT:
    ENABLED: true
    URL: mqtt://127.0.0.1:1883
    TOPIC_PREFIX: adn/73010   # opcional; por defecto adn/{GLOBAL.SERVER_ID}
    USERNAME: my-mqtt-user     # opcional; sustituye al usuario en la URL
    PASSWORD: my-mqtt-secret   # opcional; sustituye a la contraseña en la URL
    CAFILE: /path/to/ca.pem    # opcional; CA del broker con mqtts://
    QOS: 0                    # opcional, 0–2
```

**Client ID:** generado al arrancar como `adn-server-{GLOBAL.SERVER_ID}-{random}` (no configurable; el sufijo aleatorio evita colisiones de sesión en el broker al reiniciar).

**Autenticación:** usuario/contraseña con `USERNAME` y `PASSWORD`, o embebidos en la URL (`mqtt://user:pass@host:1883`). Las credenciales YAML tienen prioridad sobre la URL. La contraseña puede ir vacía si el broker lo permite. Con `mqtts://`, indique `CAFILE` si el broker usa una CA propia.

Dependencia opcional: `pip install 'adn-server[mqtt]'` (paho-mqtt). Si MQTT está habilitado pero falta la librería, el servidor registra error y sigue solo con TCP.

**Wire MQTT (fijo, no configurable):** solo **`voice_event`** (telemetría) y **`state`** (snapshot). Los tipos solo-TCP (`topology`, `routing_table`, `delta`, `hello`) **no** se publican por MQTT.

**Convención de topics** (compartidos bajo `{prefix}`):

| Topic | Dirección | `type` JSON | Retain |
|-------|-----------|-------------|--------|
| `voice_event` | servidor → broker | `voice_event` | no |
| `state` | servidor → broker | `dashboard_state` | sí |

`state` incluye masters con peers conectados, peers sueltos y openbridges (equivalente WebSocket `conf,lnksys` + `conf,opb`). Va con **retain** para que nuevos suscriptores reciban el último snapshot sin pedirlo. Los **refrescos por topología** republican `{prefix}/state` cuando cambia el dashboard (dedup).

**Disparadores:** `voice_event` en vivo; `{prefix}/state` retenido al cambiar topología y tras conectar MQTT (dedup).

**Ejemplo** (`SERVER_ID` 7302):

```bash
mosquitto_sub -h BROKER -p 1883 -u USER -P PASS -t 'adn/7302/state' -v
mosquitto_sub -h BROKER -p 1883 -u USER -P PASS -t 'adn/7302/voice_event' -v
```

**ACL TBMQ:** consumidores con **subscribe** en `adn/7302/state` y `adn/7302/voice_event`; el `client_id` del servidor solo necesita **publish** en esos dos topics (sin subscribe en el servidor).

**Recarga (`systemctl reload` / SIGHUP):** si cambia `REPORTS.MQTT` (activar/desactivar, URL, credenciales, TLS, `TOPIC_PREFIX`, `QOS`), el servidor desconecta el cliente MQTT anterior y conecta con la nueva config, o se queda sin MQTT si `ENABLED` pasa a false.

**QoS:** configurable con `MQTT.QOS` (por defecto `0`).

Los timers usan **deltas** cuando solo cambia parte de `BRIDGES`; conexión, `REPORT_INTERVAL`, reload y `CONFIG_REQ` / `BRIDGE_REQ` envían instantáneas **completas**.

## Acoplamiento de versiones (combinaciones soportadas)

| Servidor | Monitor | Wire de informes | Notas |
|----------|---------|------------------|--------|
| **1.0.x** | **1.0.x** | v1 (pickle/CSV) | Par congelado |
| **2.0.0-alpha.\*** | **2.x** (dev) | solo v2 | Línea `develop` actual |
| **2.0.0** | **2.0.x** | solo v2 | Par GA; monitor 1.0.x **no** soportado |

No usar monitor 1.0.x con servidor 2.0.0 ni monitor 2.x con servidor 1.0.x en producción.

Ver también [Monitor e informes](../user-guide/monitoring.md).
