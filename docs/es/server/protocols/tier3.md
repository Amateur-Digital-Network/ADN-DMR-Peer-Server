# DMR Tier III: protocolo Homebrew DMR trunking v0.6

## Función

El servidor habla el **protocolo Homebrew DMR trunking v0.6**, tal como lo implementan **DMRGateway** (`TrunkingEnabled=1`) y **dmrtc**, en cada MASTER con `TRUNKING: true`, y solo ahí. Es HBP con tres cambios:

1. todos los comandos de login y keepalive cambian de nombre (`DTC*`);
2. las tramas `DMRD` llevan un **UUID de llamada** de 16 bytes (71 bytes);
3. un mensaje nuevo, **`DMRT`**, lleva la señalización: registros de radios y suscripciones a talkgroups.

Configuración y comportamiento para operadores: [Trunking DMR Tier III](../user-guide/tier3-trunking.md). Los formatos de bytes siguen a DMRGateway `DMRNetwork.cpp` (commit `ee79d38`) y dmrtc `networksignalling.cpp` / `controller.cpp` (commit `8b51a34`).

## Login y keepalive

Los formatos son los de su gemelo HBP clásico; solo cambia el nombre. Cada comando se compara completo y debe tener exactamente su longitud; un `DTCL`, `DTCK`, `DTCC`, `DTCPING` o `DTCCL` de otra longitud se registra en `DEBUG` y no cambia nada.

| El emplazamiento envía | Longitud | El servidor responde | Gemelo clásico |
|------------------------|----------|----------------------|----------------|
| `DTCL` + ID del emplazamiento (4) | 8 | `DTCACK` + salt (4) | `RPTL` |
| `DTCK` + ID + SHA-256(salt ‖ contraseña) | 40 | `DTCACK` + ID, o `DTCNAK` + ID | `RPTK` |
| `DTCC` + ID + configuración (294) | 302, o 304 en 23 cm (DMRGateway escribe cada frecuencia con `%09u`, así que 1 GHz o más ocupa diez dígitos) | `DTCACK` + ID, o `DTCNAK` + ID | `RPTC` |
| `DTCO` + ID + texto de opciones | cualquiera | `DTCACK` + ID; las opciones se registran en `DEBUG` y nunca se aplican | `RPTO` |
| `DTCPING` + ID | 11 | `DTCPONG` + ID | `RPTPING` / `MSTPONG` |
| `DTCCL` + ID | 9 | nada; el emplazamiento se elimina | `RPTCL` |
| `DTCG` + ID + posición | cualquiera | nada (se ignora) | `RPTG` |

Enviados por el servidor:

| El servidor envía | Cuándo |
|-------------------|--------|
| `DTCNAK` + ID | Un login rechazado: contraseña, `REG_ACL`, un ID no registrado, `MAX_PEERS`, el indicativo. También es la respuesta a un `DTCK`, `DTCC`, `DTCO` o `DTCPING` de un emplazamiento que no está haciendo login ni conectado ahí. |
| `DTCCL` + ID | Se agotó el tiempo del emplazamiento, apagado, o el sistema se quitó o se recreó. |
| `MSTNAK` + ID | La respuesta a todo `RPTL`, `RPTK`, `RPTC`, `RPTO` y `RPTPING` clásicos: el gateway no tiene `TrunkingEnabled=1`, así que no leería un `DTCNAK`. |

- El `DTCACK` **final** (tras `DTCC`) hace que DMRGateway envíe MasterLogin (`0xC1`) a dmrtc, que entonces se suscribe a sus talkgroups; un `DTCCL` hace que le envíe `0xC2`.
- La autenticación es la del MASTER clásico: `REG_ACL`, la contraseña por ID de `GLOBAL.USERS_PASS` antes que `PASSPHRASE`, `MAX_PEERS`, y la comprobación del indicativo cuando `ALLOW_UNREG_ID` es false.
- Un comando `DTC*` en un sistema sin `TRUNKING` se registra como `Unrecognized command`, como antes.

## DMRD (71 bytes)

| Bytes | Campo |
|-------|-------|
| 0–3 | `DMRD` |
| 4 | Secuencia |
| 5–7 | ID de origen |
| 8–10 | Destino (TG o unidad) |
| 11–14 | ID del emplazamiento |
| 15 | Bits: slot (0x80), llamada unitaria (0x40), tipo de trama (0x30), tipo de datos / secuencia de voz (0x0F) |
| 16–19 | ID de stream |
| 20–52 | Carga DMR (33 bytes) |
| 53–54 | BER, RSSI |
| 55–70 | **UUID de llamada** (16 bytes) |

El UUID es la identidad de la llamada en el lado del trunking: dmrtc deriva de él su ID de stream, ignora el bit de slot y lleva muchas llamadas a la vez por un solo enlace. Todas las tramas de una llamada llevan el mismo UUID, y cada llamada tiene el suyo.

**Desde un emplazamiento:**

- Solo se aceptan exactamente 71 bytes, de un emplazamiento conectado y desde su dirección; lo demás se registra en `DEBUG`.
- El UUID se recuerda para el stream, y el enrutado recibe los 55 bytes clásicos: los hotspots reciben 55 bytes y OpenBridge 53, como desde cualquier hotspot.
- Cada stream tiene su propio estado de recepción, así que las llamadas simultáneas con el mismo bit de slot no se molestan entre sí.
- El TG 4000 se ignora.

**Hacia un emplazamiento:** siempre 71 bytes. El servidor escribe el ID del emplazamiento en 11–14, rellena BER / RSSI hasta 55 bytes y añade el UUID del stream: el que envió el emplazamiento de origen o, si no hay, uno estable, el BLAKE2b de 16 bytes de `ADN-TIER3` seguido del ID de stream.

