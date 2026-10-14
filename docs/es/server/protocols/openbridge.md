# OpenBridge (FreeBridge / DMRE)

## DMRE y «OpenBridge v5» {#dmre-and-openbridge-v5}

En cable, OpenBridge extendido usa el opcode **`DMRE`**. El **byte de versión de protocolo embebido** dentro de la trama (ver [disposición DMRE v5](dmre-v5.md)) selecciona el diseño: **versión > 4** es el formato **v5 de 89 bytes** (saltos, campo repetidor fuente, MAC BLAKE2b). En documentación y conversaciones de operadores, **«DMRE v5»** y **«OpenBridge v5»** son lo mismo: **tramas DMRE con versión embebida 5** (no el camino corto solo DMRD).

**Recomendación (red ADN Systems):** todos los enlaces inter-servidor que participen en la malla **ADN Systems** deberían usar **DMRE v5** (`PROTO_VER: 5` en YAML, que fija **VER** / versión embebida negociada) y **`ENHANCED_OBP: true`** para que **BCSQ**, **BCKA** y el control de bucle multipath se comporten igual. Los **pares** (otros servidores) deben configurarse igual. **DMRD v1** (solo HMAC) permanece como modo de compatibilidad, pero **no** es el modo preferido para nuevos despliegues ADN.

## Qué es OpenBridge

**OpenBridge** es un protocolo UDP entre **servidores** (y algunos gateways). Transporta voz DMR usando:

- **`DMRD`** versión 1 — carga autenticada HMAC-SHA1 (modo de compatibilidad); o
- **`DMRE`** — trama extendida con MAC **BLAKE2b**, versión embebida, marcas de tiempo, **saltos**, IDs servidor/repetidor de origen, etc. (**DMRE v5** = versión embebida 5, recomendada arriba).

Esta pila implementa el modo par **OPENBRIDGE** en **`udp_hbp.py`** (adaptador UDP), con las reglas de admisión, el motor por trama y la sesión del enlace en **`domain/mesh_admission.py`**, **`domain/mesh_engine.py`** y **`domain/mesh_session.py`**, y el enrutado de voz en **`RoutingUseCases`** (`application/routing/`, `application/subscription/`).

## Ingreso (DMRE)

1. Verificar **BLAKE2b** sobre el prefijo autenticado.
2. Comprobar **NETWORK_ID**, socket **TARGET** / `RELAX_CHECKS`, **slot** (TS1 para ingreso OBP).
3. Incrementar **saltos**; si **> 10**, descartar y opcionalmente enviar **BCSQ**.
4. Reconstruir un pseudo-**DMRD** para el bridge y llamar **`dmrd_received`** con metadatos de salto.

## Egreso (`send_system`)

- Construir **DMRE** o **DMRD** v1 según **VER** negociada y versión embebida.
- Preservar **saltos**, **BER/RSSI**, **source_server** / **source_rptr** según haga falta para interoperar.

## Control de bucle (malla multipath)

Para voz de **grupo** en OpenBridge:

1. **Finalizado** / **timeout 180 s** — descartar flujos obsoletos.
2. **Eco HBP** — si un sistema no-OBP ya tiene este `stream_id` en RX, esta pata OBP se trata como eco.
3. **Varios OBP** — entre patas OBP con el mismo `stream_id` y TG, **solo el `1ST` más temprano** (`min(perf_counter)`) **reenvía**; las demás paran y pueden enviar **BCSQ** si **`ENHANCED_OBP`** es true.

## BCSQ (Bridge Control — Source Quench)

- **Significado:** «No reenvíes este **`stream_id`** en esta **TG** hacia mi pata.»
- **Lo envía** el OBP **perdedor** en control de bucle hacia su **par** (no es broadcast global).
- **Se respeta** al reenviar a otro OBP: si la sesión del destino tiene ese stream en quench para ese TG, **omitir** `send_system`.

## BCKA (keepalive)

- **ENHANCED_OBP** — si el keepalive del par está obsoleto, bloquear reenvío hasta que se refresque.

## Reenvío de bridge (`to_target`)

Por fila de destino: deduplicar `(SYSTEM, TS)`, comprobar **BCSQ**, **BCKA**, **ACL**, reescribir **LC** / **TGID**, forzar patrón de bit **TS1** para OBP, llamar **`send_system`**.

## Filtros de ingreso (TG de grupo) {#ingress-filters-group-tg}

Para tráfico de **grupo** (y **vcsbk**), OpenBridge aplica **filtros TG** antes de que el flujo llegue al router de bridges. Los flujos descartados pueden disparar **BCSQ** hacia el par. Las reglas exactas difieren entre **DMRD v1** y **DMRE** en `domain/mesh_admission.py`; en general rechazan tráfico tratado como **solo local** o **servidor incorrecto** para el destino, por ejemplo:

- TG bajas (p. ej. **≤ 79** en DMRE; DMRD v1 también combina **9990–9999**, **92–199**, **900999** en una comprobación).
- **9990–9999** y **900999** (rangos de servicio / servidor local en DMRE).
- **92–199** salvo que el ID de **servidor de origen** coincida con el ID principal de tu servidor (DMRE).
- **80–89** y **800–899** salvo que el prefijo **MCC** coincida (DMRE).

Las llamadas **privadas (unitarias)** no están sujetas al mismo bloqueo de filtro TG de grupo de la misma forma; configura **ACL** por separado.

Resumen orientado al operador: [Números especiales — ingreso OpenBridge](../user-guide/special-numbers.md#openbridge-ingress--group-tg-filters).

## Eventos de monitor (adn-monitor)

- **INGRESS** — primera vista de depuración por pata.
- **START** — canónico **después** de ganar el bucle (estado del panel).

Ver también: [disposición DMRE v5](dmre-v5.md), [Monitor](../user-guide/monitoring.md).
