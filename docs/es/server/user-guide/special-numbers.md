# Números especiales (TG / IDs)

Varios **IDs de destino** están reservados para **control o servicios**. Se gestionan en capas de protocolo y/o en el router de bridges, no como tráfico de grupo normal.

## ID de voz del servidor — fuente configurable (no es «TG de anuncio») {#server-voice-source-id-configurable}

**Importante:** El servidor usa un **DMR ID** configurable (`VOICE.DMR_ID` en `adn-voice.yaml`). Predeterminado: **1000001**. Cada anuncio/TTS puede definir **`DMR_ID`** propio; si se omite, hereda `VOICE.DMR_ID`. El callsign lo resuelve la base de suscriptores para ese ID.

| Tráfico | Destino en el paquete DMR | Notas |
|---------|---------------------------|--------|
| **AMBE programado** (`ANNOUNCEMENTS`) | El **`TG`** que definas en `adn-voice.yaml` | `DMR_ID` del ítem o `VOICE.DMR_ID` (predeterminado **1000001**). |
| **TTS** (`TTS_ANNOUNCEMENTS`) | Igual — **`TG`** configurado | Igual. |
| **Bajo demanda** (TG **9991–9999**) | **TG 9** | Clips informativos cortos; fuente configurable (ver [Voz, anuncios y TTS](voice-and-tts.md)). |
| **Desconectado / reflector** | **TG 9** | Igual. |
| **Ident por voz** | **All-call** (`16777215`) o **`OVERRIDE_IDENT_TG`** si está definido | Igual. |

**No** «monitorizas» el ID de voz del servidor para oír anuncios programados: monitorizas el **TG de anuncio configurado** (p. ej. 2, 9, 26811). El ID configurado aparece como **transmisor** en esas llamadas.

El ID local heredado **5000** sigue reconocido en algunos caminos de reproducción bajo demanda por compatibilidad, pero el predeterminado nuevo es **1000001** (evita bloqueos con `SUB_ACL: DENY:0-1000000` en OBP).

### TG de destino 5000 (grupo entrante)

Si llega una llamada de grupo con **TG de destino 5000** y **no** hay fila `BRIDGES` existente, el servidor **no** crea automáticamente un bridge activado por usuario para esa TG (misma clase que IDs **0–4**, **9**, **4000**). Para llevar tráfico a TG 5000 necesitas una fila de bridge **explícita**.

## TG 9 — carril de servicio local (mensajes y cableado de bridge) {#tg-9-local-service-lane-prompts-and-bridge-plumbing}

**TG 9** se usa de dos formas: lo que **oyen** los operadores con mensajes cortos del servidor, y cómo se **conectan** filas internas del bridge.

### Lo que oyes (voz de salida del servidor)

Para reproducción **bajo demanda** (tras marcar **9991–9999**) y para líneas de voz de **desconectado / reflector**, el servidor transmite paquetes de **grupo** con:

- **DMR ID** = `DMR_ID` del ítem o `VOICE.DMR_ID` (predeterminado **1000001**)
- **TG de destino 9**
- **Timeslot 2** (el código usa el slot **TS2** para ese hotspot)

Sigue una convención habitual **HomeBrew / conferencia**: mantener el audio **local de servicio** corto en **TG 9 / TS2** para separarlo del tráfico QSO normal en tu TG principal (a menudo TS1). Los hotspots deben pasar **TS2** y estar en una configuración donde **TG 9** no esté bloqueado, o esos mensajes no se oirán.

**Rojo frente a local:** En **OpenBridge**, el tráfico de **grupo entrante** a **TG 9** está en el rango **≤ 79** «local / repetidor» y **no** entra al bridge desde la malla IP (se descarta en ingreso). Los mensajes del servidor usan el **camino HBP local** hacia el hotspot/repetidor, no un TG de área amplia puenteado. Excepción: solo si **añades** filas `BRIDGES` que reenvíen TG 9 podría salir ese tráfico a otro sitio — no es lo por defecto.

Los **anuncios programados** y **TTS** usan el **`TG`** que configures en `adn-voice.yaml` — **no** se fuerzan a TG 9 salvo que tú configures ese TG.

### Comportamiento del router (TG reservada)

- **No** hay bridge activado por usuario automático si alguien transmite a **TG 9** y no existe fila `BRIDGES` (misma clase que **0–4**, **4000**, **5000**, etc.).
- Con **`GEN_STAT_BRIDGES`**, la creación automática de bridge **STAT** desde OpenBridge **no** aplica al TG de destino **9** (excluido a propósito).
- El **bucle de depuración de bridges** elimina bridges de conferencia inválidos keyed en **9** (y **0**–**8**) para que no se acumulen bridges de un dígito erróneos.

