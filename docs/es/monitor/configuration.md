# Configuración (`adn-monitor.yaml`)

Este documento describe **`adn-monitor.yaml`**, usado por **`monitor.py`**. La ruta por defecto suele ser **`monitor/adn-monitor.yaml`** (sobrescribible con **`ADN_CONFIG_PATH`**).

**Proxy hotspot** se configura en **`adn-server.yaml`** (`PROXY` + `SELF_SERVICE`) — ver [Proxy hotspot integrado](../server/user-guide/hotspot-proxy.md). **`SELF_SERVICE`** (MySQL / PBKDF2) debe ser **idéntica** entre **`adn-server.yaml`** y **`adn-monitor.yaml`**.

El ejemplo del repositorio **adn-monitor** (`monitor/adn-monitor.yaml.example`) es la plantilla del monitor; las claves siguientes coinciden con ese fichero y `monitor/src/adn_monitor/infrastructure/config_loader.py` (los nombres internos pueden diferir).

---

## `GLOBAL`

| Clave | Significado |
|-------|-------------|
| **BRIDGES_INC** | Mostrar estado de bridges en el panel si es `true`. |
| **HOMEBREW_INC** | Incluir estado HBP peer/master. |
| **LASTHEARD_INC** | Activar funciones / tablas Last Heard. |
| **LASTHEARD_ROWS** | Filas para widgets Last Heard. |
| **EMPTY_MASTERS** | Mostrar masters sin peers. |
| **TGCOUNT_INC** | Activar página / estadísticas de conteo de TG. |
| **TGCOUNT_ROWS** | Filas para el conteo de TG. |
| **TIMEZONE** | Zona horaria IANA (p. ej. `America/Santiago`): fechas Last Heard en pantalla; **TG Count** usa el **día calendario en esa zona** (medianoche local = nuevo día de estadísticas). Vacío → día UTC. |

---

## `ADN_CONNECTION` {#adn_connection}

Debe coincidir con la configuración de informes del **ADN DMR Peer Server**.

| Clave | Significado |
|-------|-------------|
| **ADN_IP** | Host/IP donde está el **listener TCP de informes** del peer server (vista de red desde el monitor). |
| **ADN_PORT** | Puerto TCP — debe ser igual a **`REPORTS.REPORT_PORT`** en el servidor y ser alcanzable. |
| **HELLO_TIMEOUT_MS** | Tras conectar por TCP, tiempo de espera del opcode **`0xFF` HELLO** (JSON) desde **ADN DMR Server**. Si no llega a tiempo, el monitor trata el peer como **legado** (solo CONFIG/BRIDGE pickle). Por defecto **1500** ms. Ver [Monitor e informes](../server/user-guide/monitoring.md). |

---

## `SELF_SERVICE`

Credenciales MySQL y parámetros PBKDF2 para **login**, **self-service** y acceso a la tabla **`Clients`**. **PBKDF2_SALT** y **PBKDF2_ITERATIONS** deben coincidir con **`hotspot_proxy_self_service.py`** (o tu herramienta de registro de contraseñas) para que los hashes verifiquen en la API del monitor y en el proxy integrado de **adn-server**.

| Clave | Significado |
|-------|-------------|
| **DB_SERVER**, **DB_USERNAME**, **DB_PASSWORD**, **DB_NAME**, **DB_PORT** | Conexión MySQL para **`Clients`** (y tablas relacionadas). |

Si MySQL no está disponible, las rutas de **auth** y **self-service** no se registran.

---

## `OPB_FILTER`

**Network IDs** separados por comas (como cadenas). El tráfico desde esas fuentes OpenBridge puede **ocultarse** de ciertas rutas de persistencia del panel (ver manejo de `OPB_FILTER` en el controlador del monitor).

---

## `ALIASES`

