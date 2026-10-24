# Trunking DMR Tier III (dmrtc + DMRGateway)

Un **MASTER** con **`TRUNKING: true`** acepta **emplazamientos DMR Tier III**. Un emplazamiento es el controlador de trunking **dmrtc** detrás de **DMRGateway** con **`TrunkingEnabled=1`**. Ambos hablan el **protocolo Homebrew DMR trunking v0.6**. Un emplazamiento se conecta como un hotspot, pero con otros nombres de comando; se suscribe a los talkgroups que quiere, registra sus radios y lleva **muchas llamadas a la vez** por un solo enlace.

Formato en el cable: [Protocolo Tier III](../protocols/tier3.md).

## Qué es {#what-it-is}

```text
Radios Tier III
      │ RF
MMDVMHost (uno por canal de RF)
      │
dmrtc (controlador de trunking)
      │
DMRGateway, TrunkingEnabled=1
      │ UDP: login DTC*, DMRD de 71 bytes, señalización DMRT
adn-server: SYSTEMS.TRUNK (MODE: MASTER, TRUNKING: true)
      │ BRIDGES
hotspots, otros MASTER, OpenBridge, otros sistemas Tier III
```

Cada red de DMRGateway que apunta al servidor es un **emplazamiento**: un peer del sistema TRUNKING, con su propio ID.

| | Hotspot (HBP clásico) | Emplazamiento Tier III |
|---|---|---|
| Login | `RPTL` / `RPTK` / `RPTC` / `RPTO`, `RPTPING` | `DTCL` / `DTCK` / `DTCC` / `DTCO`, `DTCPING` |
| Tramas de voz | `DMRD`, 55 bytes | `DMRD`, 71 bytes (55 + un UUID de llamada de 16 bytes) |
| Talkgroups | OPTIONS, estáticos, TG activados por el usuario | **Suscripciones** DMRT, que duran hasta que el emplazamiento las retira o se va |
| Llamadas a la vez | 2 (una por timeslot) | Muchas: sin contención de timeslot |
| Dónde está una radio | Aprendido del tráfico (`SUB_MAP`) | **Registro** DMRT, y después el tráfico |

## Requisitos {#requirements}

- **Un MASTER propio** con `TRUNKING: true`, su propio `PORT` y un `PASSPHRASE` o contraseñas por ID en `GLOBAL.USERS_PASS`.
- **No el proxy integrado.** Un sistema TRUNKING no puede ser `PROXY.TARGET_SYSTEM`, así que el proxy de hotspots y su autoservicio (`SELF_SERVICE`) no se aplican a los emplazamientos.
- **DMRGateway** con `TrunkingEnabled=1` y **dmrtc** con *Use trunking protocol* activado. Un cliente HBP clásico se rechaza en este sistema.
- **IDs de radio de 7 cifras** para llamadas privadas y datos unitarios, como en los hotspots.

## Configuración del servidor {#server-configuration}

El bloque comentado `TRUNK:` de `adn-server.example.yaml` sirve de plantilla:

```yaml
SYSTEMS:
  TRUNK:
    MODE: MASTER
    ENABLED: true
    TRUNKING: true
    IP: ""
    PORT: 62040                 # el Port de [DMR Network n] en DMRGateway
    PASSPHRASE: "cambiame"
    MAX_PEERS: 4                # número de emplazamientos
    REPEAT: true                # llamadas entre emplazamientos de este sistema
    GROUP_HANGTIME: 5
    USE_ACL: true
    REG_ACL: DENY:1             # IDs de emplazamiento
    SUB_ACL: DENY:1             # IDs de radio (registros y voz)
    TGID_TS1_ACL: PERMIT:ALL
    TGID_TS2_ACL: PERMIT:ALL
    DEFAULT_UA_TIMER: 10
    ALLOW_UNREG_ID: false
```