### Tabla de bridges (avanzado)

Muchas filas **reflector / marcado** guardan **`TGID` = 9** en **TS2** como **destino de pata** interno para enganchar el camino dinámico al TG real — es cableado dentro de `BRIDGES`, no un «número al que llamar» como un TG nacional normal.

### Reglas in-band de VTERM que afectan TG 9 / reflectores

La activación/desactivación in-band de bridges se aplica sobre **voice terminator (VTERM)** con este alcance:

- Solo corre para tipos de llamada **`group`** y **`vcsbk`** (no para VTERM **unit/private**).
- En bridges reflector (`#...`), el manejo in-band solo se evalúa cuando el destino es **TG 9**.
- Por eso los mensajes de reflector y el cableado de marcado usan TG 9, mientras que llamadas privadas no disparan esa lógica de temporizadores de bridge.

## TG / ID 4000 — desactivar bridges dinámicos {#tg--id-4000--desactivar-bridges-dinamicos}

**Propósito:** Borrar el estado **activado por usuario (dinámico)** del hotspot que pulsa **4000**. **TG 4000 no es** un talkgroup que deba monitorizarse ni persistirse — es un **comando de reset**.

**Comportamiento (cabecera de voz de grupo):**

- Limpia sesiones UA del peer en memoria (**todos los slots** de ese peer).
- Borra filas correspondientes en **`peer_dynamic_tgs`** (MariaDB).
- Limpia campos RX obsoletos en **STATUS** para que un **RPTO** posterior no re-sembré el TG antiguo.
- En un MASTER propio, ejecuta **desactivación in-band** de bridges en el slot (como legacy). En un MASTER compartido por varios hotspots (el destino **inject-only** del proxy, o con `MAX_PEERS` mayor que 1) no: sus patas son de todos los hotspots, y el 4000 in-band las pondría todas en IDLE en ese slot (los dinámicos y los estáticos OPTIONS de los demás).
- Envía **`GROUP VOICE,INGRESS,RX,…,4000`** al monitor (no **START**) para que los chips **SINGLE=0** se limpien **sin** encender TX en vivo.
- En un MASTER compartido, empuja **CONFIG_SND** actualizado al monitor.

**MASTER compartido frente a uno de un solo peer:** Con **`PROXY`** integrado, o en cualquier MASTER con `MAX_PEERS` mayor que 1, el reset es **por peer** (solo los dinámicos de ese hotspot). En un MASTER de un solo peer sigue aplicándose legacy **`deactivate_all_dynamic_bridges`** a todo el system.

**TG 4000 no debe aparecer** como chip UA dinámico en el monitor ni en `peer_dynamic_tgs`.

**Respuesta de voz (activa por defecto; `TG4000_VOICE: false` en el MASTER la desactiva):** al terminar la pasada al 4000 (su terminador), el servidor responde **solo a ese hotspot**, alrededor de un segundo después, con el estado de enlace en el `ANNOUNCEMENT_LANGUAGE` del sistema: «enlazado a» y los TG estáticos que el hotspot conserva en ese slot (sus OPTIONS y los `TS1_STATIC` / `TS2_STATIC` del MASTER, hasta cuatro); si no tiene, «enlazado a *N*» si hay `DEFAULT_REFLECTOR`; si no, «no enlazado».

- Una llamada de **grupo** al TG 4000 se responde con una llamada de grupo del ID de voz del servidor al **TG 4000** por ese slot: la radio la oye en el contacto que acaba de usar.
- Una llamada **privada** al ID 4000 se responde con una llamada privada **desde el ID 4000** a quien llamó, que las configuraciones de los hotspots devuelven al contacto privado (p. ej. `PCRewrite …,84000,…,4000,…`).

Los demás hotspots de un MASTER compartido no la oyen, nunca sale por OpenBridge y se corta si ese hotspot empieza a recibir otro stream por ese slot. Se reproduce en el reactor, una trama cada 58 ms. Una respuesta por pasada; ninguna si se pierde el terminador.

**MASTER compartido, patas:** un 4000 nunca libera una pata que **cualquier** hotspot conectado, incluido el que lo envía, tiene en ese slot como estático (de OPTIONS o del YAML del MASTER); la sesión dinámica de otro hotspot en ella también la mantiene. Cualquier otra pata dinámica que tenía el que envía el 4000 pasa a IDLE en el momento, sin esperar a su temporizador.

### Impacto de `SINGLE_MODE` en la lógica de desactivación

Cuando las reglas in-band evalúan desactivación en un slot MASTER:

