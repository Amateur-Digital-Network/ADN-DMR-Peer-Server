# Behaviour and timers

## Stable control loops

The server uses **Twisted** `LoopingCall` tasks for periodic work: bridge rules, stream trimming, OpenBridge options refresh, alias reload, voice config reload, security downloads, reporting, and maintenance pings.

Intervals are part of the **observable behaviour** of the product (operators and integrators may rely on timing for troubleshooting). Avoid adding **extra** refreshes or duplicate work inside **hot paths** (for example per-packet handlers for OpenBridge) when the same concern is already covered by the scheduled loop—this keeps load predictable and avoids double application of rules.

## Configuration visibility

Runtime state lives in a shared **`config`** dict: options from peers, `SUB_MAP`, OpenBridge control-plane fields (`_bcsq`, `_bcka`), and similar. Adapters update this structure; use cases read it. This matches how the running process is inspected in logs and support scenarios.

## Core timer intervals (operational contract)

The following intervals are part of the current runtime behavior:

| Loop | Interval | Role |
|------|----------|------|
| `rule_timer` | **52s** | Bridge timeout/on-off state progression. |
| `stream_trimmer` | **5s** | Stream cleanup, timeout handling, end-of-call state trimming. |
| `bridge_reset` | **6s** | Bridge reset flag cleanup and pending reset completion. |
| OPTIONS refresh | **event-driven** | Static TG / reflector from **RPTO**, **startup/reload** (`apply_startup_bridges`), **dmrd** no-source fallback. No periodic 26s loop (**D-28**). |
| `dynamic_tg_purge_loop` | **60s** | Purge expired **SINGLE=1** rows from `peer_dynamic_tgs` and in-memory `_PEER_UA_SESSIONS`. |
| `lst_seen` (self-service reconcile) | **120s** | Reconcile `Clients.logged_in` against currently connected peers: connected peers get `logged_in=1`, the rest `0`. Runs with `now=True` on first tick so stale flags clear immediately after a server restart. Prevents the monitor from authenticating disconnected hotspots via login-by-IP. |
| `statTrimmer` | **303s** | Trim stale STAT bridges and transient status entries. |

If you change one of these intervals, document the operational impact for monitoring, loop behavior, and troubleshooting.

## Voice contention constants

These constants define per-packet and per-session behaviour. They are
documented in detail in [Voice routing and contention](routing-and-contention.md).

| Constant | Value | Role |
|---|---|---|
| `STREAM_TO` | **0.36 s** | Window to consider a stream "active" (between packets). |
| `_STALE_PEER_SESSION_TIMEOUT` | **5.0 s** | A per-peer session with no frames is considered dead (lost VTERM). |
| `GROUP_HANGTIME` | **5 s** (config default, per-system) | Blocking period after a QSO ends before another TG is accepted on that slot. |
| `DEFAULT_UA_TIMER` | configurable (minutes, per-system) | Duration of dynamic (User Activated) bridges. |

## In-band VTERM scope

In-band bridge signalling on voice terminator (VTERM) is intentionally scoped to:

- call type **`group`**
- call type **`vcsbk`**

It is not applied on **unit/private** VTERM paths.

## Packet-control behavior notes

Current packet-control behavior for stream dedup and ordering:

- OBP hash duplicate-drop checks are evaluated with **`seq > 0`** guard.
- HBP still computes/stores CRC for `seq == 0`, while duplicate-drop by CRC remains guarded by **`seq > 0`**.
- This avoids over-dropping first-packet edge cases while preserving stream duplicate protection.

<a id="loop-guard"></a>
## Loop guard

Stream-ID loop control (`*LoopControl*`) only catches a stream that comes back with its own stream ID. A transcoding bridge (YSF2DMR, DVSwitch, ASL/EchoLink gateways) re-encodes the audio and starts a **new** stream ID, and each lap restarts the 180 s source timeout. The TG-busy check also lets the same caller through on purpose. So a loop through bridges (TG A → YSF → TG B → … → TG A) would otherwise go round indefinitely.

Those bridges keep the caller's ID, and one person cannot transmit from two ingress points at once. When a group voice stream starts, `application/routing/loop_guard.py` calls it an **echo** when the same `rf_src` has another stream (different stream ID) on the **same TG** from **another ingress** (system and peer on HBP; all OPENBRIDGE links together count as one ingress, the mesh) that is active or ended less than `GLOBAL.LOOP_GUARD_HOLD` seconds ago (default 1 s; a bridge echo starts while the original is still on air, so the hold only has to cover very short overs). The validator keeps the hold under 2.0 s, the parrot's fixed replay delay (`PLAYBACK_DELAY_S`), so the parrot is never matched whatever its system or TG is called. A bridge that relays to **another** TG is not a loop and is left alone; a loop always comes back to the TG it started on.

- Per system `LOOP_GUARD`: `log` (default) writes one `*LoopGuard*` warning per echo stream and lets it through; `true` drops it (routing, and the MASTER's local REPEAT to its other peers); `false` neither checks nor records streams entering there (an extra option for the **ECHO** parrot).
- Exempt: server voice IDs (`all_server_voice_ids`, e.g. 1000001; several servers run beacons with them at once) and plugin / announcement frames.
- Cost: one verdict per stream, on its first frame, cached by stream ID. Frames of a stream already bound to its slot never reach it. The stream trimmer forgets entries after 300 s.
- OBP multipath (the same call over two OpenBridge paths) keeps its stream ID and is not an echo.
- The mesh is one ingress: a remote caller's next over may reach this server first over another OpenBridge link, and that is a re-key, not an echo. A real loop is still caught on the server where the caller is local (HBP vs a bridge peer, or HBP vs the mesh), and so is a local bridge peer echoing a remote caller (the mesh vs HBP).

Not covered: a bridge that transmits with **its own** ID instead of the caller's.