- **`MAX_PEERS`** es el número de emplazamientos que acepta el sistema.
- **`REPEAT`** (por defecto `true`): una llamada de un emplazamiento va a los demás emplazamientos del mismo sistema suscritos a su talkgroup. Con `false`, los emplazamientos de un sistema no se oyen directamente; sus llamadas siguen pasando por los bridges.
- **La autenticación** funciona como con los hotspots: primero la contraseña por ID de `GLOBAL.USERS_PASS` y, si no hay, el `PASSPHRASE` del sistema. `REG_ACL`, `ALLOW_UNREG_ID` y `MAX_PEERS` se aplican a los IDs de emplazamiento.
- **`DEFAULT_UA_TIMER`**: una llamada de un emplazamiento en un talkgroup al que no está suscrito arma una pierna activada por el usuario, como la de un hotspot. Mantén el temporizador bajo (ver [Limitaciones](#limitations)).
- **`ALIASES.TRUNK_REG_FILE`** (por defecto `trunk_registrations.json`, bajo `ALIASES.PATH`): dónde se guardan los registros (ver [Registros](#registrations)).

### Reglas que aplica el validador {#validation}

Con `TRUNKING: true` (activado o no), la configuración se rechaza si encuentra:

| Ajuste | Por qué |
|--------|---------|
| `MODE` distinto de `MASTER` | Los emplazamientos se conectan a un MASTER. |
| El sistema es `PROXY.TARGET_SYSTEM` | El proxy integrado no lleva ni los login DTC* ni DMRT. |
| `GENERATOR` mayor que 1 | Un solo puerto lleva todos los emplazamientos; usa `MAX_PEERS`. |
| `SINGLE_MODE: true` | Un emplazamiento tiene muchos talkgroups a la vez. |
| `TS1_STATIC` / `TS2_STATIC` no vacíos | Los emplazamientos se suscriben a sus propios talkgroups. |
| `DEFAULT_REFLECTOR` distinto de 0 | No se admite en un sistema de trunking. |
| Una clave `OPTIONS` | No se admite en un sistema de trunking. |
| `SUB_MAP_LEARN: false` | Los registros se guardan en `SUB_MAP`. |

Con estas reglas, toda pierna estática de un sistema TRUNKING es la suscripción de un emplazamiento.

### ACL {#acls}

Cada ámbito (`GLOBAL` y después el sistema) se aplica solo si su `USE_ACL` es `true`, igual que para la voz:

- **El registro** de una radio se acepta si `SUB_ACL` permite su ID en todos los ámbitos activos. El ID 0 siempre se rechaza.
- **La suscripción** a un talkgroup se acepta si **tanto** `TGID_TS1_ACL` **como** `TGID_TS2_ACL` lo permiten en todos los ámbitos activos. dmrtc pone el bit de slot de sus tramas de voz a su criterio, y la voz de un emplazamiento se comprueba con la lista de ese slot, así que un talkgroup que deniega un slot se rechaza al suscribirse en lugar de perderse en algunas llamadas.

### Registros {#registrations}

- Un emplazamiento puede tener **1024** radios registradas y **512** talkgroups.
- Un registro dura **24 h**. Registrarse otra vez, o el tráfico de la radio en su emplazamiento, vuelve a empezar las 24 h.
- Una radio está en un solo emplazamiento a la vez. Cuando se registra en otro, el emplazamiento anterior recibe una baja (`0x42`) para ella si está conectado.
- Mientras es válido, el registro es la entrada de la radio en `SUB_MAP`, así que las llamadas privadas y los datos unitarios llegan a su emplazamiento (ver [Llamadas privadas](private-calls.md)). Si después se oye tráfico de la radio en otro sitio, esa entrada se mueve, como con cualquier radio.
- Los registros sobreviven a que el emplazamiento vuelva a conectarse (dmrtc no registra sus radios por segunda vez) y a un reinicio del servidor: se escriben en `ALIASES.TRUNK_REG_FILE` cada 300 s si cambiaron o si falló la última escritura, antes de una recarga con `SIGHUP` y al apagar, en un hilo aparte para que una escritura nunca retrase las llamadas, y se leen una vez, cuando arranca el primer sistema TRUNKING. El fichero se escribe en un `.tmp` y se renombra. Al leerlo se descartan las entradas caducadas y las de sistemas que ya no son TRUNKING.
- Las suscripciones no se guardan: dmrtc se vuelve a suscribir tras cada login.

### Recarga y reinicio {#reload}

- **`SIGHUP`** conserva los emplazamientos, sus suscripciones y sus registros. Un cambio de `MAX_PEERS`, `REPEAT` o las ACL se aplica sin perderlos.
- **Activar o quitar `TRUNKING`** en un sistema existente recrea su protocolo: sus peers se desconectan y vuelven a conectarse.
- **Quitar el sistema** envía `DTCCL` a sus emplazamientos, libera sus talkgroups y olvida sus registros.
- **Un reinicio** empieza sin emplazamientos. DMRGateway vuelve a conectarse solo, dmrtc se vuelve a suscribir y los registros guardados se vuelven a fijar a medida que cada emplazamiento se conecta.

## DMRGateway {#dmrgateway}

`TrunkingEnabled` es una clave de `[General]`, así que se aplica a todas las redes de esa instancia de DMRGateway, y todo mensaje DMRT de dmrtc va a todas las redes activadas. Usa una instancia para el enlace de trunking y activa en ella solo la red del servidor.

```ini
[General]
TrunkingEnabled=1

[DMR Network 1]
Enabled=1
Name=ADN
Address=adn.example.org
# El PORT del sistema TRUNKING
Port=62040
# El ID del emplazamiento: el ID de peer que ve el servidor (uno por emplazamiento)
Id=214901
# El PASSPHRASE del sistema, o la contraseña propia del ID del emplazamiento
Password=cambiame
PassAllTG=1
PassAllTG=2
PassAllPC=1
PassAllPC=2
```

- `PassAllTG` deja pasar todos los talkgroups; `PassAllPC` deja pasar las llamadas privadas, en los dos sentidos.
- Los puertos entre DMRGateway y dmrtc se describen en el manual de dmrtc (`docs/operation_manual.md` en el [repositorio de dmrtc](https://github.com/qradiolink/dmrtc), en inglés).

## dmrtc {#dmrtc}

En los ajustes de dmrtc:

- **Gateways**: la instancia de DMRGateway anterior con tipo de gateway **1** (protocolo de trunking).
- **Use trunking protocol** (`use_trunking_protocol`): activado. Activa la señalización DMRT.
- **Send network registrations** (`send_network_registrations`): activado. Sin esto el servidor no sabe dónde está una radio hasta que transmite.
- **Subscribe static talkgroups** (`subscribe_static_tgs`): activado. dmrtc se suscribe a sus talkgroups estáticos tras cada login.

## Qué funciona {#what-works}

- **Login y keepalive** con `DTCL` / `DTCK` / `DTCC` / `DTCO` y `DTCPING` / `DTCPONG`. El `DTCACK` final hace que DMRGateway avise a dmrtc de que está conectado, y dmrtc envía entonces sus suscripciones.
- **Registros**: persistentes, con tope por emplazamiento y con caducidad (ver [Registros](#registrations)).
- **Suscripciones**, confirmadas talkgroup a talkgroup. Una suscripción no tiene timeslot ni temporizador: dura hasta que el emplazamiento se da de baja o se va. Un talkgroup sin bridge todavía se crea como lo haría uno activado por el usuario.
- **Llamadas de grupo simultáneas en los dos sentidos.** Toda llamada de un talkgroup suscrito llega al emplazamiento a la vez que las demás, sin contención de slot y con un estado de TX por llamada. Las llamadas de un emplazamiento se encaminan una a una: el control de duplicados y pérdidas, el control de bucle, los informes de inicio y fin y la regla de una sola conversación por talkgroup funcionan por llamada.
- **Tramas de 71 bytes** hacia los emplazamientos, con el UUID de la llamada: el propio del emplazamiento de origen si la llamada viene de un emplazamiento; si no, un UUID estable derivado del stream.
- **De emplazamiento a emplazamiento**: en el mismo sistema mediante `REPEAT`; entre sistemas TRUNKING mediante los bridges. Un emplazamiento nunca recibe de vuelta su propia llamada.
- **Llamadas privadas y datos unitarios** hacia una radio registrada (ID de 7 cifras) van solo a su emplazamiento, una vez, nunca en difusión. Desde un emplazamiento llegan a los hotspots y a OpenBridge como siempre.
- **Loro (9990)**: cada emplazamiento oye solo la reproducción de sus propios usuarios, aunque dos emplazamientos lo usen a la vez.
- El talker alias embebido en las tramas de voz llega a los emplazamientos, igual que a los hotspots.

## Limitaciones {#limitations}

- **Las llamadas simultáneas de un emplazamiento vistas desde otros sistemas.** La regla de oro, el control de bucle y el loop guard de los otros sistemas leen la última llamada por bit de slot, como en un MASTER compartido por varios hotspots. Un hotspot que pulse un talkgroup que usa una de varias llamadas simultáneas de un emplazamiento puede no quedar bloqueado.
- **Sin TG 4000 desde los emplazamientos.** El TG 4000 de un emplazamiento se ignora, así que no puede quitarle sus suscripciones; tampoco hay respuesta de voz del TG 4000.
- **No se retransmite desde los emplazamientos**: el talker alias (`DMRA` y el alias embebido en las tramas de voz del emplazamiento), la grabación de voz (`RECORDING_TG`) ni las peticiones de reproducción de ficheros (llamadas privadas a 9991–9999).
- **Talkgroups no suscritos.** Una llamada de un emplazamiento en un talkgroup al que no está suscrito arma una pierna activada por el usuario durante `DEFAULT_UA_TIMER` minutos. El emplazamiento solo oye sus talkgroups suscritos, así que las llamadas encaminadas a esa pierna no llegan a nadie hasta que vence el temporizador.
- **Registros obsoletos.** Una radio que se va mientras el servidor está parado sigue registrada hasta que caduca o se registra en otro emplazamiento. Las llamadas hacia ella se descartan, nunca se difunden.
- **Talkgroups a los que un emplazamiento no puede suscribirse** (`0` en la confirmación): 0–5, 9, 4000, 5000, 9990–9999, cualquiera por encima de 16777215, cualquier talkgroup que deniegue la ACL de uno de los slots, y los que pasen de 512 por emplazamiento.
- **Longitudes estrictas.** La trama de voz de un emplazamiento debe medir exactamente 71 bytes y `DTCC` exactamente 302 (304 en 23 cm, donde las dos frecuencias ocupan diez dígitos). Los formatos siguen a DMRGateway (commit `ee79d38`) y dmrtc (commit `8b51a34`); un cliente que envíe otras longitudes no se entiende.
- **Los datos unitarios hacia un ID sin entrada en `SUB_MAP`** pueden coincidir con el ID de un emplazamiento por sus primeras 6 o 7 cifras y enviarse a ese emplazamiento, como pasa con un hotspot (comportamiento existente).
- **Las sondas de estado del servidor** (`RPTL` con ID 4294967295) se rechazan en un sistema TRUNKING. Apúntalas a otro MASTER.

## Logs que verás {#logs}

Con el sistema `TRUNK`, los emplazamientos 214901 y 214902 y la radio 2141234:

```text
(TRUNK) Repeater Logging in with Radio ID: 214901, 203.0.113.10:62031
(TRUNK) Peer 214901 has completed the login exchange successfully (global passphrase)
(TRUNK) Tier III site 214901 subscribed [214, 2141, 91], refused [9]
(TRUNK) Tier III site 214901 registered [2141234], refused []
(TRUNK) Tier III site 214902 registered [2141234], refused []
(TRUNK) Tier III site 214901 unsubscribed [2141]
(TRUNK) Tier III site 214901 is closing down
(TRUNK) Tier III talkgroup 214 re-armed after a bridge reset
(TRUNK) Writing Tier III registrations to disk
(CONFIG-RELOAD) TRUNK TRUNKING changed: protocol recreated, peers dropped
```

Los mensajes DMRT mal formados o no tratados, `DTCG` y las tramas de longitud incorrecta se registran en `DEBUG`.

## Resolución de problemas {#troubleshooting}

| Síntoma | Causa y solución |
|---------|------------------|
| `Unrecognized command. Raw HBP PDU: b'DTCL...'` | El emplazamiento llegó a un sistema sin `TRUNKING: true`: un `Port` equivocado en DMRGateway, o falta la clave. |
| `classic HBP login refused on a TRUNKING system (DMRGateway needs TrunkingEnabled=1)` | Un hotspot, o un DMRGateway con `TrunkingEnabled=0`, apunta al puerto de trunking. |
| DMRGateway reintenta el login sin parar | `Password` o `Id` incorrectos, `REG_ACL`, un ID no registrado con `ALLOW_UNREG_ID: false`, o `MAX_PEERS` alcanzado. El servidor registra `FAILED the login exchange`, `Invalid Login` o `Maximum number of peers exceeded`. |
| DMRGateway registra `Logged into the master via DTC protocol successfully`, pero el servidor nunca registra `subscribed` | En dmrtc, *Use trunking protocol* o *Subscribe static talkgroups* está desactivado, ningún talkgroup estático está asignado al gateway de trunking, o el tipo de gateway no es 1. |
| `refused [...]` tras `subscribed` | Un talkgroup de la lista de [Limitaciones](#limitations): reservado, denegado por la ACL de un slot, o por encima de 512. |
| Las llamadas privadas a una radio de un emplazamiento no llegan | *Send network registrations* está desactivado y la radio no ha transmitido, o su registro caducó. Busca `registered [...]` con su ID. |
| Los emplazamientos de un mismo sistema no se oyen | `REPEAT: false` en el sistema TRUNKING. |

## Ver también

- [Protocolo Tier III](../protocols/tier3.md): comandos de login, el DMRD de 71 bytes, los mensajes DMRT.
- [Configuración](configuration.md): `TRUNKING` y `ALIASES.TRUNK_REG_FILE`.
- [Llamadas privadas](private-calls.md): `SUB_MAP` y datos unitarios.
- [dmrtc](https://github.com/qradiolink/dmrtc) y [DMRGateway](https://github.com/g4klx/DMRGateway).