Misma idea que en el peer server: descargar JSON de **peer / subscriber / TGID** y checksums opcionales. Claves: **PATH**, **\*_FILE**, **\*_URL**, **STALE_HOURS**, **REVIEW_INTERVAL_MINUTES**, **CHECKSUM_***, **TG_LIST_URL**, **BRIDGE_LIST_URL** (proxy del backend para páginas del frontend).

---

## `LOGGER`

| Clave | Significado |
|-------|-------------|
| **LOG_PATH** | Directorio de ficheros de log. |
| **LOG_FILE** | Nombre del log del monitor (p. ej. `adn-monitor.log`). |
| **LOG_LEVEL** | p. ej. `INFO`, `DEBUG`. |

---

## `MONITOR_APP`

Proceso unificado **`monitor.py`**: REST y WebSocket en el **mismo** host/puerto.

| Clave | Significado |
|-------|-------------|
| **LISTEN_HOST** | Interfaz de escucha (`""` = todas las IPv4). |
| **LISTEN_PORT** | Puerto HTTP (p. ej. `8080`): `/api/*` y `/ws`. |
| **INGEST** | `tcp` (cliente a `ADN_CONNECTION`) o `mqtt` (tópicos del broker). |
| **MQTT** | Obligatorio si `INGEST: mqtt` (`URL`, `TOPIC_PREFIX`, `QOS`). |
| **FREQUENCY** | Resync periódico en segundo plano (segundos); las actualizaciones en vivo son por eventos. |
| **CLIENT_TIMEOUT** | Cerrar clientes WS inactivos tras N segundos (`0` = desactivado). |
| **CORS_ORIGINS** | Orígenes permitidos para desarrollo (opcional). |

Nginx en producción proxifica `/api` y `/ws` a **LISTEN_PORT**. No hace falta un puerto WebSocket aparte.

La sección obsoleta **`WEBSOCKET_SERVER`** en YAML (Twisted en puerto distinto) se ignora; usa **`MONITOR_APP`**.

---

## `DASHBOARD`

| Clave | Significado |
|-------|-------------|
| **DASHTITLE** | Título de cabecera. |
| **BACKGROUND** | Usar fondo `bk.jpg` si es `true`. |
| **LANGUAGE** | Idioma por defecto de la UI (`en`, `es`, …). |
| **SELF_SERVICE** | Si es `true`, la UI puede mostrar la entrada **Self-service** (API del monitor + MySQL). |
| **SHOW_CONSOLE** | Mostrar página consola (mensajes inicio/fin de llamada). |
| **MIN_DURATION** | Duración mínima de llamada (segundos) para la tabla Last Heard del **panel** (la página Last Heard puede seguir mostrando más cortas). |
| **SERVER_STATUS_URL** | Feed JSON con la lista de servidores de la red (`/serverstatus`), servido por `GET /api/servers/status`. Por defecto `https://adn.systems/servers/status.json`. |
| **nav_links**, **footer**, **news** | Enlaces estructurados / marquee opcionales. |

Los cambios en **`DASHBOARD`** y **`MAP`** se aplican sin reiniciar: `/api/config/dashboard` los reconstruye cuando `adn-monitor.yaml` cambia en disco y mantiene la última versión válida si al guardar el archivo queda roto. Los visitantes los ven al cargar de nuevo la página. Puertos, base de datos e ingest se siguen leyendo solo al arrancar.

---

## `MAP`

Mapas en **Home** (una bandera sobre cada país con una llamada al aire) y en **Linked Systems** (los sistemas conectados). Los marcadores salen de las coordenadas que cada peer envía al iniciar sesión (RPTC); los sistemas que no informan ninguna quedan fuera, o se ubican en su país con `APPROX_BY_COUNTRY`.

