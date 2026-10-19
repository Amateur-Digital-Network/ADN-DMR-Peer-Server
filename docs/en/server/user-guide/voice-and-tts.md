# Voice, announcements, and TTS

> **Scheduled announcements and TTS are a plugin** — `plugins/voice-announcements/`, enabled by default. Configuration is unchanged (this file); the plugin follows it every 15 s and the server grants it exactly the talkgroups and DMR IDs its items use (override with `PLUGINS.send.voice-announcements`, see [Plugins](plugins.md#sending-unit-data-and-group-voice-opt-in)). Voice ident, on-demand 999x and disconnected prompts stay in the core.

## Configuration files

- **`adn-voice.yaml`** (optional, not committed) — merged into `config["VOICE"]`.
- Hot reload on file **mtime** (~15 s loop).

Template: `adn-voice.example.yaml`.

## Server voice identity (configurable `DMR_ID`)

All **server-originated** voice uses a configurable **DMR ID** in `adn-voice.yaml`:

```yaml
VOICE:
  DMR_ID: 1000001        # optional; default 1000001

  TTS_ANNOUNCEMENTS:
    - ENABLED: true
      FILE: texto1
      TG: 730500
      DMR_ID: 3109898    # optional; inherits VOICE.DMR_ID when omitted
```

- **`VOICE.DMR_ID`** — default RF source. **Optional** — existing `adn-voice.yaml` files need no change.
- **Per-item `DMR_ID`** — override on each `ANNOUNCEMENTS` / `TTS_ANNOUNCEMENTS` row. Also optional.
- **Callsign** — resolved from the subscriber DB (`users` / `SUB_MAP`) for that DMR ID; not in voice config.

Applies to:

- Scheduled **ANNOUNCEMENTS** and **TTS_ANNOUNCEMENTS** (destination = the **`TG`** field in each item).
- **On-demand** clips triggered by dialling **9991–9999** (playback uses destination **TG 9** on **TS2**; you still key **999x** to request the file).
- **Disconnected / reflector** voice lines (same: **TG 9** / **TS2**).

See [TG 9 — local service lane](special-numbers.md#tg-9-local-service-lane-prompts-and-bridge-plumbing) for why TG 9 is used and what must be enabled on the hotspot.
- **Voice ident** (destination **all-call** or **`OVERRIDE_IDENT_TG`**).

See [Special numbers — server voice source ID](special-numbers.md#server-voice-source-id-configurable) for the full table.

## Features

| Feature | Description |
|---------|-------------|
| **Scheduled announcements** | AMBE phrases from `Audio/<lang>/ondemand/` on a schedule (hourly / interval). |
| **TTS announcements** | Text → gTTS → MP3 → ffmpeg → WAV → vocoder → AMBE (see pipeline below). |
| **Voice ident** | Periodic identification (optional); uses `VOICE_IDENT` in main server config and `OVERRIDE_IDENT_TG` / all-call. |
| **Recording** | When enabled, records AMBE to disk for traffic on **`RECORDING_TG`** / **`RECORDING_TIMESLOT`** (see template `adn-voice.example.yaml`). |
| **On-demand (9991–9999)** | See [Special numbers](special-numbers.md). |

## TTS pipeline (summary)

1. Source text in `.txt` under `Audio/<language>/ondemand/`.
2. **gTTS** produces **MP3**.
3. **ffmpeg** converts to **WAV**.
4. Vocoder command or **AMBEServer** produces **AMBE** for transmission.

**ffmpeg** must be installed on the host OS.

Configure **`TTS_VOCODER_CMD`** or **`TTS_AMBESERVER_HOST`** / **`TTS_AMBESERVER_PORT`** in `adn-voice.yaml` for encoding; see comments in `adn-voice.example.yaml`.

## Anti-collision (QSO) with announcements

The plugin plays **one announcement per TG at a time**; others for the same TG wait in order, 1.5 s apart. An item that fires while its TG is on air is retried every 3 s. Before choosing a slot it waits until the TG has been quiet for `tg_quiet_s` (the plugin's `config.yaml`, default 3 s; `0` turns it off): no over starting or ending on that TG from any hotspot, MASTER, bridge or OpenBridge, so an announcement never starts in the pause between two overs of a QSO. While voice is live it checks again every 5 s; in a pause it starts as soon as the TG has been quiet long enough. The slot comes from `voice_slot_for_tg` (where the TG has a dynamic session or an active bridge leg on the MASTER, then TS2, then TS1); while every slot is busy it retries every 5 s, up to 60 times, then skips that playback. Frames are paced at 58 ms after a 0.5 s start delay.

The plugin sends each frame through `send_dmrd`, which the core injects as a **synthetic hotspot PTT** on `PROXY.TARGET_SYSTEM` (the same MASTER used by the integrated proxy). Routing fans out to bridged hotspots and OPENBRIDGE legs; local peers on that MASTER still receive frames via `send_system`. Routed voice may still take the slot's TX leg (each hotspot keeps the stream it hears first); only a radio or another plugin stream on the RX leg stops the announcement. A frame routing refuses is still played to the MASTER's hotspots, with one WARNING per stream.

Server prompts from the core (voice ident, on-demand 999x, the disconnected prompt) hold **TS2** while they play; a radio or another stream on the slot stops them. An announcement from another server arriving over OpenBridge is ordinary mesh voice and does not get this hold.

## Broadcast queue

Parallel **broadcasts** on **different** TGs may run concurrently; **same-TG** broadcasts are **serialised** so one announcement completes before another on that TG.

## See also

- [Configuration](configuration.md) — voice file paths.
- [Echo](echo.md) — separate playback service.
- [Special numbers](special-numbers.md) — 5000, 999x, recording-related behaviour.
