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

When scheduling announcements, the server may **wait** if target slots are busy, **drop** targets if a live QSO appears mid-stream, and only mark **hourly** announcement state after a successful target list — this avoids clobbering live traffic.

The plugin sends each frame through `send_dmrd`, which the core injects as a **synthetic hotspot PTT** on `PROXY.TARGET_SYSTEM` (the same MASTER used by the integrated proxy). Routing fans out to bridged hotspots and OPENBRIDGE legs; local peers on that MASTER still receive frames via `send_system`. While it plays, the stream holds that MASTER slot; a radio taking the slot stops it.

## Broadcast queue

Parallel **broadcasts** on **different** TGs may run concurrently; **same-TG** broadcasts are **serialised** so one announcement completes before another on that TG.

## See also

- [Configuration](configuration.md) — voice file paths.
- [Echo](echo.md) — separate playback service.
- [Special numbers](special-numbers.md) — 5000, 999x, recording-related behaviour.
