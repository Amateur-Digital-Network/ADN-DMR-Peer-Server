# DMR Tier III: Homebrew DMR trunking protocol v0.6

## Role

The server speaks the **Homebrew DMR trunking protocol v0.6**, as implemented by **DMRGateway** (`TrunkingEnabled=1`) and **dmrtc**, on every MASTER with `TRUNKING: true`, and only there. It is HBP with three changes:

1. every login and keepalive command is renamed (`DTC*`);
2. `DMRD` frames carry a 16-byte **call UUID** (71 bytes);
3. a new **`DMRT`** message carries signalling: radio registrations and talkgroup subscriptions.

Setup and behaviour for operators: [DMR Tier III trunking](../user-guide/tier3-trunking.md). The byte layouts below follow DMRGateway `DMRNetwork.cpp` (commit `ee79d38`) and dmrtc `networksignalling.cpp` / `controller.cpp` (commit `8b51a34`).

## Login and keepalive

The layouts are those of the classic HBP twin; only the name changes. Every command is compared in full and must have exactly its length; a `DTCL`, `DTCK`, `DTCC`, `DTCPING` or `DTCCL` of another length is logged at `DEBUG` and changes nothing.

| Site sends | Length | Server answers | Classic twin |
|------------|--------|----------------|--------------|
| `DTCL` + site ID (4) | 8 | `DTCACK` + salt (4) | `RPTL` |
| `DTCK` + site ID + SHA-256(salt ‖ password) | 40 | `DTCACK` + site ID, or `DTCNAK` + site ID | `RPTK` |
| `DTCC` + site ID + configuration (294) | 302, or 304 on 23 cm (DMRGateway prints each frequency with `%09u`, so 1 GHz or more takes ten digits) | `DTCACK` + site ID, or `DTCNAK` + site ID | `RPTC` |
| `DTCO` + site ID + options text | any | `DTCACK` + site ID; the options are logged at `DEBUG` and never applied | `RPTO` |
| `DTCPING` + site ID | 11 | `DTCPONG` + site ID | `RPTPING` / `MSTPONG` |
| `DTCCL` + site ID | 9 | nothing; the site is removed | `RPTCL` |
| `DTCG` + site ID + position | any | nothing (ignored) | `RPTG` |

Sent by the server:

| Server sends | When |
|--------------|------|
| `DTCNAK` + site ID | A refused login: password, `REG_ACL`, an unregistered ID, `MAX_PEERS`, the callsign. Also the answer to a `DTCK`, `DTCC`, `DTCO` or `DTCPING` from a site that is not logging in or logged in there. |
| `DTCCL` + site ID | The site timed out, shutdown, or the system was removed or recreated. |
| `MSTNAK` + site ID | The answer to every classic `RPTL`, `RPTK`, `RPTC`, `RPTO` and `RPTPING`: the gateway lacks `TrunkingEnabled=1`, so it would not read a `DTCNAK`. |

- The **final** `DTCACK` (after `DTCC`) makes DMRGateway send MasterLogin (`0xC1`) to dmrtc, which then subscribes its talkgroups; a `DTCCL` makes it send `0xC2`.
- Authentication is the classic MASTER's: `REG_ACL`, per-ID password from `GLOBAL.USERS_PASS` before `PASSPHRASE`, `MAX_PEERS`, and the callsign check when `ALLOW_UNREG_ID` is false.
- A `DTC*` command on a system without `TRUNKING` is logged as `Unrecognized command`, as before.

## DMRD (71 bytes)

| Bytes | Field |
|-------|-------|
| 0–3 | `DMRD` |
| 4 | Sequence |
| 5–7 | Source ID |
| 8–10 | Destination (TG or unit) |
| 11–14 | Site ID |
| 15 | Bits: slot (0x80), unit call (0x40), frame type (0x30), data type / voice sequence (0x0F) |
| 16–19 | Stream ID |
| 20–52 | DMR payload (33 bytes) |
| 53–54 | BER, RSSI |
| 55–70 | **Call UUID** (16 bytes) |

The UUID is the call's identity on the trunking side: dmrtc derives its stream ID from it, ignores the slot bit, and carries many calls at once over one link. Every frame of a call carries the same UUID, and each call has its own.

**From a site:**

- Only exactly 71 bytes, from a logged-in site at its address, are accepted; anything else is logged at `DEBUG`.
- The UUID is remembered for the stream, and routing gets the classic 55 bytes: hotspots get 55 bytes and OpenBridge 53, as from any hotspot.
- Each stream has its own receive state, so concurrent calls with the same slot bit do not disturb each other.
- TG 4000 is ignored.

