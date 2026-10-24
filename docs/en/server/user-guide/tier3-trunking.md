# DMR Tier III trunking (dmrtc + DMRGateway)

A **MASTER** with **`TRUNKING: true`** accepts **DMR Tier III sites**. A site is the **dmrtc** trunking controller behind **DMRGateway** with **`TrunkingEnabled=1`**. Both speak the **Homebrew DMR trunking protocol v0.6**. A site logs in like a hotspot under other command names, subscribes the talkgroups it wants, registers its radios, and carries **many calls at once** over one link.

Wire format: [Tier III protocol](../protocols/tier3.md).

## What it is {#what-it-is}

```text
Tier III radios
      │ RF
MMDVMHost (one per RF channel)
      │
dmrtc (trunking controller)
      │
DMRGateway, TrunkingEnabled=1
      │ UDP: DTC* login, 71-byte DMRD, DMRT signalling
adn-server: SYSTEMS.TRUNK (MODE: MASTER, TRUNKING: true)
      │ BRIDGES
hotspots, other MASTERs, OpenBridge, other Tier III systems
```

Each DMRGateway network that points at the server is one **site**: one peer of the TRUNKING system, with its own ID.

| | Hotspot (classic HBP) | Tier III site |
|---|---|---|
| Login | `RPTL` / `RPTK` / `RPTC` / `RPTO`, `RPTPING` | `DTCL` / `DTCK` / `DTCC` / `DTCO`, `DTCPING` |
| Voice frames | `DMRD`, 55 bytes | `DMRD`, 71 bytes (55 + a 16-byte call UUID) |
| Talkgroups | OPTIONS, statics, user-activated TGs | DMRT **subscriptions**, held until the site drops them or leaves |
| Calls at once | 2 (one per timeslot) | Many: no timeslot contention |
| Where a radio is | Learned from traffic (`SUB_MAP`) | DMRT **registration**, then traffic |

## Requirements {#requirements}

- **A MASTER of its own** with `TRUNKING: true`, its own `PORT`, and a `PASSPHRASE` or per-ID passwords in `GLOBAL.USERS_PASS`.
- **Not the integrated proxy.** A TRUNKING system cannot be `PROXY.TARGET_SYSTEM`, so the hotspot proxy and its self-service (`SELF_SERVICE`) do not apply to sites.
- **DMRGateway** with `TrunkingEnabled=1` and **dmrtc** with *Use trunking protocol* on. A classic HBP client is refused on this system.
- **7-digit radio IDs** for private calls and unit data, as for hotspots.

## Server configuration {#server-configuration}

The commented `TRUNK:` block in `adn-server.example.yaml` is the template:

```yaml
SYSTEMS:
  TRUNK:
    MODE: MASTER
    ENABLED: true
    TRUNKING: true
    IP: ""
    PORT: 62040                 # the Port in DMRGateway's [DMR Network n]
    PASSPHRASE: "change-me"
    MAX_PEERS: 4                # number of sites
    REPEAT: true                # site-to-site calls on this system
    GROUP_HANGTIME: 5
    USE_ACL: true
    REG_ACL: DENY:1             # site IDs
    SUB_ACL: DENY:1             # radio IDs (registrations and voice)
    TGID_TS1_ACL: PERMIT:ALL
    TGID_TS2_ACL: PERMIT:ALL
    DEFAULT_UA_TIMER: 10
    ALLOW_UNREG_ID: false
```

