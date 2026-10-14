# Enrutado de voz y reglas de contención

Esta página documenta **cómo viajan los paquetes de voz por el servidor** y las
**reglas de contención / slot** que determinan quién escucha qué. Es la
referencia para sysops e integradores que necesitan entender o diagnosticar el
comportamiento de enrutado sin leer el código fuente.

El servidor mantiene **paridad legada** con `adn-dmr-server` (`bridge_master.py`)
salvo las divergencias explícitamente documentadas al final de esta página.

---

## Regla de oro: una conversación por TG

**Sólo una conversación por TG puede existir en el servidor a la vez.** Esta
regla es **global** — aplica sin importar el slot, el peer o el origen del
tráfico (OBP o HBP). Tiene la prioridad más alta; todas las demás reglas se
subordinan a ella.

Un TG está **ocupado** cuando tiene un stream de voz **activo** (frames dentro
de `STREAM_TO` del último paquete) en cualquier slot, venga de donde venga.

| Origen del nuevo tráfico | Comportamiento |
|---|---|
| Otro **OBP** envía el mismo TG | **Rechazar** — el TG ya está ocupado |
| Un **hotspot (HBP)** transmite al mismo TG | **Rechazar**, *o* activación silenciosa (ver abajo) |

La única excepción es la **activación silenciosa**, que **no** crea una segunda
conversación — sólo permite al usuario escuchar la existente.

---

## Flujo de paquetes de extremo a extremo

```mermaid
flowchart TD
    PTT[Usuario pulsa PTT<br/>en TG 730500 slot 2] --> HBP[Hotspot envía DMRD<br/>por UDP HBP al PROXY]
    HBP --> INJ[PROXY inyecta en MASTER 'SYSTEM']
    INJ --> AUTH{Auth HBP OK?}
    AUTH -->|No| DROP1[Drop: peer no autenticado]
    AUTH -->|Sí| ACL{ACL de SUB/TG<br/>permite?}
    ACL -->|No| DROP2[Drop: ACL]
    ACL -->|Sí| DMRD_RX[dmrd_received<br/>actualiza STATUS slot]
    DMRD_RX --> CONT{Contención global:<br/>TG ocupado?}
    CONT -->|Ocupado OBP/HBP| SILENT{Activación silenciosa?<br/>ver abajo}
    CONT -->|Libre| SRC[source row ACTIVE?<br/>bridge leg]
    SRC -->|No| NODRV[No hay bridge leg<br/>ver: TG dinámico]
    SRC -->|Sí| FWD[forward a to_target]
    NODRV --> FWD
    FWD --> FANOUT[Iterar peers destino]
    FANOUT --> P1{Para cada peer:<br/>¿OPTIONS tiene TG?}
    P1 -->|Sí o dinámico activo| SLOT{¿Slot destino<br/>libre?}
    P1 -->|No| SKIP[Skip peer]
    SLOT -->|Ocupado| SKIP
    SLOT -->|Libre| REWRITE[Rewrite LC<br/>+ slot mapping]
    REWRITE --> TX[Enviar DMRD<br/>al peer en su slot]
    TX --> NEXT[Próximo peer / frame]

    style DMRD_RX fill:#bfb,stroke:#333
    style FWD fill:#bbf,stroke:#333
    style TX fill:#fbb,stroke:#333
```

### Puntos de decisión en orden

1. **Autenticación HBP** — el peer debe estar registrado con passphrase válida.
2. **ACL** — `SUB_ACL`, `TGID_TS1_ACL`, `TGID_TS2_ACL` (si `USE_ACL`).
3. **`dmrd_received`** — actualiza `STATUS[slot]`: `RX_TIME`, `RX_TGID`,
   `RX_STREAM_ID`, `RX_TYPE`, `RX_PEER`.
4. **Contención global** — si el TG ya tiene un stream activo desde otra
   fuente, se bloquea o activa silenciosamente.
5. **Source row ACTIVE** — debe existir una pata de bridge `ACTIVE` para ese
   `system/slot/TG`. Si no existe, se crea una dinámica.
6. **Fan-out** — para cada peer del MASTER, el gate de downlink decide si
   recibe el paquete.
7. **Rewrite LC + slot mapping** — el slot destino es el del peer, no el de
   origen.

---

## Las cuatro reglas de contención (paridad legada)

