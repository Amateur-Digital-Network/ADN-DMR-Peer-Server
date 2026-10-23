# Talker Alias (DMR)

El **Talker Alias** DMR (ETSI TS 102 361-2, 2016) transporta una etiqueta alfanumérica corta en el flujo de voz. En el protocolo Homebrew (HBP) aparece como paquetes UDP **`DMRA`** (15 bytes cada uno, hasta cuatro por transmisión).

**No** es lo mismo que los alias de IDs en `subscriber_ids.json` usados en logs y en el monitor. Talker Alias es señalización embebida para **pantallas de radio** (OLED, Hytera/MD380tools, etc.).

Ver también: [Configuración](configuration.md#talker-alias-global).

---

## Cómo lo gestiona ADN

Con la función activada en un sistema **MASTER**, el servidor puede:

| Modo | Comportamiento |
|------|----------------|
| **`both`** (por defecto) | Passthrough del TA del hotspot/radio origen si se recibieron los cuatro bloques `DMRA`; si no, inyección desde `subscriber_ids` + plantilla. |
| **`passthrough`** | Solo reenvía `DMRA` bufferizado. |
| **`inject`** | Siempre genera TA desde la plantilla y datos de alias. |

En el **reenvío por bridge** en cabecera de voz (`VHEAD`), si **`TALKER_ALIAS_SEND_DMRA`** es `true`, el servidor puede enviar hasta cuatro paquetes `DMRA` a cada destino HBP una vez por stream. Con el valor por defecto (`false`) solo se usa la LC embebida — más parecido a masters legacy que a menudo no muestran `DMRA` en el cable.

**MMDVMHost / DMRGateway (Pi-Star, WPSD):** el MMDVMHost estándar **no** procesa `DMRA` UDP independiente en downlink; decodifica Talker Alias desde la **LC embebida en voz `DMRD`** (FLCO 4–7). Con TA activado, ADN inyecta el TA en la LC embebida de los bursts **B–E** (dtype 1–4) en el **reenvío por bridge** hacia destinos HBP, alternando LC de grupo y bloques TA durante todo el stream; los paquetes `DMRA` son opcionales (`TALKER_ALIAS_SEND_DMRA`).

En el **mismo MASTER**, cuando **`REPEAT`** copia voz de grupo a otros hotspots registrados, el servidor también envía esos cuatro `DMRA` en `VHEAD` (excluyendo el peer que transmite). Si el bridge apunta al mismo system, comparten la misma deduplicación por stream y el TA no se envía dos veces.

**No soportado:** tramos OpenBridge (no hay `DMRA` estándar en OBP/DMRE). El ADN legacy nunca implementó TA más allá de logs de depuración.

---

## Configuración

Bajo **`GLOBAL`** (opcional override por sistema con las mismas claves):

```yaml
GLOBAL:
  TALKER_ALIAS: true
  TALKER_ALIAS_MODE: both
  TALKER_ALIAS_FORMAT: "{callsign} {fname}"
  TALKER_ALIAS_TEXT_FORMAT: "utf8,iso8"
  TALKER_ALIAS_SEND_DMRA: false
```

| Clave | Significado |
|-------|-------------|
| **TALKER_ALIAS** | Interruptor maestro (`false` si se omite; la config de ejemplo lo activa). |
| **TALKER_ALIAS_MODE** | `both`, `passthrough` o `inject`. Por defecto **`both`** si se omite. |
| **TALKER_ALIAS_FORMAT** | Plantilla tipo Python; campos: `{callsign}`, `{fname}`, `{surname}`, `{id}`. |
| **TALKER_ALIAS_TEXT_FORMAT** | Codificación del TA: `utf8` (Motorola), `iso8` (Hytera), `7bit`. Lista separada por comas (p. ej. `utf8,iso8`) emite ambas en LC embebido; `DMRA` UDP usa solo el **primer** formato. Por defecto **`utf8`**. |
| **TALKER_ALIAS_SEND_DMRA** | Enviar `DMRA` UDP autónomo en VHEAD (`false` por defecto). Las radios / MMDVMHost usan la **LC embebida** en `DMRD`; dejarlo en off se acerca al cable legacy (sin `DMRA` hacia el peer). Pon `true` solo si un cliente necesita esos paquetes UDP. |

La longitud máxima es **29 caracteres** (ETSI / MMDVMHost). Este límite está fijado en código y **no** es configurable, para evitar payloads incompatibles en radios y hotspots.

El JSON de suscriptores puede incluir `fname`, `surname` o un campo `talker_alias` por registro.

---

## Formato HBP `DMRA`

| Offset | Campo |
|--------|--------|
| 0–3 | `DMRA` |
| 4–6 | ID DMR origen (3 bytes, big-endian) |
| 7 | Índice de bloque 0–3 |
| 8–14 | 7 bytes de payload |

La codificación usa **formato UTF-8** (formato 2), igual que MMDVMHost `DMRTA.cpp`.

El TA embebido en voz `DMRD` alterna supertramas: un ciclo (bursts B–E) con la LC de grupo normal y el siguiente con un bloque TA (FLCO 4–7), repitiendo hasta el fin del stream.

---

## Ajustes Pi-Star / MMDVM

Para que el TA llegue a la **radio RF local**:

| Ajuste | Recomendación |
|--------|----------------|
| **DMR DumpTAData** | `1` (activado, valor por defecto en Pi-Star): escribe el Talker Alias embebido en el log MMDVM — necesario para que el dashboard de Pi-Star y herramientas basadas en log muestren TA; con `0` no aparece nada ahí. **No** bloquea el TA hacia RF. |
| **DMR EmbeddedLCOnly** | `off` (por defecto). Si está `on`, se desactiva el Talker Alias recibido de la red. |

El panel web de Pi-Star no muestra TA; use OLED de la radio, MD380tools o herramientas como [pistar-lastqso](https://github.com/kencormack/pistar-lastqso).

**Compatibilidad de radios:** Hytera PD6/7/9 y firmware MD380tools suelen funcionar. Muchas Motorola no soportan TA; firmware antiguo puede dar problemas de audio con TA presente.

El **proxy adn-monitor** ya reenvía `DMRA` al master sin modificar.

---

## Limitaciones y trabajo pendiente

| Tema | Estado |
|------|--------|
| TA en destinos **OpenBridge** | No disponible (sin `DMRA` estándar en OBP). |
| Columna TA en vivo en el **monitor** | Trabajo aparte (el monitor usa alias de BD, no `DMRA` en vivo). |
| **`RuntimeError`** no relacionado en iteración de bridges | Fix en rama separada. |

---

## Referencias

- [ETSI TS 102 361-2](http://www.etsi.org/deliver/etsi_ts/102300_102399/10236102/02.03.01_60/ts_10236102v020301p.pdf)
- [MMDVMHost `DMRTA.cpp`](https://github.com/g4klx/MMDVMHost/blob/master/DMRTA.cpp) / [`DMRNetwork.cpp`](https://github.com/g4klx/MMDVMHost/blob/master/DMRNetwork.cpp)
