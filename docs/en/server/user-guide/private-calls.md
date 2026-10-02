# Private calls

## Overview

**Unit (private)** calls use a different path than **group** voice. The router uses **`SUB_MAP`** (subscriber → last known system/slot/time) and collision rules to decide whether and where to forward.

## SUB_MAP

- Populated when stations register traffic; persisted via configured **`SUB_MAP`** pickle path under **`ALIASES`**.
- Used to resolve **destination radio ID** to a **target system** and **slot** for private forwarding.
- Unit data (SMS, ACKs) whose destination was last heard on the same hotspot it came from is not sent back to that hotspot: both radios hear each other on RF, and the echo would transmit over their own exchange.

## OpenBridge vs MASTER

Private handling uses CSBK/data/unit branches, `SUB_MAP` lookup, and busy-slot checks where applicable (see `RoutingUseCases` in source).

## TG / ID 4000 (unit)

As documented in [Special numbers](special-numbers.md), a **private** call to **4000** disconnects dynamics and is **not** treated as a normal private call route.

## Reporting

Private **START/END** events may be emitted to the report TCP client when **`REPORTS.REPORT`** is enabled, analogous to group voice (shape `PRIVATE VOICE,...` where implemented).

For protocol ingress details, see [HBP](../protocols/hbp.md) and the routing use cases in source (`RoutingUseCases._pvt_call_received`).