Evaluadas **paquete a paquete** en `to_target` contra el `STATUS[slot]` del
sistema destino. Coinciden con `bridge_master.py` líneas ~2076–2104.

| Regla | Condición | Acción |
|---|---|---|
| **1. RX hangtime** | TG ≠ `RX_TGID` **y** `(now - RX_TIME) < GROUP_HANGTIME` | `continue` (no enruta) |
| **2. TX hangtime** | TG ≠ `TX_TGID` **y** `(now - TX_TIME) < GROUP_HANGTIME` | `continue` |
| **3. mismo TG RX activo** | TG == `RX_TGID` **y** `(now - RX_TIME) < STREAM_TO` **y** stream distinto | `continue` |
| **4. mismo TG TX, otro sub** | TG == `TX_TGID` **y** `(now - TX_TIME) < STREAM_TO` **y** otro suscriptor | `continue` |

Hechos clave:

- `RX_TIME`, `RX_TGID`, `TX_TIME`, `TX_TGID` **no se borran en VTERM**.
  Conservan el último valor hasta que otro QSO los sobrescribe — por eso el
  hangtime cuenta desde el último paquete.
- La contención se evalúa por frame, no por stream. Si un stream fue bloqueado
  por hangtime y luego el hangtime expira, los siguientes frames del mismo
  stream **sí** se reenvían.
- El flag `CONTENTION` del legado es sólo un debounce de log; no bloquea.

---

## Timeouts y constantes críticas

Estos valores definen el comportamiento observable. Cambiarlos afecta
contención, hangtime y reconexión.

| Constante | Valor | Ubicación | Rol |
|---|---|---|---|
| `STREAM_TO` | **0.36 s** | `domain/hbp_protocol.py` | Ventana para considerar un stream "activo" (entre paquetes). |
| `_STALE_PEER_SESSION_TIMEOUT` | **5.0 s** | `routing/helpers.py` | Una sesión per-peer sin frames se considera muerta (VTERM perdido). |
| `GROUP_HANGTIME` | **5 s** (default config) | por system en YAML | Bloqueo tras fin de QSO antes de aceptar otro TG en ese slot. |
| `DEFAULT_UA_TIMER` | configurable (minutos) | por system en YAML | Duración de bridges dinámicos (User Activated). |

Ver [Comportamiento y temporizadores](behaviour-and-timers.md) para los
intervalos de los bucles periódicos (`rule_timer`, `stream_trimmer`, etc.).

### Estados de un stream

```mermaid
stateDiagram-v2
    [*] --> IDLE: slot libre
    IDLE --> VHEAD: llega voice header (VHEAD)
    VHEAD --> ACTIVE: frames de voz dentro de STREAM_TO
    ACTIVE --> ACTIVE: cada frame renueva RX_TIME/TX_TIME
    ACTIVE --> VTERM: llega voice terminator (VTERM)
    ACTIVE --> TIMEOUT: sin frames > STREAM_TO
    VTERM --> HANGTIME: GROUP_HANGTIME segundos
    TIMEOUT --> HANGTIME: GROUP_HANGTIME segundos
    HANGTIME --> IDLE: expira hangtime
    HANGTIME --> ACTIVE: mismo TG reanuda (renueva)
```

**VTERM perdido:** si el MMDVM nunca envía el terminator, la sesión per-peer
expira tras `_STALE_PEER_SESSION_TIMEOUT` (5 s), liberando el slot.

**Hotspots simultáneos en un slot del MASTER:** los hotspots del MASTER
compartido comparten un mismo `STATUS[slot]`, así que además cada stream de
entrada se sigue por separado (`RX_STREAMS`: peer, origen, TG, inicio, último
frame). Cada uno vence por su cuenta a los 5 s, recibe su propio END RX con su
duración real y su START no se repite.

**Avisos del servidor:** el ident de voz, los 999x a demanda y el aviso de
desconexión ocupan el **TS2** de su MASTER mientras suenan (una radio u otro
stream en el slot los corta). Solo la voz propia de este servidor vuelve a
aplicar la reserva del slot del MASTER; el anuncio de otro servidor que llega
por OpenBridge es voz normal de la malla (`TX_FROM_MESH`) y pasa por los
controles por hotspot.

---

## SINGLE=1 vs SINGLE=0 (escucha exclusiva)

