# Private calls

## Overview

**Unit (private)** calls use a different path than **group** voice. The router uses **`SUB_MAP`** (subscriber → last known system/slot/time) and collision rules to decide whether and where to forward.

## SUB_MAP

- Populated when stations register traffic; persisted via configured **`SUB_MAP`** pickle path under **`ALIASES`**.
- Used to resolve **destination radio ID** to a **target system** and **slot** for private forwarding.
- Unit data (SMS, ACKs) whose destination was last heard on the same hotspot it came from is not sent back to that hotspot: both radios hear each other on RF, and the echo would transmit over their own exchange.

<a id="service-systems-sub_map_learn"></a>
### Service systems (`SUB_MAP_LEARN`)

Every frame from a MASTER or PEER system moves its source ID to that system in `SUB_MAP`. A service system that transmits with someone else's ID would take that subscriber's place until they key up again, and private calls and unit data addressed to them would be delivered to the service instead:

- the **ECHO** parrot plays each call back with the caller's own ID, so right after a 9990 test that caller's private traffic points at `ECHO`;
- a beacon or announcement peer that transmits with a person's ID or a shared service ID;
- an ASL / EchoLink / DVSwitch bridge that forwards the real callers' IDs from another mode.

Set `SUB_MAP_LEARN: false` on those systems:

```yaml
SYSTEMS:
  ECHO:
    MODE: MASTER
    SUB_MAP_LEARN: false   # the parrot never becomes where a caller is
```

Only learning is skipped: group routing, ACLs, and delivery to subscribers learned on other systems work as before. The default is `true` on every system, including one named `ECHO`. When a reload or a restart finds a system with `false`, the `SUB_MAP` entries that already point at it are dropped. OpenBridge systems always learn (whoever is behind the link must stay reachable), so the key is rejected on `MODE: OPENBRIDGE`. Frames that plugins send with `send_dmrd` never update `SUB_MAP` either.

## OpenBridge vs MASTER

Private handling uses CSBK/data/unit branches, `SUB_MAP` lookup, and busy-slot checks where applicable (see `RoutingUseCases` in source).

## TG / ID 4000 (unit)

As documented in [Special numbers](special-numbers.md), a **private** call to **4000** disconnects dynamics and is **not** treated as a normal private call route.

## Reporting

Private **START/END** events may be emitted to the report TCP client when **`REPORTS.REPORT`** is enabled, analogous to group voice (shape `PRIVATE VOICE,...` where implemented).

For protocol ingress details, see [HBP](../protocols/hbp.md) and the routing use cases in source (`RoutingUseCases._pvt_call_received`).
