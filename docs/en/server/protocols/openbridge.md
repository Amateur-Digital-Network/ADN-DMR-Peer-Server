# OpenBridge (FreeBridge / DMRE)

## DMRE and “OpenBridge v5”

On the wire, extended OpenBridge uses the **`DMRE`** opcode. The **embedded protocol version** byte inside the frame (see [DMRE v5 layout](dmre-v5.md)) selects the layout: **version > 4** is the **89-byte v5** format (hops, source repeater field, BLAKE2b MAC). In documentation and operator discussions, **“DMRE v5”** and **“OpenBridge v5”** refer to the same thing: **DMRE frames with embedded version 5** (not the older short DMRD-only path).

**Recommendation (ADN Systems network):** All inter-server links that participate in the **ADN Systems** mesh should use **DMRE v5** (`PROTO_VER: 5` in YAML, which sets the negotiated **VER** / embedded version) and **`ENHANCED_OBP: true`** so **BCSQ**, **BCKA**, and multi-path loop control behave consistently. **Peers** (other servers) should be configured the same way. **DMRD v1** (HMAC-only) remains available as a compatibility mode, but it is **not** the preferred mode for new ADN deployments.

## What OpenBridge is

**OpenBridge** is a UDP protocol between **servers** (and some gateways). It carries DMR voice using:

- **`DMRD`** version 1 — HMAC-SHA1 authenticated payload (compatibility mode); or
- **`DMRE`** — extended frame with **BLAKE2b** MAC, embedded version, timestamps, **hops**, source server/repeater IDs, etc. (**DMRE v5** = embedded version 5, recommended above).

This stack implements the **OPENBRIDGE** peer mode in **`udp_hbp.py`** (UDP adapter), with admission rules, the per-frame engine and the link session in **`domain/mesh_admission.py`**, **`domain/mesh_engine.py`** and **`domain/mesh_session.py`**, and voice routing in **`RoutingUseCases`** (`application/routing/`, `application/subscription/`).

## Ingress (DMRE)

1. Verify **BLAKE2b** over the authenticated prefix.
2. Check **NETWORK_ID**, **TARGET** socket / `RELAX_CHECKS`, **slot** (TS1 for OBP ingress).
3. Increment **hops**; if **> 10**, drop and optionally send **BCSQ**.
4. Rebuild a pseudo-**DMRD** for the bridge and call **`dmrd_received`** with hop metadata.

## Egress (`send_system`)

- Build **DMRE** or **DMRD** v1 depending on negotiated **VER** and embedded version.
- Preserve **hops**, **BER/RSSI**, **source_server** / **source_rptr** as required for interop.

## Loop control (multi-path mesh)

For **group** voice on OpenBridge:

1. **Finished** / **timeout 180 s** — drop stale streams.
2. **Echo HBP** — if a non-OBP system already has this `stream_id` in RX, this OBP path is treated as echo.
3. **Multiple OBP** — among OBP legs with the same `stream_id` and TG, **only the earliest `1ST` time** (`min(perf_counter)` ) **forwards**; others stop and may send **BCSQ** if **`ENHANCED_OBP`** is true.

## BCSQ (Bridge Control — Source Quench)

- **Meaning:** “Do not forward this **`stream_id`** on this **TG** to my leg.”
- **Sent by** the **losing** OBP in loop control to its **peer** (not a global broadcast).
- **Honoured** when **forwarding** to another OBP: if the destination’s session has quenched that stream on that TG, **skip** `send_system`.

## BCKA (keepalive)

- **ENHANCED_OBP** — if peer keepalive is stale, block forward until refreshed.

## Bridge forwarding (`to_target`)

Per destination row: dedupe `(SYSTEM, TS)`, check **BCSQ**, **BCKA**, **ACL**, rewrite **LC** / **TGID**, force **TS1** bit pattern for OBP, call **`send_system`**.

## Ingress filters (group TG)

For **group** (and **vcsbk**) traffic, OpenBridge applies **TG filters** before traffic reaches the bridge router. Dropped streams may trigger **BCSQ** back to the peer. The exact rules differ between **DMRD v1** and **DMRE** paths in `domain/mesh_admission.py`; in general they reject traffic that is treated as **local-only** or **wrong server** for the destination, for example:

- Low TG numbers (e.g. **≤ 79** on DMRE; DMRD v1 also combines **9990–9999**, **92–199**, **900999** in one check).
- **9990–9999** and **900999** (service / local-server ranges on DMRE).
- **92–199** unless the **source server** ID matches your server’s main ID (DMRE).
- **80–89** and **800–899** unless the **MCC** prefix matches (DMRE).

**Private (unit)** calls are not subject to the same group-TG filter block in the same way; configure **ACL** separately.

Operator-oriented summary: [Special numbers — OpenBridge ingress](../user-guide/special-numbers.md#openbridge-ingress--group-tg-filters).

## Monitor events (adn-monitor)

- **INGRESS** — debug-only first sight per leg.
- **START** — canonical **after** loop win (dashboard state).

See also: [DMRE v5 layout](dmre-v5.md), [Monitoring](../user-guide/monitoring.md).