`SINGLE` se configura en la línea `OPTIONS` del hotspot (ej: `SINGLE=1;`).
Controla **cuántos TG dinámicos puede tener activos un peer por slot**.

```mermaid
flowchart LR
    subgraph SINGLE1["SINGLE=1 (escucha exclusiva)"]
        S1A[1 TG exclusivo por slot]
        S1B[PTT en nuevo TG<br/>= reemplaza el lock]
        S1C[Timer DEFAULT_UA_TIMER<br/>expira → libera]
        S1D[TG 4000 → borra lock]
    end
    subgraph SINGLE0["SINGLE=0 (multi-dinámico)"]
        S0A[Varios TG dinámicos<br/>acumulados por slot]
        S0B[GROUP_HANGTIME rige<br/>entre TGs]
        S0C[Sólo 1 stream downlink<br/>a la vez por slot]
    end
```

| Aspecto | SINGLE=1 | SINGLE=0 |
|---|---|---|
| TGs dinámicos por slot | **1 exclusivo** | **Varios acumulados** (`_PEER_UA_MULTI_TGS`) |
| Almacenamiento | `_PEER_UA_SESSIONS[peer][slot]` | `_PEER_UA_MULTI_TGS[peer][slot]` |
| Cambiar de TG | PTT en nuevo TG reemplaza el lock | Se suma al set; no reemplaza |
| TG 4000 | Borra la sesión del slot | Borra todos los dinámicos del peer |
| Timer | `DEFAULT_UA_TIMER` expira → libera | No expira individualmente; purga por `GROUP_HANGTIME` |
| Desactivación in-band | Agresiva: OFF/RESET/TG4000/tráfico no matching | Conservadora: principalmente TG 4000 |

### Excepciones a SINGLE

- **TG 9990 (eco):** **no** crea lock SINGLE. El eco vuelve siempre al llamante.
- **TG 4000:** **no** crea sesión UA. Es sólo comando de reset; con
  `TG4000_VOICE` (por defecto `true`) el hotspot además recibe la respuesta de
  estado por voz.
- **TG 9991–9999 (bajo demanda):** no crean lock SINGLE.
- **UA session propia:** un peer SINGLE=1 que activó el TG T dinámicamente
  **debe** recibir downlink de T (no se auto-bloquea).

---

## Talkgroups estáticos vs dinámicos

### TG estático

Definido en la línea `OPTIONS` del hotspot (`TS1_STATIC` / `TS2_STATIC`). El
peer siempre escucha ese TG en ese slot mientras esté conectado.

```
OPTIONS: TS1=730500;TS2=730502,730508;SINGLE=1;TIMER=60;
```

Origen de los TG estáticos:

- **OPTIONS al login (RPTO)** — el hotspot reporta su línea.
- **Self-service (panel web)** — el usuario cambia sus TG desde el navegador;
  el servidor envía un `RPTO` actualizado al MASTER.
- **`TS1_STATIC` / `TS2_STATIC` del MASTER** — se suman a los estáticos de cada
  hotspot conectado, además de sus OPTIONS (se ignoran los TG 0–5, 9 y
  9990–9999); un `SIGHUP` actualiza los hotspots conectados y las piernas del
  MASTER.
- **Startup/reload** — `apply_startup_bridges` aplica los TG al arranque y en
  `SIGHUP`.
- **D-28 (divergencia):** **no** hay loop periódico de 26 s
  (`options_config_loop`). El refresco es por evento (RPTO, startup, fallback
  dmrd sin source).

### TG dinámico (User Activated)

Activado cuando un usuario transmite en un TG que **no** tiene estático.

```mermaid
flowchart TD
    PTT[PTT en TG no estático] --> CHECK{TG libre?}
    CHECK -->|Sí| ACT1[Activar TG dinámico<br/>+ pasar voz en 1 TX<br/>divergencia vs legacy]
    CHECK -->|No, ocupado| SILENT[Activación silenciosa]
    ACT1 --> STORE1[_PEER_UA_SESSIONS o<br/>_PEER_UA_MULTI_TGS]
    STORE1 --> PERSIST[Upsert asíncrono<br/>peer_dynamic_tgs MariaDB]
    ACT1 --> BRIDGE1[Crear bridge leg dinámica<br/>ensure_dynamic_relay]
    BRIDGE1 --> DL[Peer queda escuchando<br/>ese TG]
```

**Diferencia vs legacy (`adn-dmr-server`):**

