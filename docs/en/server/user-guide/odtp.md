# ODTP gateway (phone apps and IP terminals)

## What it is

**ODTP** (Open DMR Terminal Protocol, the BrandMeister *Rewind* protocol) lets
phone apps and IP terminals (Z3DMR, PoC radios…) use an ADN master with
their user's own DMR ID and password, without a hotspot.

The gateway runs as a separate process of the same binary,
**`adn-server.py --odtp`**. Each terminal that logs in becomes **one HBP peer**
of the MASTER, from its own UDP socket, exactly like a hotspot:

| Terminal | HBP peer |
|----------|----------|
| Logs in with a 7-digit DMR ID | Peer ID = DMR ID × 100 + `PEER_SUFFIX` (`98`; a second session of the same ID takes `97`) |
| Logs in with a 9-digit number | That number, as is; it talks as DMR ID = number ÷ 100 (a hotspot's ID with its 2-digit suffix) |
| Password | Relayed: the MASTER's salt is the terminal's challenge, its answer is the RPTK. The gateway never sees nor stores a password; the MASTER (self-service or passphrase) decides |
| Talkgroups it subscribes to | `OPTIONS` `TERMINAL=1;TS2=<tgs>;` — subscription changes within 0.1 s go as one RPTO |
| Voice | DMRD on TS2; the source is always the session's DMR ID, never the one the terminal puts in the header. A call without a terminator ends 1 s after its last frame, both ways |
| Text messages, location | DMR packet data (ETSI standard / MOTOROLA TMS / Hytera, NMEA to `LOCATION_DST`) |

A wrong password gets no answer, as on BrandMeister, and never drops a session
already connected with that ID.

## TERMINAL=1

`TERMINAL=1` in a peer's `OPTIONS` marks a terminal: it has no RX group list,
so the MASTER does **not** add its `TS1_STATIC` / `TS2_STATIC` to it — the
terminal hears only what it subscribed to. Everything else (routing, ACLs,
self-service, monitor) treats it as any other peer; the monitor lists these
peers under *ODTP terminals*.

## Configuration

Copy **`adn-odtp.example.yaml`** → **`adn-odtp.yaml`** (not committed):

| Key (`ODTP:`) | Default | Role |
|---------------|---------|------|
| `LISTEN_IP` / `LISTEN_PORT` | `0.0.0.0` / `54006` | UDP port the apps connect to |
| `MASTER_IP` / `MASTER_PORT` | `127.0.0.1` / `62031` | MASTER the terminals log in to (the hotspot proxy port if self-service passwords are wanted) |
| `PEER_SUFFIX` | `98` | 0–99, suffix of the peer ID |
| `SUBSCRIBERS_FILE` | — | RadioID `subscriber_ids.json` for the callsign sent in RPTC (looked up, not loaded) |
| `MAX_SESSIONS` | `500` | Sessions in total. The MASTER's `MAX_PEERS` caps them too, shared with its hotspots: when it is full, the app gets no answer and each retry spends a login |
| `MAX_SESSIONS_PER_IP` | `4` | Sessions from one IP |
| `MAX_LOGINS_PER_MINUTE` | `60` | New sessions (= MASTER logins) per minute |
| `SMS_FORMAT` | `standard` | Text to a radio that has not written first: `standard`, `tms` or `hytera` (a reply goes in the format the radio used) |
| `SMS_PREAMBLES` | `2` | CSBK preambles before a text |
| `LOCATION_DST` | `0` | Private ID that gets the app's GPS position as NMEA data (e.g. a D-APRS plugin); `0` = not sent |

A new terminal over any cap is ignored; refusals are logged once a minute with
their counts. A datagram that is not an Open DMR Terminal keep-alive opens no
session and spends no login, nor does an ID that is not a 24-bit DMR ID. The section is validated at start. Changes need a restart
(`SIGHUP` is ignored; `SIGUSR2` reopens the log files).

## Running

```bash
python adn-server.py --odtp -c adn-odtp.yaml
```

Production: `examples/systemd/adn-odtp.service`. Open `LISTEN_PORT` (UDP) in
the firewall. On stop, every terminal gets a CLOSE and every peer an RPTCL.
