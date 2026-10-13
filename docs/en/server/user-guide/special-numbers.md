# Special numbers (TG / IDs)

Several **destination IDs** are reserved for **control or services**. They are handled in protocol layers and/or the bridge router, not as normal group traffic.

## Server voice source ID (configurable, not “announcement TG”) {#server-voice-source-id-configurable}

**Important:** The server uses a configurable **DMR ID** (`VOICE.DMR_ID` in `adn-voice.yaml`). Default: **1000001**. Each announcement/TTS row may set its own **`DMR_ID`**; when omitted, it inherits `VOICE.DMR_ID`. Callsign comes from the subscriber DB for that ID.

| Traffic | Destination in the DMR packet | Notes |
|---------|----------------------------------|--------|
| **Scheduled AMBE** (`ANNOUNCEMENTS`) | Whatever **`TG`** you set in `adn-voice.yaml` | Item `DMR_ID` or `VOICE.DMR_ID` (default **1000001**). |
| **TTS** (`TTS_ANNOUNCEMENTS`) | Same — configured **`TG`** | Same. |
| **On-demand** (TG **9991–9999**) | **TG 9** | Short info clips; configurable source (see [Voice, announcements, and TTS](voice-and-tts.md)). |
| **Disconnected / reflector prompts** | **TG 9** | Same. |
| **Voice ident** | **All-call** (`16777215`) or **`OVERRIDE_IDENT_TG`** if set | Same. |

You do **not** “monitor” the server voice ID to hear scheduled announcements: you monitor the **configured announcement TG** (e.g. 2, 9, 26811). The configured ID appears as the **transmitter** on those calls.

The legacy local ID **5000** is still recognised on some on-demand playback paths for compatibility, but the new default is **1000001** (avoids `SUB_ACL: DENY:0-1000000` blocks on OBP).

### Destination TG 5000 (inbound group)

If a group call arrives with **destination TG 5000** and there is **no** existing `BRIDGES` entry, the server does **not** auto-create a user-activated bridge for that TG (same class as IDs **0–4**, **9**, **4000**). To carry traffic to TG 5000 you need an **explicit** bridge row.

## TG 9 — local service lane (prompts and bridge plumbing) {#tg-9-local-service-lane-prompts-and-bridge-plumbing}

**TG 9** is used in two different ways: what **operators hear** from short server prompts, and how **internal bridge rows** are wired.

### What you hear (outbound server voice)

For **on-demand** playback (after you key **9991–9999**) and for **disconnected / reflector** voice lines, the server transmits **group** packets with:

- **DMR ID** = item `DMR_ID` or `VOICE.DMR_ID` (default **1000001**)
- **Destination TG 9**
- **Timeslot 2** (the code drives the **TS2** slot for that hotspot)

That follows a common **HomeBrew / conference** convention: keep short **local service** audio on **TG 9 / TS2** so it stays separate from normal QSO traffic on your main TG (often on TS1). Hotspots must pass **TS2** and be on a configuration where **TG 9** is not blocked, or those prompts will not be heard.

**Red vs local:** On **OpenBridge**, **inbound group** traffic to **TG 9** is in the **≤ 79** “local / repeater” range and is **not** brought into the bridge from the IP mesh (it is dropped at ingress). Server prompts use the **local HBP path** to the hotspot/repeater, not a wide-area bridged TG. Exception: only if you **explicitly** add `BRIDGES` rows that forward TG 9 could that traffic be sent elsewhere — not the default.

**Scheduled** announcements and **TTS** use the **`TG`** you set in `adn-voice.yaml` — they are **not** forced to TG 9 unless you configure that TG yourself.

### Router behaviour (reserved TG)

- **No automatic user-activated bridge** if someone transmits to **TG 9** and no `BRIDGES` row exists (same class as **0–4**, **4000**, **5000**, etc.).
- With **`GEN_STAT_BRIDGES`**, automatic **STAT** bridge creation from OpenBridge does **not** apply to destination **TG 9** (it is excluded on purpose).
- The **bridge debug** loop removes invalid conference bridges keyed **`9`** (and **`0`–`8`**) so single-digit stray bridges do not accumulate.

### Bridge table (advanced)

Many **reflector / dial** rows store **`TGID` = 9** on **TS2** as the **leg destination** used internally to attach the dynamic path to the real talkgroup — that is wiring inside `BRIDGES`, not something you “call” like a normal national TG.

### In-band VTERM rules that affect TG 9 / reflector behavior

In-band bridge activation/deactivation is applied on **voice terminator (VTERM)** with this scope:

- It runs only for call types **`group`** and **`vcsbk`** (not for **unit/private** VTERM).
- For reflector bridges (`#...`), in-band handling is evaluated only when the destination is **TG 9**.
- This is why reflector prompts and dial wiring are tied to TG 9 while private calls do not trigger that bridge-timer logic.

## TG / ID 4000 — deactivate dynamic bridges {#tg--id-4000--deactivate-dynamic-bridges}

**Purpose:** Clear **user-activated (dynamic)** state for the hotspot that keys **4000**. **TG 4000 is not** a talkgroup to monitor or persist — it is a **reset command**.

**Behaviour (group voice header):**