- **Legacy:** se necesitan 2 TX — la 1ª activa, la 2ª pasa voz.
- **Este servidor:** la 1ª TX hace ambas cosas (activar + pasar voz).

Ver [Persistencia TG dinámicos (MariaDB)](../user-guide/bridges-and-talkgroups.md#persistencia-tg-dinamicos-mariadb)
para la supervivencia tras reconexión.

---

## Activación silenciosa (TG ocupado con QSO activo)

**Divergencia intencionada.** Si un usuario transmite en un TG que tiene una
conversación **activa**, el servidor:

1. **No rechaza** la TX.
2. **Activa** ese TG como dinámico.
3. **No sube el uplink** del usuario (no molesta el QSO activo).
4. **Sí entrega el downlink** del QSO activo al usuario inmediatamente.

```mermaid
flowchart TD
    TX[Usuario TX en TG ocupado] --> Q1{TG tiene QSO<br/>activo?}
    Q1 -->|No| NORMAL[Flujo normal]
    Q1 -->|Sí| ACT[Activar TG dinámico]
    ACT --> NOUL[Suprimir uplink<br/>no reenviar voz del usuario]
    NOUL --> DL2[Entregar downlink<br/>del QSO activo al usuario]
    DL2 --> HANG[Respetar GROUP_HANGTIME<br/>para otros TGs en ese slot]
```

Aplica a:

- **TG no estático** que el usuario quiere escuchar.
- **SINGLE=1** cambiando de TG activo a otro con QSO en curso.

---

## Mapeo de slot en downlink

El slot por el que un peer **recibe** un TG lo determina su `OPTIONS` (si es
estático) o el slot donde lo activó (si es dinámico). **No** es el slot de
origen de la transmisión.

```mermaid
flowchart LR
    OBP1[OBP envía TG 730500<br/>en slot 1] --> MAP{Slot mapping}
    PEA[HS-A tiene 730500<br/>en TS2] --> MAP
    PEB[HS-B tiene 730500<br/>en TS1] --> MAP
    MAP --> OUTA[HS-A recibe en TS2]
    MAP --> OUTB[HS-B recibe en TS1]
```

- **OBP siempre viaja en slot 1** del paquete. El slot es informativo del
  origen; la entrega se hace al slot del peer destino.
- Dos peers con el mismo TG en slots distintos pueden **ambos** escuchar la
  misma transmisión, cada uno en su slot.
- En **simplex (DMO)**, MMDVMHost descarta paquetes con el bit TS1; el servidor
  entrega voz de downlink en **TS2** para peers simplex.

---

## Gate de downlink: ¿un peer recibe el paquete?

Para cada peer y cada frame de voz de grupo, el servidor evalúa una cadena de
filtros. **Todos** deben pasar para que el paquete se entregue.

```mermaid
flowchart TD
    PKT[DMRD hacia peer] --> F1{OPTIONS tiene TG<br/>o dinámico activo?}
    F1 -->|No| DROP[No entrega]
    F1 -->|Sí| F2{TG especial 9990-9999?}
    F2 -->|Sí| PASS1[Punto-a-punto:<br/>sólo RX_PEER (o peer único)]
    F2 -->|No| F3{¿Slot destino<br/>libre?}
    F3 -->|Peer TXing ingress| DROP
    F3 -->|Bridge hold activo<br/>TG distinto| DROP
    F3 -->|Stream activo<br/>en ese slot, TG distinto| DROP
    F3 -->|Libre| F4{GROUP_HANGTIME<br/>respeta?}
    F4 -->|No| DROP
    F4 -->|Sí| F5{SINGLE lock<br/>compatible?}
    F5 -->|Bloqueado por otro TG| DROP
    F5 -->|OK| SEND[Entregar DMRD al peer]
    PASS1 --> SEND
```

### Condiciones que bloquean downlink (per-peer)

| Condición | Detalle |
|---|---|
| **Ingress activo** | El peer está transmitiendo en ese slot → no recibe hasta terminar TX. |
| **Bridge hold** | El slot tiene un `bridge_hold` (ingress propio o listener) que bloquea TGs ajenos por `GROUP_HANGTIME`. |
| **Stream activo, TG distinto** | Ya hay un stream downlink activo en ese slot con otro TG → espera a que termine. |
| **GROUP_HANGTIME** | Si el último TG en ese slot fue distinto y está dentro del hangtime → bloquea. |
| **SINGLE lock incompatible** | SINGLE=1 con lock en otro TG → bloquea salvo que sea el TG del lock o el peer lo activara. |
| **Sesión stale** | Si la sesión per-peer lleva `_STALE_PEER_SESSION_TIMEOUT` sin frames, se purga y el slot se libera. |

### TG especiales (9990–9999): entrega punto-a-punto

El eco (9990) y los TG de servicio bajo demanda (9991–9999) **omiten** la
contención de slot per-peer y los filtros OPTIONS descritos arriba, pero **no**
se difunden a todos los peers. En un MASTER inject-only multi-peer se entregan
**punto-a-punto**:

- El paquete va **sólo** a `RX_PEER` — el peer exacto que originó la llamada
  en ese slot — cuando `RX_TGID` coincide con el TG especial.
- Si `RX_TGID` aún no coincide (p. ej. el primer VHEAD de una transmisión a
  9990 llega antes de que se actualice `RX_TGID`) y sólo hay un peer conectado,
  se entrega a ese peer único (fallback legado).
- **No hay matching difuso** del ID DMR de origen: otros hotspots del mismo
  usuario nunca reciben el eco ni la reproducción de servicio.

Esto coincide con el legado `adn-dmr-server`, donde cada MASTER tenía un solo
peer y el eco volvía naturalmente sólo al llamante. Ver [Echo](../user-guide/echo.md).

### Mid-call join

Cuando un stream downlink termina (VTERM o timeout), el slot del peer queda
libre. El **siguiente frame** de cualquier otro TG que el peer tenga activo
(estático o dinámico) y que esté en curso en la red **se entrega sin requerir
nuevo PTT**.

Esto **no relaja** ninguna regla: GROUP_HANGTIME, SINGLE y contención per-peer
se evalúan igual que para cualquier stream. Es simplemente el comportamiento
normal cuando el slot queda libre.

---

## OpenBridge y TGs dinámicos

Si un hotspot en el servidor 7302 activó el TG 7300 dinámicamente y un
hotspot en 7301 transmite ese TG, el tráfico OBP debe llegar al hotspot 7302
aunque 7300 no esté en sus OPTIONS. La pata de bridge se activa por la sesión
UA (vía `master_dynamic_tg_slots`), que consulta tanto `_PEER_UA_SESSIONS`
(SINGLE=1) como `_PEER_UA_MULTI_TGS` (SINGLE=0).

Ver [Protocolo OpenBridge](../protocols/openbridge.md) para filtros de ingreso,
BCSQ/BCKA y detalles de DMRE v5.

---

## Divergencias vs legacy

| Comportamiento | Legacy (`adn-dmr-server`) | Este servidor |
|---|---|---|
| Activar TG dinámico (TG libre) | 2 TX: 1ª activa, 2ª pasa voz | 1 TX: activa + pasa voz |
| TX en TG ocupado | Se bloquea por contención (reglas 1–4) | Activación silenciosa: activa TG, no sube uplink, entrega downlink |
| Fin de stream → join TG activo | El peer queda en hangtime; no hay join mid-stream | Al terminar el stream, el siguiente TG activo se entrega sin nuevo PTT |
| OPTIONS refresh (loop 26 s) | `options_config_loop` cada 26 s | **D-28:** por evento (RPTO, startup, fallback dmrd) — sin loop periódico |
| `GROUP_HANGTIME` | RX + TX, por sistema, sin reset en VTERM | **Paridad** (mismo comportamiento) |
| `OPTIONS` sobrescribe `GROUP_HANGTIME` | No | **Paridad** (no se puede) |

---

## Ver también

- [Bridges y talkgroups](../user-guide/bridges-and-talkgroups.md) — el modelo
  `BRIDGES` / subscriptions.
- [Números especiales](../user-guide/special-numbers.md) — TG 4000, 999x, eco.
- [Comportamiento y temporizadores](behaviour-and-timers.md) — intervalos de
  bucles periódicos.
- [BRIDGES vs Subscriptions](bridges-vs-subscriptions.md) — modelo interno.
- [Protocolo OpenBridge](../protocols/openbridge.md) — DMRE v5, filtros de
  ingreso.
- [Proxy hotspot](../user-guide/hotspot-proxy.md) — PROXY / self-service
  integrados.
