# Configuración

## Ficheros y flujo de trabajo

| Fichero | En el repo | Rol |
|---------|-------------|-----|
| `adn-server.example.yaml` | Sí | Plantilla — copiar a `adn-server.yaml` y editar. |
| `adn-server.yaml` | **No** (gitignored) | Servidor principal: sistemas, globales, logging, alias, informes. **Recarga en caliente** con `SIGHUP` (ver [Configuración](configuration.md#recarga-en-caliente-adn-serveryaml)). |
| `adn-voice.example.yaml` | Sí | Plantilla de voz — copiar a `adn-voice.yaml`. |
| `adn-voice.yaml` | **No** (habitual) | Voz/TTS/grabación; se fusiona en `config["VOICE"]` al arranque y **recarga en caliente** (~cada 15 s) si cambia el fichero. |

Ejecución:

```bash
python adn-server.py -c /ruta/a/adn-server.yaml
```

Opcional: `--logging LEVEL` sobrescribe `LOGGER.LOG_LEVEL`.

Si `adn-voice.yaml` está junto a `adn-server.yaml`, se carga automáticamente. También puedes poner un bloque `VOICE:` dentro de `adn-server.yaml`; el fichero separado es la forma habitual de cambiar anuncios sin tocar la config principal.

### Recarga en caliente (`adn-server.yaml`)

Tras editar la config principal puedes recargar **sin reiniciar** el proceso (se conservan streams de voz activos en listeners UDP que no cambian):

```bash
kill -HUP $(pidof adn-server.py)    # o: systemctl reload adn-server
```

Unidad de ejemplo: **`examples/systemd/adn-server.service`** (copiar a `/etc/systemd/system/`; incluye `ExecReload` para `systemctl reload`).

**Se recarga:** `GLOBAL`, `REPORTS`, `ALIASES`, **`LOGGER.LOG_LEVEL`** (sin reiniciar el proceso), **`PROXY`** (timeouts, debug, listas de bloqueo — no bind ni destino), **`SELF_SERVICE`** (flags PBKDF2 fusionados; activar/desactivar bucles BD requiere reinicio), parámetros por system, **systems nuevos/eliminados** (incluida expansión/colapso `GENERATOR` y OBP nuevos), y cambios de IP/puerto (solo reinicia el listener de ese system).

**No se recarga:** **`DATABASE`** (pool MariaDB y bootstrap `peer_dynamic_tgs`), `adn-voice.yaml` (loop aparte cada 15 s), código Python, ficheros de alias (recarga periódica). La tabla **BRIDGES** no se reconstruye — reinicia si cambiaste reglas de bridge que exijan reset completo. **`PROXY.LISTEN_PORT`**, **`LISTEN_IP`** y **`TARGET_SYSTEM`** requieren **reinicio completo** para aplicarse.

**Secretos:** no versionar passphrases reales, URLs de seguridad ni `user_passwords.json` / `encryption_key.secret`. Usa placeholders en plantillas y mantén producción en local.

---

## Arquitectura: ¿qué es un «sistema»?

Cada entrada bajo **`SYSTEMS`** es un **enlace lógico** con nombre (endpoint UDP) que habla **HBP** (HomeBrew Protocol) con hotspots/repetidores, u **OpenBridge** con otros servidores. Los nombres son libres (`SYSTEM`, `ECHO`, `OBP-UK`, …) y se usan en logs y en la **tabla de bridges** (`BRIDGES`) para identificar por dónde entra o sale el tráfico.

Existen tres **modos**:

| Modo | Uso típico | Escucha | Conecta aguas arriba |
|------|------------|---------|----------------------|
| **MASTER** | Servidor de conferencia para uno o más hotspots/repetidores | **Sí** — `IP` / `PORT`, los peers se registran con passphrase | No (los peers se conectan a ti) |
| **PEER** | Hotspot/repetidor o servicio (p. ej. echo) como **cliente** de un MASTER | **Sí** — `IP` / `PORT` local | **Sí** — `MASTER_IP` / `MASTER_PORT` deben apuntar al MASTER |
| **OPENBRIDGE** | Enlace a otro **servidor** por OpenBridge (DMRD v1 / DMRE) | **Sí** — `IP` / `PORT` | **Sí** — `TARGET_IP` / `TARGET_PORT` (servidor par) |

**MASTER** mantiene la tabla **`PEERS`** en tiempo de ejecución (hotspots autenticados). **PEER** mantiene **STATS** (conexión, pings). **OPENBRIDGE** usa **NETWORK_ID**, **PASSPHRASE**, **TARGET_***, **PROTO_VER** / **VER**, y opcionalmente **ENHANCED_OBP**, **RELAX_CHECKS**, **TGID_ACL**.

Un solo proceso puede ejecutar **varios** sistemas a la vez (p. ej. un MASTER para usuarios, un ECHO para playback, un OBP hacia una red asociada).

---

## `GLOBAL`

Valores por defecto de todo el servidor. Muchas claves pueden sobrescribirse por sistema si `USE_ACL` (o similar) está activo en ese sistema.

| Clave | Significado |
|-------|-------------|
| **PING_TIME** | Intervalo (segundos) para ping / keepalive del PEER hacia MASTER. |
| **MAX_MISSED** | Cuántos pings perdidos antes de considerar el enlace PEER no saludable (depende de la implementación con STATS). |
| **USE_ACL** | Si es true, se aplican **REG_ACL**, **SUB_ACL**, **TGID_TS1_ACL**, **TGID_TS2_ACL** (tras procesarlas en tuplas internas). |
| **REG_ACL** | Control de acceso para **registro** / IDs de peer (`PERMIT:…` / `DENY:…`; ver [Cadenas ACL](#cadenas-acl)). |
| **SUB_ACL** | ACL para IDs de **suscriptor** (radio) en tráfico recibido. |
| **TGID_TS1_ACL** | ACL para **talkgroup** en **slot temporal 1**. |
| **TGID_TS2_ACL** | ACL para **talkgroup** en **slot temporal 2**. |
| **GEN_STAT_BRIDGES** | Si es true, OpenBridge puede disparar filas de bridges **estáticos** para ciertas TG (ver [Bridges y talkgroups](bridges-and-talkgroups.md)). |
| **SERVER_ID** | ID numérico del servidor; valor de 4 bytes para OpenBridge / metadatos de voz. |
| **VALIDATE_SERVER_IDS** | Si es true (ruta DMRE), los IDs de **servidor de origen** pueden comprobarse contra una lista descargada (`ALIASES` **SERVER_ID_**\*). |
| **LOOP_GUARD_HOLD** | Segundos tras el fin de una pasada durante los que la misma persona en el mismo TG desde otra entrada sigue contando como eco de un bucle (por defecto `2.0`). Ver [Loop guard](../development/behaviour-and-timers.md#loop-guard). |
| **URL_SECURITY** / **PORT_SECURITY** / **PASS_SECURITY** | Si están definidos, habilitan descarga de claves/contraseñas desde el endpoint de seguridad (ver comentarios del ejemplo). Vacío = desactivado. |
| **USERS_PASS** | Nombre de fichero JSON de contraseñas por radio (opcional). |
| **HASH_ENCRYPT** | Ruta a la clave de cifrado para el manejo del fichero de contraseñas. |

### Talker Alias (`GLOBAL`)

Talker Alias DMR opcional en HBP (paquetes `DMRA`). Guía completa: [Talker Alias](talker-alias.md).

| Clave | Significado |
|-------|-------------|
| **TALKER_ALIAS** | Activa inyección/passthrough de TA (`false` por defecto). |
| **TALKER_ALIAS_MODE** | `both` (por defecto), `passthrough` o `inject`. |
| **TALKER_ALIAS_FORMAT** | Plantilla, p. ej. `{callsign} {fname}`. Máx. **29** caracteres (límite de protocolo, no YAML). |
| **TALKER_ALIAS_SEND_DMRA** | Enviar `DMRA` UDP autónomo (`false` por defecto; las radios usan LC embebida). |
| **TALKER_ALIAS_TEXT_FORMAT** | `utf8`, `iso8`, `7bit` o lista con comas (p. ej. `utf8,iso8`). Por defecto `utf8`. |

---

## `SYSTEMS` — campos comunes

Aparecen principalmente en **MASTER** (y a menudo en **PEER**). OpenBridge usa un subconjunto distinto.

| Clave | Significado |
|-------|-------------|
| **MODE** | `MASTER`, `PEER` u `OPENBRIDGE`. |
| **ENABLED** | Si es `false`, el sistema se omite. |
| **IP** / **PORT** | Dirección UDP de escucha para el listener HBP u OpenBridge de este sistema. |
| **PASSPHRASE** | Secreto compartido para autenticación HBP (MASTER ↔ PEER). Debe coincidir entre PEER y su MASTER. |
| **USE_ACL** | Sobrescribe ACL por sistema si es true (usa `REG_ACL` / `SUB_ACL` / `TGID_TS*_ACL` del sistema). |
| **GROUP_HANGTIME** | Tiempo de hang (segundos) para el estado de voz de grupo. |
| **DEFAULT_UA_TIMER** | Tiempo de espera por defecto (minutos en muchos sitios) para bridges **activados por usuario**. |
| **ANNOUNCEMENT_LANGUAGE** | Carpeta de idioma por defecto bajo `Audio/<lang>/` para mensajes en este sistema. |
| **ALLOW_UNREG_ID** | Si se permiten IDs de suscriptor no registrados (MASTER). |
| **SUB_MAP_LEARN** | Por defecto `true`. Con `false`, el tráfico que entra por este sistema nunca actualiza **SUB_MAP**, así que nunca pasa a ser donde se localiza a un suscriptor para llamadas privadas y datos unitarios. Para sistemas de servicio que transmiten con el ID de otra persona o con un ID de servicio compartido: balizas, puentes ASL / EchoLink / DVSwitch, el loro **ECHO**. Solo MASTER y PEER; en OPENBRIDGE se rechaza. Ver [Llamadas privadas](private-calls.md#service-systems-sub_map_learn). |
| **LOOP_GUARD** | `log` (por defecto), `true` o `false`. Protección contra bucles de la voz que vuelve por un puente que transcodifica (YSF2DMR, DVSwitch, pasarelas ASL/EchoLink): la misma persona en el mismo TG desde otra entrada mientras su pasada original sigue activa o dentro de `GLOBAL.LOOP_GUARD_HOLD`. `log` escribe una línea `*LoopGuard*` por eco y lo deja pasar; `true` lo descarta; `false` ni comprueba ni registra los streams que entran por este sistema. Pon `false` en el loro **ECHO**, que repite cada pasada con el ID de quien llama unos 2 s después. También se aplica a OPENBRIDGE. Ver [Loop guard](../development/behaviour-and-timers.md#loop-guard). |

---

## `SYSTEMS` — MASTER

| Clave | Significado |
|-------|-------------|
| **REPEAT** | Si es true, el tráfico recibido puede **repetirse** a otros peers conectados al MASTER (comportamiento típico de conferencia). |
| **MAX_PEERS** | Máximo de hotspots conectados. En el MASTER **destino del proxy**, limita sesiones fan-in simultáneas. |
| **EXPORT_AMBE** | Flag de exportación AMBE (si está habilitado en el build). |
| **SINGLE_MODE** | Afecta a OPTIONS / expansión del generador (estilo un solo usuario). |
| **VOICE_IDENT** | Habilita **identificación por voz** periódica cuando se cumplen condiciones (ver `IdentUseCases`). |
| **TS1_STATIC** / **TS2_STATIC** | Listas estáticas de TG separadas por comas, enviadas vía manejo OPTIONS (ver `options_config`). |
| **DEFAULT_REFLECTOR** | Número de **reflector** por defecto para bridges de marcado `#` (0 = ninguno). |
| **OVERRIDE_IDENT_TG** | TG opcional para ident por voz en lugar de all-call. |
| **GENERATOR** | Si es **> 1**, este MASTER se expande en **`NAME-0`**, **`NAME-1`**, … con puertos consecutivos (ver `expand_generator` en código). El **`adn-proxy`** independiente legado usaba el mismo rango; el proxy **integrado** usa **`PROXY.TARGET_SYSTEM`** solo inyección (sin puertos UDP por hotspot en el servidor). |

**MASTER** escucha conexiones PEER (salvo que sea el destino **solo inyección** del proxy — ver [Proxy hotspot](hotspot-proxy.md)); cada peer autenticado se guarda en **`PEERS`** en tiempo de ejecución.

---

## `SYSTEMS` — PEER

Un **PEER** conecta **saliente** hacia un **MASTER** y escucha localmente para la radio o la app.

| Clave | Significado |
|-------|-------------|
| **MASTER_IP** / **MASTER_PORT** | Dirección del **MASTER** con el que registrarse (debe coincidir con `IP`/`PORT` de ese MASTER). |
| **RADIO_ID** | ID de este peer en HBP (4 bytes). |
| **CALLSIGN**, **RX_FREQ**, **TX_FREQ**, **COLORCODE**, **LATITUDE**, … | Campos del payload RPT enviados al MASTER en el registro (anchos fijos en protocolo). |
| **OPTIONS** | Cadena / línea de opciones (p. ej. `TS2=9990;`) para TG estáticas / comportamiento. |
| **LOOSE** | Flag de manejo relajado donde aplique. |

El ejemplo **echo** (`adn-echo.example.yaml`) es un PEER que se une al MASTER **ECHO**: mismo **PASSPHRASE**, **MASTER_PORT** = **PORT** del ECHO. Ver [Echo](echo.md).

---

## `SYSTEMS` — OPENBRIDGE

| Clave | Significado |
|-------|-------------|
| **NETWORK_ID** | Debe coincidir con el **NETWORK_ID** del par en paquetes OpenBridge. |
| **TARGET_IP** / **TARGET_PORT** | Par OpenBridge remoto (UDP). |
| **TGID_ACL** (o **TG1_ACL**) | ACL de talkgroup para OpenBridge (a menudo estilo `DENY:0-82,…` por rangos). |
| **RELAX_CHECKS** | Permitir paquetes cuando el socket del par no coincide estrictamente con `TARGET` (usar con cuidado). |
| **ENHANCED_OBP** | Habilita **BCSQ** / **BCKA** y control de bucle multipath — **debería ser `true`** en enlaces inter-servidor ADN Systems (ver abajo). |
| **PROTO_VER** | Versión de protocolo **DMRE** embebida; **`5`** selecciona **DMRE / OpenBridge v5** (trama de 89 bytes, BLAKE2b). El valor por defecto en código es **5**; usa **5** en nuevos despliegues ADN. |

**Recomendación ADN Systems:** para cada par OpenBridge en la malla ADN, configura **`PROTO_VER: 5`** (DMRE v5) y **`ENHANCED_OBP: true`**. Alinea la misma configuración en **ambos** extremos. Los pares solo **DMRD** v1 siguen siendo posibles por compatibilidad, pero no son el modo preferido para la red.

Filtros de ingreso y control de bucle: [Protocolo OpenBridge](../protocols/openbridge.md) (incl. [DMRE frente a OpenBridge v5](../protocols/openbridge.md#dmre-and-openbridge-v5)) y [Números especiales — ingreso OpenBridge](special-numbers.md#openbridge-ingress--group-tg-filters).

El UDP entrante de todos los OPENBRIDGE puede usar el fan-in compartido **`OBP_PROXY`** (puerto estándar **62032**). Ver [Proxy OBP](obp-proxy.md).

---

## `OBP_PROXY` (fan-in OpenBridge integrado)

Activo cuando existe **`OBP_PROXY`** o algún sistema **OPENBRIDGE** (se aplican defaults). Todo el UDP OBP entrante se demultiplexa en **`LISTEN_PORT`** (default **62032**); los listeners legacy por bridge son opcionales. Guía completa: [Proxy OBP](obp-proxy.md).

| Clave | Significado |
|-------|-------------|
| **LISTEN_PORT** / **LISTEN_IP** | Bind UDP del fan-in OpenBridge (pareja del **`PROXY`** hotspot **62031**). |
| **BIND_LEGACY_PORTS** | Con `true`, también escucha cada **`SYSTEMS.*.PORT`** distinto de **`LISTEN_PORT`** (migración bridge a bridge). |
| **ENABLED** | `false` restaura bind UDP por bridge (modo legacy). |

---

## `BRIDGES` (tiempo de ejecución)

La tabla de bridges asocia claves TG con filas de enrutado. Está **en memoria** en el proceso en ejecución.

El cargador YAML **no** carga un bloque `BRIDGES:` de nivel superior desde `adn-server.yaml` en el router hoy — las filas iniciales se crean en código (p. ej. arranque **9990 / ECHO** cuando existe un sistema **ECHO**), luego **OPTIONS**, bridges **activados por usuario**, bridges **estáticos** y la lógica **OpenBridge** añaden filas con el tiempo.

Para el modelo conceptual (ACTIVE, TS, TGID, timeouts): [Bridges y talkgroups](bridges-and-talkgroups.md).

---

## `REPORTS`

Canal TCP de informes para **adn-monitor** (o paneles compatibles). **adn-server 2.x** habla **solo informe v2**; los paneles legacy que esperan **v1** necesitan el opcional [report-proxy](report-proxy.md) (paquete aparte).

| Clave | Significado |
|-------|-------------|
| **REPORT** | Activar/desactivar envío. |
| **REPORT_INTERVAL** | Intervalo de envío periódico (segundos). |
| **REPORT_PORT** | Puerto local en el que el **servidor escucha** clientes de informes. |
| **REPORT_CLIENTS** | Lista separada por comas o lista de IPs de clientes permitidos (ver ejemplo). |

Detalle: [Monitor e informes](monitoring.md). Monitores legacy v1: [Proxy de informes](report-proxy.md).

---

## `PROXY` (proxy hotspot integrado)

Se arranca siempre que exista un bloque **`PROXY`** (ver `adn-server.example.yaml`). Los hotspots se conectan a **`LISTEN_PORT`**; el tráfico se inyecta en **`TARGET_SYSTEM`**. Guía completa: [Proxy hotspot](hotspot-proxy.md).

| Clave | Significado |
|-------|-------------|
| **LISTEN_PORT** / **LISTEN_IP** | Bind UDP para conexiones de hotspots. |
| **TARGET_SYSTEM** | Nombre del **MASTER** que recibe HBP inyectado. Ese system pasa a **solo inyección** (`IP` / `PORT` eliminados al cargar). |
| **TIMEOUT** | Timeout de sesión inactiva (segundos). |
| **DEBUG** / **CLIENT_INFO** | Verbosidad de logs. |
| **BLACK_LIST** / **IP_BLACK_LIST** | Bloqueo de IDs de radio o IPs de cliente. |

**No** ejecutes **`adn-proxy`** independiente en el mismo **`LISTEN_PORT`** si el proxy integrado está activo.

---

## `DATABASE` (MariaDB)

**Obligatorio** en configs típicas de servidor de conferencia: cualquier despliegue con **`PROXY`**, o al menos un system **`MASTER`** / **`OPENBRIDGE`**. **No** es obligatorio en flotas **solo echo** (`adn-server.py --echo`).

| Clave | Significado |
|-------|-------------|
| **DB_SERVER** | Host MariaDB/MySQL. |
| **DB_USERNAME** / **DB_PASSWORD** | Credenciales. |
| **DB_NAME** | Nombre de la base (a menudo la misma que **adn-monitor**, p. ej. `hbmon`). |
| **DB_PORT** | Puerto TCP (por defecto **3306**). |

**Un solo pool** compartido para:

- **Persistencia de TG dinámicos** — tabla **`peer_dynamic_tgs`** (TG activados por usuario por hotspot entre reconexiones). El servidor **crea la tabla al arranque** si falta (migración **`004_peer_dynamic_tgs`**, mismo esquema que adn-monitor).
- **Self-service integrado** — tabla **`Clients`** con **`SELF_SERVICE.USE_SELFSERVICE: true`**.

El arranque aborta con log claro si MariaDB no responde o **`DATABASE`** está incompleto. Instala **`mysqlclient`** (`pip install -e ".[selfservice]"` lo incluye).

**Recarga en caliente:** cambiar **`DATABASE`** exige **reinicio completo** del proceso.

Detalle: [Bridges y talkgroups — persistencia TG dinámicos](bridges-and-talkgroups.md#persistencia-tg-dinamicos-mariadb).

---

## `SELF_SERVICE` (MySQL / opciones del panel)

Opcional; requiere `pip install -e ".[selfservice]"` con **`USE_SELFSERVICE: true`**. Usa el bloque **`DATABASE`** anterior (sin claves DB separadas en **`SELF_SERVICE`**). Los parámetros PBKDF2 deben **coincidir** con **adn-monitor**. Ver [Self-service](../../monitor/self-service.md) y [Proxy hotspot](hotspot-proxy.md#claves-self_service).

| Clave | Significado |
|-------|-------------|
| **USE_SELFSERVICE** | Activa sincronización de opciones desde el panel (`true` / `false`). |
| **PBKDF2_SALT** / **PBKDF2_ITERATIONS** | Deben coincidir con **`adn-monitor.yaml`** / herramienta de contraseñas. |

---

## `LOGGER`

Implementado en `infrastructure/logging_config.py` (`setup_logging`). Los valores se leen del bloque **`LOGGER`** (o `--logging` solo para **LOG_LEVEL**).

| Clave | Significado |
|-------|-------------|
| **ENABLED** | **`true`** (por defecto si se omite) — logging normal. **`false`** — desactiva la salida de logs de la aplicación (sin consola ni fichero; solo `NullHandler`). Las configs antiguas sin esta clave no cambian. |
| **LOG_HANDLERS** | Lista separada por comas de **tokens** de manejador (espacios alrededor de las comas están bien). Cada token elige salidas; puedes combinar varios. Valores reconocidos: **`console-timed`** o **`console`** — log a **stderr** con formato `LEVEL asctime message`; **`file-timed`** o **`file`** — log a **LOG_FILE** con el mismo formato (UTF-8). **Por defecto** si falta: `console-timed`. Ejemplos: solo `console-timed`; solo `file-timed`; `console-timed,file-timed` para consola y fichero. |
| **LOG_FILE** | Ruta usada cuando **`file-timed`** o **`file`** está en **LOG_HANDLERS**. Si falta, el código usa por defecto `/dev/null`. Si la ruta es **`/dev/null`**, los manejadores de fichero **no** se adjuntan aunque estén listados. Si no se puede abrir el fichero (permisos, directorio inexistente), se escribe un aviso a stderr y el logging continúa sin ese manejador de fichero. |
| **LOG_LEVEL** | Nivel del logger raíz: **`DEBUG`**, **`INFO`**, **`WARNING`**, **`ERROR`**, **`CRITICAL`** (sin distinguir mayúsculas; por defecto **INFO**). Nombres desconocidos caen en **INFO**. Puedes sobrescribir al arranque con **`python adn-server.py --logging LEVEL`** (mismos nombres). Hay un nivel personalizado **`TRACE`** registrado para llamadas ocasionales `logger.trace(...)`; usa **`DEBUG`** para diagnóstico detallado en operación normal. |
| **LOG_NAME** | Nombre del logger devuelto a la aplicación (por defecto **`ADN`**). No cambia la lista de manejadores; selecciona qué logger nombrado recibe el nivel configurado. |

---

## `ALIASES`

Descargas y ficheros locales para **IDs de peer**, **IDs de suscriptor**, **etiquetas de talkgroup**, lista opcional de **IDs de servidor**, **checksums** y **claves**. Usados en paneles, validación y descargas de seguridad opcionales.

| Clave | Significado |
|-------|-------------|
| **PATH** | Directorio base para JSON/TSV/pickle. |
| **TRY_DOWNLOAD** | Si se deben obtener desde URLs cuando están obsoletos. |
| **PEER_FILE** / **SUBSCRIBER_FILE** / **TGID_FILE** | Nombres de ficheros locales. |
| **\*_URL** | Orígenes remotos para descargas. |
| **SUB_MAP_FILE** | Ruta pickle para **SUB_MAP** (enrutado de llamadas privadas); nombre por defecto si está vacío. |
| **STALE_DAYS** | Umbral de refresco para descargas. |

---

## `VOICE` (desde `adn-voice.yaml` o inline)

Fusionado en `config["VOICE"]`. Ver [Voz, anuncios y TTS](voice-and-tts.md) y `adn-voice.example.yaml`.

---

## Cadenas ACL

Procesadas por `acl_build`: `PERMIT:` o `DENY:` seguido de IDs o rangos separados por comas.

Ejemplos:

- `PERMIT:ALL` — permitir todos los IDs en rango.
- `DENY:1` — denegar solo el ID 1.
- `DENY:0-82,9990-9999` — denegar los rangos listados.

Las ACL globales aplican cuando `USE_ACL` es true; OpenBridge puede usar **TGID_ACL** en el sistema OBP.

---

## Entorno Python

Usa el intérprete del proyecto (ver reglas del workspace), p. ej. `python3.11` de pyenv, para un comportamiento alineado con producción.

---

## Ver también

- [Introducción](introduction.md) — rol del servidor.
- [Bridges y talkgroups](bridges-and-talkgroups.md) — semántica de `BRIDGES`.
- [Números especiales](special-numbers.md) — TG e IDs reservados.
- [Echo](echo.md) — ejemplo PEER (proceso echo).
- [Proxy hotspot](hotspot-proxy.md) — **`PROXY`** / **`SELF_SERVICE`** integrados.
- [Proxy OBP](obp-proxy.md) — fan-in OpenBridge **`OBP_PROXY`** integrado.
