# OBP proxy (single inbound port)

Optional `OBP_PROXY` stanza configures the fan-in listener for all `MODE: OPENBRIDGE` systems. When active, OpenBridge instances are **inject-only** (no per-bridge `listenUDP` in `HBPProtocol`); the proxy owns every inbound OBP socket.

## Activation

| YAML | Behaviour |
|------|-----------|
| No `OBP_PROXY` block, no OPENBRIDGE | N/A (proxy not started). |
| No `OBP_PROXY` block, OPENBRIDGE present | **Default proxy on** (`LISTEN_PORT` 62032, `BIND_LEGACY_PORTS` true). |
| `OBP_PROXY.ENABLED: false` | Legacy mode: each OPENBRIDGE binds its own `PORT`. |
| `OBP_PROXY.ENABLED: true` | Proxy manages all OBP inbound UDP (same as absent block). |

## Configuration

```yaml
OBP_PROXY:
  ENABLED: true
  LISTEN_PORT: 62032      # ADN standard OBP fan-in (pair to PROXY 62031)
  LISTEN_IP: ""           # optional bind address
  BIND_LEGACY_PORTS: true # default: also listen each SYSTEMS.*.PORT
  DEBUG: false
```

OPENBRIDGE sections stay unchanged (`PORT`, `NETWORK_ID`, `PASSPHRASE`, `TARGET_*`, ACL, etc.). With proxy enabled, `PORT` is kept as metadata (`_REPORT_PORT` internally) for monitor/report and optional legacy listeners.

## Per-bridge migration (`BIND_LEGACY_PORTS: true`)

When the global flag is true, each OPENBRIDGE can migrate individually:

| `SYSTEMS.*.PORT` | Behaviour |
|------------------|-----------|
| Same as `OBP_PROXY.LISTEN_PORT` (e.g. 62032) | Fan-in only for this bridge (no extra legacy listener). |
| Omitted, `0`, or empty | Same as `LISTEN_PORT` — fan-in only (migrated bridge). |
| Any other port (e.g. 62999) | Legacy listener stays open for that bridge. |

Example: migrate `OBP-CL2` to the shared fan-in while `OBP-EU` keeps `PORT: 62999`.

## Migration

1. Existing configs with OPENBRIDGE but no `OBP_PROXY` stanza already use defaults (`BIND_LEGACY_PORTS: true`) — no remote changes required.
2. Optionally add an explicit `OBP_PROXY` block to tune `LISTEN_PORT` / `BIND_LEGACY_PORTS`.
3. Set `BIND_LEGACY_PORTS: false` and close legacy ports when all remotes use `LISTEN_PORT`.

## Requirements

- `NETWORK_ID` must be unique among enabled OPENBRIDGE systems.
- `LISTEN_PORT` must not collide with any OPENBRIDGE `PORT` when `BIND_LEGACY_PORTS` is true.
- `RELAX_CHECKS: true` is recommended so the peer address is learned from the first valid packet. What is learned lives in the bridge's session, not in the config: `TARGET_IP` / `TARGET_PORT` stay as written, and a reload puts the link back on them.

## Why did that call not cross?

Replay a capture through the same ingress the server runs, offline and against
your own `adn-server.yaml`. Nothing is sent and no port is bound, so the server
can keep running:

```bash
tcpdump -i any -n -w /tmp/obp.pcap udp port 62201 or udp port 62268   # a minute is plenty
adn-server -c adn-server.yaml --replay /tmp/obp.pcap
```

Each frame comes back with the bridge it belongs to and a verdict:

```
12:04:31  82.65.127.86:62201       OBP-FR       DMRD v1   2130001 -> 214          delivered
12:04:31  85.241.222.7:62268       OBP-PT       DMRE v5   2680015 -> 9            dropped (tg-filter-server) +BCSQ
12:04:32  203.0.113.9:50000        -            DMRD v1                           unmatched

3 datagram(s)
  OBP-FR
       1  delivered
  OBP-PT
       1  dropped: tg-filter-server
  (no bridge)
       1  unmatched
```

`unmatched` means no enabled bridge could verify the frame with its passphrase.
Add `--system OBP-FR` to look at one link, `--replay-limit N` to stop early and
`--replay-summary` for the tally alone. Classic pcap only; convert a pcapng with
`editcap -F pcap in.pcapng out.pcap`.

See also: [OpenBridge protocol](../protocols/openbridge.md).