- **`MAX_PEERS`** is the number of sites the system accepts.
- **`REPEAT`** (default `true`): a call from one site goes to the other sites of the same system that subscribed its talkgroup. With `false`, sites of one system do not hear each other directly; their calls still go through the bridges.
- **Authentication** works as for hotspots: a per-ID password from `GLOBAL.USERS_PASS` first, else the system `PASSPHRASE`. `REG_ACL`, `ALLOW_UNREG_ID` and `MAX_PEERS` apply to site IDs.
- **`DEFAULT_UA_TIMER`**: a call from a site on a talkgroup it did not subscribe arms a user-activated leg, as a hotspot's does. Keep the timer low (see [Limitations](#limitations)).
- **`ALIASES.TRUNK_REG_FILE`** (default `trunk_registrations.json`, under `ALIASES.PATH`): where registrations are kept (see [Registrations](#registrations)).

### Rules the validator enforces {#validation}

With `TRUNKING: true` (enabled or not), the configuration is refused when it finds:

| Setting | Why |
|---------|-----|
| `MODE` other than `MASTER` | Sites log in to a MASTER. |
| The system is `PROXY.TARGET_SYSTEM` | The integrated proxy carries neither DTC* logins nor DMRT. |
| `GENERATOR` above 1 | One port carries every site; use `MAX_PEERS`. |
| `SINGLE_MODE: true` | A site holds many talkgroups at once. |
| `TS1_STATIC` / `TS2_STATIC` not empty | Sites subscribe their own talkgroups. |
| `DEFAULT_REFLECTOR` other than 0 | Not supported on a trunking system. |
| An `OPTIONS` key | Not supported on a trunking system. |
| `SUB_MAP_LEARN: false` | Registrations are kept in `SUB_MAP`. |

Because of these rules, every static leg of a TRUNKING system is a site's subscription.

### ACLs {#acls}

Each scope (`GLOBAL`, then the system) applies only when its `USE_ACL` is `true`, as for voice:

- **Registration** of a radio is accepted when `SUB_ACL` permits its ID in every active scope. ID 0 is always refused.
- **Subscription** to a talkgroup is accepted when **both** `TGID_TS1_ACL` and `TGID_TS2_ACL` permit it in every active scope. dmrtc sets the slot bit of its voice frames freely, and voice from a site is checked against the list of that slot, so a talkgroup one slot denies is refused at subscription time instead of being dropped on some calls.

### Registrations {#registrations}

- A site can hold **1024** registered radios and **512** talkgroups.
- A registration lasts **24 h**. Registering again, or traffic from the radio at its site, starts the 24 h again.
- A radio is at one site at a time. When it registers at another site, the old site gets a de-registration (`0x42`) for it if it is connected.
- While it is valid, a registration is the radio's `SUB_MAP` entry, so private calls and unit data reach its site (see [Private calls](private-calls.md)). Traffic from the radio heard elsewhere afterwards moves that entry, as for any radio.
- Registrations survive a site logging in again (dmrtc does not register its radios a second time) and a server restart: they are written to `ALIASES.TRUNK_REG_FILE` every 300 s when they changed or the last write failed, before a `SIGHUP` reload and at shutdown, in a worker thread so a write never holds up calls, and read once, when the first TRUNKING system starts. The file is written to a `.tmp` and renamed. Expired entries, and entries of systems that are no longer TRUNKING, are dropped when it is read.
- Subscriptions are not saved: dmrtc subscribes again after every login.

### Reload and restart {#reload}

- **`SIGHUP`** keeps the sites, their subscriptions and their registrations. A change of `MAX_PEERS`, `REPEAT` or the ACLs applies without dropping them.
- **Setting or removing `TRUNKING`** on an existing system recreates its protocol: its peers are dropped and log in again.
- **Removing the system** sends `DTCCL` to its sites, releases their talkgroups and forgets their registrations.
- **A restart** starts with no sites. DMRGateway logs in again by itself, dmrtc subscribes again, and the saved registrations are pinned again as each site logs in.

## DMRGateway {#dmrgateway}

`TrunkingEnabled` is a `[General]` key, so it applies to every network of that DMRGateway instance, and every DMRT message from dmrtc goes to every enabled network. Use one instance for the trunking link and enable only the server's network in it.

```ini
[General]
TrunkingEnabled=1

[DMR Network 1]
Enabled=1
Name=ADN
Address=adn.example.org
# The PORT of the TRUNKING system
Port=62040
# The site ID: the peer ID the server sees (one per site)
Id=214901
# The system PASSPHRASE, or the site ID's own password
Password=change-me
PassAllTG=1
PassAllTG=2
PassAllPC=1
PassAllPC=2
```

- `PassAllTG` passes every talkgroup; `PassAllPC` lets private calls through, in both directions.
- The ports between DMRGateway and dmrtc are described in dmrtc's operation manual (`docs/operation_manual.md` in the [dmrtc repository](https://github.com/qradiolink/dmrtc)).

## dmrtc {#dmrtc}

In the dmrtc settings:

- **Gateways**: the DMRGateway instance above with gateway type **1** (trunking protocol).
- **Use trunking protocol** (`use_trunking_protocol`): on. It turns on DMRT signalling.
- **Send network registrations** (`send_network_registrations`): on. Without it the server does not know where a radio is until it transmits.
- **Subscribe static talkgroups** (`subscribe_static_tgs`): on. dmrtc subscribes its static talkgroups after each login.

## What works {#what-works}

- **Login and keepalive** with `DTCL` / `DTCK` / `DTCC` / `DTCO` and `DTCPING` / `DTCPONG`. The final `DTCACK` makes DMRGateway tell dmrtc that it is logged in, and dmrtc then sends its subscriptions.
- **Registrations**: persisted, capped per site and expiring (see [Registrations](#registrations)).
- **Subscriptions**, confirmed talkgroup by talkgroup. A subscription has no timeslot and no timer: it lasts until the site unsubscribes or leaves. A talkgroup with no bridge yet is created as a user-activated one would be.
- **Concurrent group calls in both directions.** Every call on a subscribed talkgroup reaches the site at the same time as the others, with no slot contention and one TX state per call. Calls from a site are routed one by one: duplicate and loss control, loop control, start and end reports and the talkgroup's single-conversation rule work per call.
- **71-byte frames** to sites, with the call's UUID: the originating site's own for a call from a site, else a stable UUID derived from the stream.
- **Site to site**: on the same system through `REPEAT`; between TRUNKING systems through the bridges. A site never gets its own call back.
- **Private calls and unit data** to a registered radio (7-digit ID) go to its site only, once, never as a broadcast. From a site they reach hotspots and OpenBridge as usual.
- **Parrot (9990)**: each site hears only the playback of its own callers, even when two sites use it at once.
- Talker alias embedded in the voice frames reaches sites, as it reaches hotspots.

## EXPERIMENTAL: short data, status and private-call setup {#experimental-dmrt}

The protocol also defines DMRT messages for short data (UDT, `0x05`, and its accept, `0x06`), private-call setup, accept and reject (`0x07`–`0x09`) and status (`0x0A`). The server routes them **between Tier III sites only**, and they are **EXPERIMENTAL**: they have no field use yet.

- **dmrtc today** (commit `8b51a34`) sends `0x05` when a radio sends a short message to a group or to a radio that is not at the site, and builds but never sends `0x07`–`0x0A`. Its controller (`processDMRNetworkMessage` in `controller.cpp`) requires the network bit and then compares the opcode with `0x05` and `0x06`, so it ignores the `0x45` and `0x46` the server forwards: short messages between sites do not reach RF yet.
- **The source must be real.** The source radio must be registered at the sending site, and `SUB_ACL` (`GLOBAL` and the system, each when its `USE_ACL` is on) must permit it. Anything else is dropped and logged, once a minute per site, opcode and radio.
- **Group messages follow the bridges**, as voice does: the other sites of the sender's system that subscribed the talkgroup (when `REPEAT` is on), and the subscribed sites of another TRUNKING system only when the talkgroup's bridge has active legs for both systems on that same talkgroup.
- **Private messages** go to the site where the destination radio is registered, if it is connected.
- **Answers.** An accept or reject goes back to the sender only when it comes, within 60 s, from a site the message was sent to. In that time no other site can reuse the message's UUID. A call setup that no other connected site can take is answered with a reject (`0x49`).
- There is no conversion between DMRT short data and the DMR data frames (SMS) hotspots send.

Lines to expect:

```text
(TRUNK) EXPERIMENTAL DMRT 0x05 from site 214901, SUB 2141234 to 2145678: forwarded to ['TRUNK/214902']
(TRUNK) EXPERIMENTAL DMRT 0x06 from site 214902 returned to TRUNK/214901
(TRUNK) EXPERIMENTAL DMRT 0x05 from site 214901: SUB 2149999 not registered there or denied by SUB_ACL, dropped
(TRUNK) EXPERIMENTAL DMRT call setup from site 214901, SUB 2141234 to 2149999: not at another site, rejected
```

Wire format: [Tier III protocol](../protocols/tier3.md#dmrt).

## Limitations {#limitations}

- **Concurrent site calls as other systems see them.** The golden rule, loop control and the loop guard of other systems read the latest call per slot bit, as for a MASTER shared by several hotspots. A hotspot keying a talkgroup that one of several concurrent site calls uses may not be held off.
- **No TG 4000 from sites.** TG 4000 from a site is ignored, so it cannot drop the site's subscriptions; there is no TG 4000 voice reply either.
- **Not relayed from sites**: talker alias (`DMRA`, and the alias embedded in a site's voice frames), voice recording (`RECORDING_TG`) and play-file requests (private calls to 9991–9999).
- **Unsubscribed talkgroups.** A call from a site on a talkgroup it did not subscribe arms a user-activated leg for `DEFAULT_UA_TIMER` minutes. The site only hears its subscribed talkgroups, so calls routed to that leg reach nobody until the timer ends.
- **Stale registrations.** A radio that leaves while the server is down stays registered until it expires or registers elsewhere. Calls to it are dropped, never broadcast.
- **Talkgroups a site cannot subscribe** (`0` in the confirmation): 0–5, 9, 4000, 5000, 9990–9999, anything above 16777215, any talkgroup one of the slot ACLs denies, and anything beyond 512 per site.
- **Strict lengths.** A site's voice frame must be exactly 71 bytes and `DTCC` exactly 302 (304 on 23 cm, where both frequencies take ten digits). The byte layouts follow DMRGateway (commit `ee79d38`) and dmrtc (commit `8b51a34`); a client that sends other lengths is not understood.
- **Unit data to an ID with no `SUB_MAP` entry** can match a site's ID by its first 6 or 7 digits and be sent to that site, as it is to a hotspot (existing behaviour).
- **Server status probes** (`RPTL` with ID 4294967295) are refused on a TRUNKING system. Point them at another MASTER.
- **Short data, status and private-call setup** between sites are EXPERIMENTAL (see [above](#experimental-dmrt)).

## Logs to expect {#logs}

With the system `TRUNK`, sites 214901 and 214902 and radio 2141234:

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

Malformed or unhandled DMRT messages, `DTCG`, and frames of the wrong length are logged at `DEBUG`.

## Troubleshooting {#troubleshooting}

| Symptom | Cause and fix |
|---------|---------------|
| `Unrecognized command. Raw HBP PDU: b'DTCL...'` | The site reached a system without `TRUNKING: true`: a wrong `Port` in DMRGateway, or the key is missing. |
| `classic HBP login refused on a TRUNKING system (DMRGateway needs TrunkingEnabled=1)` | A hotspot, or a DMRGateway with `TrunkingEnabled=0`, points at the trunking port. |
| DMRGateway keeps retrying the login | A wrong `Password` or `Id`, `REG_ACL`, an unregistered ID with `ALLOW_UNREG_ID: false`, or `MAX_PEERS` reached. The server logs `FAILED the login exchange`, `Invalid Login` or `Maximum number of peers exceeded`. |
| DMRGateway logs `Logged into the master via DTC protocol successfully`, but the server never logs `subscribed` | In dmrtc, *Use trunking protocol* or *Subscribe static talkgroups* is off, no static talkgroup is assigned to the trunking gateway, or the gateway type is not 1. |
| `refused [...]` after `subscribed` | A talkgroup from the list in [Limitations](#limitations): reserved, denied by one slot's ACL, or over 512. |
| Private calls to a radio at a site are not delivered | *Send network registrations* is off and the radio has not transmitted, or its registration expired. Look for `registered [...]` with its ID. |
| Sites of the same system do not hear each other | `REPEAT: false` on the TRUNKING system. |
| Short messages between sites never show on the radios | dmrtc does not act on `0x45` yet (see [EXPERIMENTAL](#experimental-dmrt)). The server log shows whether it forwarded the message. |

## See also

- [Tier III protocol](../protocols/tier3.md): login commands, the 71-byte DMRD, DMRT messages.
- [Configuration](configuration.md): `TRUNKING` and `ALIASES.TRUNK_REG_FILE`.
- [Private calls](private-calls.md): `SUB_MAP` and unit data.
- [dmrtc](https://github.com/qradiolink/dmrtc) and [DMRGateway](https://github.com/g4klx/DMRGateway).
