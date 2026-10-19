# Arquitectura e implantación

## Arquitectura limpia (monitor Python)

Bajo `monitor/src/adn_monitor/`:

- **Dominio** — entidades, errores, `Result`, opcodes, `UserSession`.
- **Aplicación** — `MonitorState`, casos de uso (auth, self-service, informes, dashboard), puertos (`AuthRepository`, `HttpFetcherPort`, …).
- **Infraestructura** — YAML, FastAPI (REST + `/ws`), ingest TCP (Twisted en hilo) o MQTT, MySQL, decodificadores de informes.

El **composition root** está en `infrastructure/fastapi/composition.py` (`build_monitor_api`).

```mermaid
flowchart TD
  INF["Infraestructura<br/>YAML · FastAPI /ws · ingest TCP/MQTT<br/>MySQL · decodificadores pickle/JSON"]
  APP["Aplicación<br/>MonitorState · informes/dashboard<br/>auth · self-service · alias"]
  DOM["Dominio<br/>objetos de valor · errores · opcodes<br/>UserSession · Result"]
  INF --> APP --> DOM
```

## Proceso unificado (`monitor.py`)

Un solo proceso uvicorn/FastAPI:

| Ruta / rol | Contenido |
|------------|-----------|
| `/api/*` | Config dashboard, auth, self-service, proxy alias/status |
| `/ws` | Protocolo `conf,<grupo>` — CTABLE, Last Heard, voz. Ver [Compresión del WebSocket](#websocket-compression). |
| Ingest (fondo) | `MONITOR_APP.INGEST`: `tcp` (cliente a `ADN_CONNECTION`) **o** `mqtt` (tópicos `state` + `voice_event`) |
| Alias (fondo) | Descarga → `FILES.PATH`; import con **staging + RENAME** (commits cada 10k en staging; swap breve); merge con commits cada 2k; **PK en `id`** |

## Protocolo de informes (desde el peer server)

Modos de ingest según el peer: legacy (pickle), v1 HELLO, v2 slim (`dashboard_state` + `voice_event`). Ver [Monitor e informes](../server/user-guide/monitoring.md).

## Frontend

- **Vite + React** en `frontend/`; build → `frontend/dist/`.
- Desarrollo: Vite hace proxy de `/api` y `/ws` a `MONITOR_APP.LISTEN_PORT` (p. ej. 8080).
- Producción: Nginx sirve estáticos y proxifica `/api` + `/ws` al mismo puerto FastAPI.

<a id="websocket-compression"></a>
### Compresión del WebSocket

El dashboard abre `/ws?z=1` cuando el navegador tiene `DecompressionStream`. Entonces cada broadcast de 512 bytes o más se comprime una sola vez con zlib y se envía como el mismo frame binario a todas esas pestañas, que lo descomprimen y procesan los mensajes en el orden en que llegan; los más cortos siguen como texto. Las pestañas sin `?z=1` (navegadores antiguos, o una pestaña que sigue con un frontend anterior) reciben texto sin comprimir. El `permessage-deflate` por conexión de uvicorn está apagado: comprimía cada mensaje una vez por pestaña, y con muchas pestañas abiertas era la mayor porción de la CPU del monitor (37 % en un servidor con 29). Los opcodes y el JSON son los mismos en ambos casos, y nginx no necesita cambios.

## Proxy hotspot

**Integrado (predeterminado):** `adn-server.py` con `PROXY` + `SELF_SERVICE` en `adn-server.yaml`. Ver [Proxy hotspot integrado](../server/user-guide/hotspot-proxy.md).

El proceso **`adn-proxy`** independiente se eliminó del repositorio **adn-monitor**; usa solo **`PROXY`** integrado.

## Topología típica

```mermaid
flowchart TD
  HS[Hotspots] -->|UDP HBP| PROXY[adn-server PROXY]
  PROXY --> MASTER[MASTER inyectado]
  PROXY --> DB[(MySQL Clients)]

  MASTER --> REPORT[REPORTS listener]
  REPORT <-->|TCP o MQTT| ING[monitor.py ingest]
  ING --> APP[FastAPI /api /ws]
  APP --> DB

  BR[Navegador] -->|HTTPS| FE[Nginx o SERVE_STATIC]
  FE --> APP
```

---

## Ver también

- [Inicio de la documentación](../README.md)
- [Configuración](configuration.md)
- [Self-service](self-service.md)