| Trama | Emplazamientos que la reciben |
|-------|-------------------------------|
| Grupo, TG 9990–9999 | Solo el emplazamiento donde ese usuario pulsó el TG |
| Otro grupo | Todos los emplazamientos conectados del sistema suscritos al TG |
| Unitaria (voz privada, datos) | El emplazamiento de la entrada `SUB_MAP` del destino, si está en este sistema; si no, ninguno |

Un emplazamiento nunca recibe de vuelta sus propias tramas, y los datos unitarios llegan una sola vez aunque dos caminos de enrutado los entreguen. `DMRA` (talker alias) no se envía a los emplazamientos.

## DMRT

`DMRT` (4 bytes), el opcode en el byte 4 y después sus campos. Las direcciones son de 24 bits big-endian; las marcas de tiempo, segundos Unix de 64 bits little-endian; un mensaje mide como mucho 255 bytes. El bit `0x40` del opcode marca un mensaje que viene de la red. `DMRT` no lleva ID de emplazamiento: el servidor lo toma de la dirección de origen del datagrama, y solo de un emplazamiento conectado.

| Op | Nombre | Longitud (bytes) | Formato tras el byte 4 | Servidor |
|----|--------|------------------|------------------------|----------|
| `0x01` | Registro | 14 + 3n (n = 1..8) | [5–12] marca de tiempo, [13] n, [14…] IDs de radio | Admitido. Cada radio se acepta salvo que sea 0, que `SUB_ACL` la deniegue o que su emplazamiento tenga ya 1024 radios. Si estaba en otro emplazamiento conectado, ese emplazamiento recibe un `0x42` para ella. Respuesta: un `0x41`. |
| `0x41` | Confirmación de registro | 15 + 3n | [5–12] marca de tiempo, [13] bits de estado (bit i = ID i, 1 = aceptado), [14] n, [15…] IDs | Enviado. |
| `0x02` | Baja | 14 + 3n | como `0x01` | Admitido. Se olvida solo si la radio está registrada en este emplazamiento. Siempre se responde con `0x42` repitiendo los IDs. |
| `0x42` | Baja desde la red | 14 + 3n | como `0x01` | Enviado como respuesta a `0x02`, y al emplazamiento anterior cuando una radio cambia de emplazamiento. |
| `0x03` | Suscripción a grupo | 6 + 3n (n = 1..64) | [5] n, [6…] TG | Admitido. Cada TG se acepta salvo que esté reservado, que la ACL de TG de alguno de los slots lo deniegue o que el emplazamiento tenga ya 512. Respuesta: `0x43` con cada TG, `0` para cada uno rechazado. |
| `0x04` | Baja de grupo | 6 + 3n | como `0x03` | Admitido. Respuesta: `0x44` repitiendo todos los TG. |
| `0x05`–`0x0A` | UDT, aceptación de UDT, establecimiento / aceptación / rechazo de llamada privada, estado | | | Se ignoran. |
| `0x40` activo, `0x80` y superiores | Desde la red; locales del emplazamiento | | | Se ignoran. |

La longitud debe ser exactamente la que construye dmrtc. Un mensaje mal formado o ignorado se registra en `DEBUG` una vez por minuto por emplazamiento y opcode.

## Estado

| Estado | Duración |
|--------|----------|
| Registro (un emplazamiento por radio, en todos los sistemas TRUNKING) | 24 h, que vuelven a empezar con un registro nuevo o con el tráfico de la radio en su emplazamiento; hasta la baja, el cambio de emplazamiento o que se quite el sistema. Se guarda en `ALIASES.TRUNK_REG_FILE`. |
| Talkgroups por emplazamiento | Hasta la baja del grupo o que el emplazamiento se vaya |
| UUID de llamada | 300 s |
| Quién pulsó el loro, por sistema, TG y usuario | 185 s tras la última pasada del usuario |
| Estado de recepción por stream de un emplazamiento | 60 s tras su última trama |

En el enrutado, el primer emplazamiento de un sistema que se suscribe a un TG le da al sistema piernas estáticas y activas en TS1 y TS2; cuando el último se da de baja, las piernas vuelven a ser piernas in-band inactivas. Cada 10 s el servidor vuelve a armar las piernas que desactivó un reset de bridge y caduca los registros antiguos.

## Implementación

| Capa | Módulo |
|------|--------|
| Formato en el cable | `domain/trunking.py` |
| Registros, suscripciones, destinos | `application/trunking/` |
| Talkgroups de un emplazamiento como piernas estáticas | `application/subscription/site_tg_ops.py` |
| MASTER HBP para emplazamientos | `infrastructure/twisted_adapters/udp_hbp_trunk.py` (`TrunkSiteProtocol`) |
| Fichero de registros | `infrastructure/persistence/trunk_registration_store.py` |
| Ensamblado | `infrastructure/bootstrap/trunking.py` |

El enrutado no conoce ningún tipo de Tier III: un emplazamiento pasa a `dmrd_received` el estado de recepción de cada stream (`rx_row`), y `set_concurrent_systems` nombra los sistemas que reciben todas las llamadas a la vez.

## Referencias

- Especificación: `docs/Homebrew_DMR_trunking_protocol_v0.6.pdf` en el [repositorio de dmrtc](https://github.com/qradiolink/dmrtc).
- Cliente: [DMRGateway](https://github.com/g4klx/DMRGateway) `DMRNetwork.cpp`.
- [HBP (DMRD)](hbp.md) para la trama clásica.