| Clave | Significado |
|-------|-------------|
| **ENABLED** | Mostrar ambos mapas (por defecto `true`). |
| **STYLE** | `1` (por defecto) teselas raster desde `TILE_URL`; `2` mundo vectorial: siluetas de países con su código, países activos resaltados, sin proveedor de teselas. |
| **TILE_URL**, **TILE_URL_DARK**, **ATTRIBUTION**, **MAX_ZOOM** | Teselas raster del estilo 1. Por defecto OpenStreetMap, oscurecidas con un filtro CSS en el tema oscuro; `TILE_URL_DARK` usa en su lugar un mapa base oscuro real. Mantén `ATTRIBUTION` como lo pida el proveedor. |
| **ZOOM**, **CENTER** | Vista inicial cuando el visitante no tiene una guardada. `auto` (por defecto) ajusta el mundo completo a cada mapa, en teléfono o escritorio; un número (en cuartos, p. ej. `6` o `7.5`) fija el zoom alrededor de `CENTER` `[lat, lon]`. |
| **HOTSPOT_PRECISION**, **REPEATER_PRECISION** | Decimales que se conservan en las coordenadas enviadas al navegador: `2` es cerca de 1 km, `3` cerca de 100 m, `null` las deja como llegan. Los hotspots usan `2` por defecto, porque suelen estar en la casa de alguien; los repetidores `null`. |
| **APPROX_BY_COUNTRY** | Ubicar en su país (según el prefijo del ID DMR) los sistemas sin coordenadas, con borde punteado y marcados como aproximados (por defecto `true`). |
| **GPS_URL**, **GPS_LABEL**, **GPS_REFRESH_SEC** | Capa extra opcional desde un feed propio (gateway APRS, servicio GPS): puntos GeoJSON, una lista de `{callsign, lat, lon}` o `{"stations": [...]}`. No se ofrece mientras `GPS_URL` esté vacío. |
| **OVERRIDES** | Coordenadas fijas para sistemas que no informan ninguna, como `ID de peer: [lat, lon]`. |

---

## Esquema MySQL y migraciones

No hay Alembic: el monitor usa **`schema_migrations`** y comprobaciones en **`information_schema`**.

| Comando | Cuándo |
|---------|--------|
| `python db_bootstrap.py --config adn-monitor.yaml --create` | BD nueva |
| `python db_bootstrap.py --config adn-monitor.yaml --update` | BD existente (mismas operaciones, idempotente) |

**`monitor.py`** ejecuta **`ensure_schema`** al arrancar si hay **`SELF_SERVICE`**: `CREATE TABLE IF NOT EXISTS`, migraciones pendientes y limpieza de tablas staging huérfanas (`*_import`, `*_old`). **No borra datos** de `subscriber_ids` ni de `Clients`.

**Import masivo de alias (sin bloquear lecturas):**

- Tablas con **PK en `id`** únicamente (lookups puntuales).
- **Replace:** carga en `{tabla}_import` con **commit cada 10 000 filas** (la tabla live sigue legible); swap atómico `RENAME TABLE` (bloqueo metadata breve).
- **Merge** (ficheros locales): `INSERT IGNORE` con **commit cada 2 000 filas**.

Migraciones: `001_clients_callsign`, `002_clients_options_width`, `003_alias_pk_only`, **`004_peer_dynamic_tgs`** (tabla compartida con **adn-server 2.0.0-rc.3+**).

**adn-server** también asegura **`peer_dynamic_tgs`** al arrancar (idempotente). Cualquiera de los dos caminos basta; ambos pueden usar la misma base **`hbmon`**.

---

## Entorno

- **`ADN_CONFIG_PATH`**: ruta absoluta a **`adn-monitor.yaml`** para **`monitor.py`**.
- **`.env`** en la raíz del repo: `VITE_API_BASE`, `VITE_DEFAULT_LANGUAGE` (build del frontend); se carga automáticamente en `monitor.py` y `db_bootstrap.py`.

---

## Ver también

- [Inicio de la documentación](../README.md)
- [Arquitectura](architecture.md)
- [Self-service](self-service.md)
- **`REPORTS`** en el peer server: [Monitor e informes](../server/user-guide/monitoring.md), [Configuración del servidor](../server/user-guide/configuration.md) (sección **`REPORTS`**).