**To a site:** always 71 bytes. The server writes the site's ID at 11–14, pads BER / RSSI to 55 bytes and appends the stream's UUID: the one the originating site sent, else a stable one, the 16-byte BLAKE2b of `ADN-TIER3` followed by the stream ID.

| Frame | Sites that get it |
|-------|-------------------|
| Group, TG 9990–9999 | Only the site where that caller keyed the TG |
| Other group | Every connected site of the system subscribed to the TG |
| Unit (private voice, data) | The site in the destination's `SUB_MAP` entry, when it is on this system; else none |

A site never gets its own frames back, and unit data reaches a site once even when two routing paths deliver it. `DMRA` (talker alias) is not sent to sites.

## DMRT

`DMRT` (4 bytes), the opcode at byte 4, then its fields. Addresses are 24-bit big-endian; timestamps are 64-bit little-endian Unix seconds; a message is at most 255 bytes. Bit `0x40` of the opcode marks a message from the network. `DMRT` carries no site ID: the server takes the site from the datagram's source address, and only from a logged-in site.

| Op | Name | Length (bytes) | Layout after byte 4 | Server |
|----|------|----------------|---------------------|--------|
| `0x01` | Registration | 14 + 3n (n = 1..8) | [5–12] timestamp, [13] n, [14…] radio IDs | Supported. Each radio is accepted unless it is 0, `SUB_ACL` denies it, or its site holds 1024 radios. If it was at another connected site, that site gets a `0x42` for it. Answer: one `0x41`. |
| `0x41` | Registration confirmation | 15 + 3n | [5–12] timestamp, [13] status bits (bit i = ID i, 1 = accepted), [14] n, [15…] IDs | Sent. |
| `0x02` | De-registration | 14 + 3n | as `0x01` | Supported. Forgotten only when the radio is registered at this site. Always answered with `0x42` echoing the IDs. |
| `0x42` | De-registration from the network | 14 + 3n | as `0x01` | Sent as the answer to `0x02`, and to the old site when a radio roams. |
| `0x03` | Group subscription | 6 + 3n (n = 1..64) | [5] n, [6…] TGs | Supported. Each TG is accepted unless it is reserved, a TG ACL of either slot denies it, or the site holds 512. Answer: `0x43` with each TG, `0` for each refused one. |
| `0x04` | Group un-subscription | 6 + 3n | as `0x03` | Supported. Answer: `0x44` echoing every TG. |
| `0x05`–`0x0A` | UDT, UDT accept, private call setup / accept / reject, status | | | Ignored. |
| `0x40` set, `0x80` and above | From the network; local to the site | | | Ignored. |

The length must be exactly what dmrtc builds. A malformed or ignored message is logged at `DEBUG` once a minute per site and opcode.

## State

| State | Lifetime |
|-------|----------|
| Registration (one site per radio, across all TRUNKING systems) | 24 h, restarted by a new registration or by the radio's traffic at its site; until de-registration, roaming, or the system's removal. Saved to `ALIASES.TRUNK_REG_FILE`. |
| Talkgroups per site | Until un-subscription or the site leaves |
| Call UUIDs | 300 s |
| Parrot keyer per system, TG and caller | 185 s after the caller's last over |
| Receive state per site stream | 60 s after its last frame |

In routing, the first site of a system to subscribe a TG gives the system static, active legs on TS1 and TS2; when the last one drops it, the legs go back to idle in-band legs. Every 10 s the server re-arms the legs a bridge reset idled and expires old registrations.

## Implementation

| Layer | Module |
|-------|--------|
| Wire format | `domain/trunking.py` |
| Registrations, subscriptions, destinations | `application/trunking/` |
| Site talkgroups as static legs | `application/subscription/site_tg_ops.py` |
| HBP MASTER for sites | `infrastructure/twisted_adapters/udp_hbp_trunk.py` (`TrunkSiteProtocol`) |
| Registration file | `infrastructure/persistence/trunk_registration_store.py` |
| Assembly | `infrastructure/bootstrap/trunking.py` |

Routing knows no Tier III type: a site hands each stream's receive state to `dmrd_received` (`rx_row`), and `set_concurrent_systems` names the systems that take every call at once.

## References

- Specification: `docs/Homebrew_DMR_trunking_protocol_v0.6.pdf` in the [dmrtc repository](https://github.com/qradiolink/dmrtc).
- Client: [DMRGateway](https://github.com/g4klx/DMRGateway) `DMRNetwork.cpp`.
- [HBP (DMRD)](hbp.md) for the classic frame.
