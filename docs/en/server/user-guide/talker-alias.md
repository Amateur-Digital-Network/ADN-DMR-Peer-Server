# Talker Alias (DMR)

DMR **Talker Alias** (ETSI TS 102 361-2, 2016) carries a short alphanumeric label in the voice stream. On the Homebrew Protocol (HBP) it appears as separate **`DMRA`** UDP packets (15 bytes each, up to four per transmission).

This is **not** the same as subscriber ID aliases in `subscriber_ids.json` used for logs and the monitor UI. Talker Alias is embedded signaling intended for **radio displays** (OLED, Hytera/MD380tools, etc.).

See also: [Configuration](configuration.md#talker-alias-global).

---

## How ADN handles Talker Alias

When enabled on a **MASTER** system, the server can:

| Mode | Behaviour |
|------|-----------|
| **`both`** (default) | Prefer the source's own TA, fall back to inject. The server briefly waits (≈2 s after `VHEAD`) for the source's TA, decoded from either a valid MMDVM `DMRA` buffer or the embedded LC inside voice (FLCO 4–7). If found, it is **passed through unchanged**. If the source sent **no** TA within that window (e.g. a radio that does not support Talker Alias), ADN **injects** the configured template (embedded LC + `DMRA`). When the **source is an OpenBridge** system (which never carries TA), there is nothing to wait for, so ADN injects immediately at `VHEAD`. |
| **`passthrough`** | Relay the source's TA only (buffered `DMRA` and the source embedded LC, both untouched). Never inject the template. |
| **`inject`** | Always build TA from the configured template and alias data, overwriting the embedded LC and sending `DMRA` packets. |

On **bridge forward** at voice header (`VHEAD`), when **`TALKER_ALIAS_SEND_DMRA`** is `true`, the server may send up to four `DMRA` packets to each HBP target (**MASTER** peers or **PEER** upstream) once per stream, then forwards `DMRD` as usual. With the default (`false`), only embedded LC is used — closer to legacy ADN masters that often show **no** `DMRA` on the wire.

**MMDVMHost / DMRGateway (Pi-Star, WPSD):** stock MMDVMHost does **not** consume standalone downlink `DMRA` UDP; it decodes Talker Alias from **embedded LC inside `DMRD` voice** (FLCO 4–7).

The embedded LC of voice bursts **B–E** (dtype 1–4) is always rewritten with the **destination** group LC (re-encoded for the forwarded TGID, as legacy `bridge.py` does) — this is required for the receiving MMDVM to accept the voice; a stale embedded LC from the source TG causes packet loss. The Talker Alias is then **overlaid on alternate superframes**:

- **`inject`** and the **`both`** no-source-TA fallback use the configured template.
- **`passthrough`** and **`both`** with a source TA re-encode the source's own Talker Alias (decoded from its `DMRA`/embedded-voice blocks) and overlay that, so the radio's alias reaches the far side while the group LC stays correct for the new TG.

On the **same MASTER**, when **`REPEAT`** copies group voice to other logged-in hotspots, the server also sends those four `DMRA` packets on `VHEAD` (excluding the transmitting peer). Bridge forwarding to the same system shares the same once-per-stream dedupe, so TA is not sent twice.

**Not supported:** OpenBridge legs (no standard `DMRA` on OBP/DMRE wire). Legacy ADN never implemented TA beyond debug logging.

---

## Configuration

Under **`GLOBAL`** (optional per-system override with the same keys):

```yaml
GLOBAL:
  TALKER_ALIAS: true
  TALKER_ALIAS_MODE: both
  TALKER_ALIAS_FORMAT: "TA {callsign} {fname}"
  TALKER_ALIAS_TEXT_FORMAT: "utf8,iso8"
  TALKER_ALIAS_SEND_DMRA: false
```

| Key | Meaning |
|-----|---------|
| **TALKER_ALIAS** | Master switch (`false` if omitted; the example config enables it). |
| **TALKER_ALIAS_MODE** | `both`, `passthrough`, or `inject`. Default **`both`** if omitted. |
| **TALKER_ALIAS_FORMAT** | Python format string; fields: `{callsign}`, `{fname}`, `{surname}`, `{id}`. |
| **TALKER_ALIAS_TEXT_FORMAT** | TA payload encoding: `utf8` (Motorola / MMDVMHost default), `iso8` (ISO-8859-1, many Hytera models), or `7bit` (oldest radios). Comma-separated list (e.g. `utf8,iso8`) emits **both** encodings back-to-back in embedded LC so each vendor can pick the format it displays; standalone `DMRA` UDP uses the **first** format only. Default **`utf8`**. |
| **TALKER_ALIAS_SEND_DMRA** | Send standalone HBP `DMRA` UDP on VHEAD (`false` by default). Radios / MMDVMHost use **embedded LC** in `DMRD`; leaving this off matches typical legacy wire (no `DMRA` toward the peer). Set `true` only if a client needs the UDP packets. |

Maximum string length is **29 characters** (ETSI / MMDVMHost). This limit is fixed in code and is **not** configurable, to avoid incompatible payloads on radios and hotspots.

Subscriber JSON may include `fname`, `surname`, or a dedicated `talker_alias` field per record.

---

## HBP `DMRA` wire format

| Offset | Field |
|--------|--------|
| 0–3 | `DMRA` |
| 4–6 | Source DMR ID (3 bytes, big-endian) |
| 7 | Block index 0–3 |
| 8–14 | 7 payload bytes |

Encoding follows **TALKER_ALIAS_TEXT_FORMAT** (ETSI formats 0/1/2: 7-bit, ISO-8859-1, UTF-8), matching MMDVMHost `DMRTA.cpp` when set to `utf8`.

Embedded TA in `DMRD` voice alternates superframes: one cycle (bursts B–E) with the normal group embedded LC, the next with a TA block (FLCO 4–7), repeating until the stream ends.

---

## Pi-Star / MMDVM operator settings

For TA to reach the **local RF radio**:

| Setting | Recommendation |
|---------|----------------|
| **DMR DumpTAData** | `1` (on, Pi-Star default): write embedded Talker Alias to the MMDVM log — required for Pi-Star dashboard / log-based tools to show TA; with `0` nothing appears there. This does **not** block TA on RF. |
| **DMR EmbeddedLCOnly** | `off` (default). If `on`, Talker Alias from the network is disabled. |

Pi-Star’s web dashboard does not show TA; use the radio OLED, MD380tools, or tools such as [pistar-lastqso](https://github.com/kencormack/pistar-lastqso).

**Radio compatibility:** Hytera PD6/7/9 and MD380tools firmware generally work. Many Motorola radios do not support TA; some older firmware may show audio issues when TA is present.

The **adn-monitor proxy** already forwards `DMRA` to the master unchanged.

---

## Limitations and follow-up work

| Topic | Status |
|-------|--------|
| TA on **OpenBridge** targets | Not available (no `DMRA` on standard OBP wire). |
| **Monitor dashboard** live TA column | Separate work (monitor uses DB aliases, not live `DMRA`). |
| Unrelated **`RuntimeError`** in bridge iteration | Tracked as a separate fix. |

---

## References

- [ETSI TS 102 361-2](http://www.etsi.org/deliver/etsi_ts/102300_102399/10236102/02.03.01_60/ts_10236102v020301p.pdf) (Talker Alias format)
- [MMDVMHost `DMRTA.cpp`](https://github.com/g4klx/MMDVMHost/blob/master/DMRTA.cpp) / [`DMRNetwork.cpp`](https://github.com/g4klx/MMDVMHost/blob/master/DMRNetwork.cpp) (`writeTalkerAlias`)