- **`SINGLE_MODE: true`**: la desactivación es agresiva. Una pata puede apagarse por triggers OFF/RESET, por **TG 4000** o por tráfico que no coincide con el TG de la pata.
- **`SINGLE_MODE: false`**: la desactivación es conservadora. **TG 4000** es el trigger principal de apagado forzado; filas de TG estática y filas reflector se preservan según los chequeos actuales del bridge.

A nivel operativo: si usuarios reportan «bridges que se caen demasiado fácil» tras cambios de OPTIONS, verifica el valor actual de `SINGLE_MODE` y el payload OPTIONS del hotspot.

## TG 9991–9999 — audio informativo / bajo demanda

**Propósito:** **reproducir** ficheros AMBE pregenerados («ondemand») (p. ej. información de la estación, ayuda).

**Comportamiento:**

- Dispara manejo tipo **`playFileOnRequest`**: mapea los últimos dígitos al nombre de fichero bajo el árbol de audio configurado.
- La ruta de disparo es **private VTERM** para destino **9991–9999**, seguida de generación/reproducción asíncrona.
- Funciona desde rutas **MASTER** y **PEER**.

El **audio** se envía con el **ID de fuente configurado** y **TG de destino 9** en el flujo generado. Estructura de ficheros: [Voz, anuncios y TTS](voice-and-tts.md).

## TG 9990 — eco (en banda)

**Propósito:** las filas de bridge para **eco** suelen usar **9990** con el sistema **ECHO** (ver `BRIDGES` y opciones en tu YAML).

**`SINGLE=1`:** pulsar **9990** **no** crea sesión de escucha exclusiva (igual que **4000**). El downlink del eco vuelve siempre al hotspot llamante aunque otra TG tenga el bloqueo SINGLE. Ver [Proxy hotspot](hotspot-proxy.md#comportamiento-con-varios-hotspots) y [Enrutado de voz y contención — Excepciones a SINGLE](../development/routing-and-contention.md#excepciones-a-single).

**Nota:** Un **echo independiente** también está disponible como proceso aparte — [Echo](echo.md).

## Llamada privada al ID 4000

Una llamada **unitaria** a **4000** se trata solo como **desconexión de dinámicos**; **no** se enruta como llamada privada normal. Con `TG4000_VOICE` (por defecto `true`) el hotspot además recibe la [respuesta por voz](#tg--id-4000--desactivar-bridges-dinamicos) como llamada privada desde el ID 4000.

## Ingreso OpenBridge — filtros de TG de grupo {#openbridge-ingress--group-tg-filters}

En **OpenBridge** (**DMRD** v1 y **DMRE**), el tráfico de **grupo** **no unitario** hacia ciertas TG puede **descartarse** antes del bridge (con **BCSQ** donde aplique). Las reglas difieren ligeramente entre DMRD y DMRE; incluyen TG de número bajo (p. ej. **≤ 79**), **9990–9999**, **900999**, rangos **92–199** (frente al servidor de origen), y rangos relacionados con MCC (**80–89**, **800–899**). Detalle: [Protocolo OpenBridge](../protocols/openbridge.md#ingress-filters-group-tg).

## TG prohibidos / reservados (creación de bridges)

Muchos IDs pequeños (0–5, 9, etc.) y el rango de **servicio 999x** quedan excluidos de ciertos caminos **automáticos** de creación de bridges; **5000** y **4000** están en el conjunto «sin bridge UA automático» si no existe fila. Los conjuntos exactos están definidos en el router de bridges y el manejo de opciones en el código.

## Tabla resumen

| ID / rango | Rol |
|------------|-----|
| **VOICE.DMR_ID** (fuente) | Voz generada por el servidor — **ID de llamada** en receptores (predeterminado **1000001**) |
| **5000** (destino) | Sin bridge UA automático si falta en `BRIDGES` |
| **4000** (grupo) | Desactivar bridges dinámicos; respuesta de estado por voz en el TG 4000 (`TG4000_VOICE`, por defecto `true`) |
| **4000** (unitaria) | Desconectar dinámicos; no enrutada como PC; respuesta de estado por voz desde el ID 4000 (`TG4000_VOICE`) |
| **9991–9999** | Audio informativo / bajo demanda (TG de disparo); reproducción usa `VOICE.DMR_ID` → TG **9** (TS2) |
| **9** | Carril de servicio/mensajes (audio corto del servidor); reservada para auto-bridges; **TGID** interno en patas TS2 |
| **9990** | TG de bridge de eco (con sistema ECHO) |
| **16777215** | All-call (destino por defecto de ident por voz salvo sobrescritura) |