- Clears per-peer UA sessions in memory (**all slots** for that peer).
- Deletes matching rows from **`peer_dynamic_tgs`** (MariaDB).
- Clears stale **STATUS** RX fields so a later **RPTO** does not re-seed the old TG.
- Runs **in-band bridge deactivation** on the slot (same as legacy).
- Sends **`GROUP VOICE,INGRESS,RX,…,4000`** to the monitor (not **START**) so **SINGLE=0** multi-dynamic chips clear **without** lighting a live TX chip.
- On **inject-only** MASTER, pushes updated **CONFIG_SND** to the monitor.

**Inject-only vs global:** With integrated **`PROXY`**, reset is **per peer** (only that hotspot’s dynamics). Without inject-only filtering, legacy **`deactivate_all_dynamic_bridges`** still runs for the whole system.

**TG 4000 must never appear** as a dynamic UA chip on the monitor or in `peer_dynamic_tgs`.

**Voice reply (`TG4000_VOICE: true` on the MASTER):** when the 4000 over ends (its terminator), the server answers **that hotspot only** about a second later with the link status in the system's `ANNOUNCEMENT_LANGUAGE`: "not linked", or "linked to *N*" with a `DEFAULT_REFLECTOR`. It is a group call from the server voice ID to **TG 4000** on the slot the 4000 came in on, so the radio hears it on the contact it just used. Other hotspots on a shared MASTER do not hear it, it never goes to OpenBridge, and it stops if that hotspot starts receiving another stream on the slot. One reply per over; none if the terminator is lost.

### `SINGLE_MODE` impact on in-band deactivation

When in-band rules evaluate deactivation on a MASTER slot:

- **`SINGLE_MODE: true`**: deactivation is aggressive. A bridge leg can be turned off by OFF/RESET triggers, **TG 4000**, or traffic that does not match the leg TG.
- **`SINGLE_MODE: false`**: deactivation is conservative. **TG 4000** is the primary forced-deactivate trigger; static TG rows and reflector rows are preserved according to current bridge checks.

Operationally: if users report “bridges drop too easily” after OPTIONS updates, verify the current `SINGLE_MODE` value and hotspot OPTIONS payload.

## TG 9991–9999 — information / on-demand audio

**Purpose:** **Play back** pre-generated AMBE (“ondemand”) files (e.g. station info, help).

**Behaviour:**

- Triggers **`playFileOnRequest`**-style handling: maps the last digits to a file name under the configured audio tree.
- Trigger path is **private VTERM** for destination **9991–9999**, then async playback generation.
- Works from **MASTER** and **PEER** paths.

The **audio** is sent with the **configured source ID** and **destination TG 9** in the generated stream. File layout: [Voice, announcements, and TTS](voice-and-tts.md).

## TG 9990 — echo (in-band)

**Purpose:** Bridge rows for **echo** often use **9990** with the **ECHO** system (see `BRIDGES` and options in your YAML).

**`SINGLE=1`:** Keying **9990** does **not** create an exclusive listen session (same as **4000**). Downlink echo always returns to the calling hotspot even when another TG holds the SINGLE lock. See [Hotspot proxy](hotspot-proxy.md#multi-hotspot-behaviour) and [Voice routing and contention — SINGLE exceptions](../development/routing-and-contention.md#single-exceptions).

**Note:** A **standalone echo** is also available as a separate process — [Echo](echo.md).

## Private call to ID 4000

A **unit** call to **4000** is treated as **disconnect dynamics** only; it is **not** routed as a normal private call.

## OpenBridge ingress — group TG filters {#openbridge-ingress--group-tg-filters}

On **OpenBridge** (**DMRD** v1 and **DMRE**), **non-unit** group traffic to certain TGs may be **dropped** before bridging (with **BCSQ** where applicable). Rules differ slightly between DMRD and DMRE; they include low-number TGs (e.g. **≤ 79**), **9990–9999**, **900999**, ranges **92–199** (vs source server), and MCC-related ranges (**80–89**, **800–899**). Details: [OpenBridge protocol](../protocols/openbridge.md#ingress-filters-group-tg).

## Prohibited / reserved TGs (bridge creation)

Many small IDs (0–5, 9, etc.) and the **999x service** range are excluded from certain **automatic** bridge-creation paths; **5000** and **4000** are in the “no auto UA bridge” set when no row exists. Exact sets are defined in the bridge router and options handling in source.

## Summary table

| ID / range | Role |
|------------|------|
| **VOICE.DMR_ID** (source) | Server-generated voice — **caller ID** on receivers (default **1000001**) |
| **5000** (destination) | No auto user-activated bridge if missing from `BRIDGES` |
| **4000** (group) | Deactivate dynamic bridges |
| **4000** (unit) | Disconnect dynamics; not routed as PC |
| **9991–9999** | On-demand / information audio (trigger TG); playback uses `VOICE.DMR_ID` → TG **9** (TS2) |
| **9** | Service/prompt lane (short server audio); reserved for auto-bridges; internal **TGID** on TS2 legs |
| **9990** | Echo bridge TG (with ECHO system) |
| **16777215** | All-call (default voice-ident destination unless overridden) |
