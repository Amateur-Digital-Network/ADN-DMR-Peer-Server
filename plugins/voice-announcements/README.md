# voice-announcements

Scheduled announcements, TTS announcements and voice beacons on talkgroups. This used to be part of the core (`VoiceUseCases`) and now is a plugin that speaks through `ServerContext.send_dmrd`.

**Nothing changes for sysops.** The items are still configured in `adn-voice.yaml` (`VOICE.ANNOUNCEMENTS`, `VOICE.TTS_ANNOUNCEMENTS`, `AUDIO_PATH`, `TTS_*`), with the same keys and behaviour, and changes are picked up within 15 s. The plugin is enabled by default. The server grants it exactly the talkgroups and DMR IDs those items use, unless `PLUGINS.send.voice-announcements` says otherwise.

## Behaviour

It is the same as the core announcements it replaces:

- **Schedule:** `MODE: interval` plays every `INTERVAL` s. `MODE: hourly` plays once in the first minute of each hour.
- **Talkgroups:** one playback per talkgroup at a time; others wait their turn, 1.5 s apart.
- **Slot:** the slot the server chooses (`voice_slot_for_tg`) is where the TG is a dynamic session or has an active bridge on the MASTER, then TS2, then TS1. While every slot is busy, it retries every 5 s (up to 60 times).
- **Routing:** like any announcement, through the TG's bridges (OpenBridge included) and to the MASTER's hotspots.
- **Pacing and collisions:** 58 ms per frame. It stops when a radio takes the slot.
- **TTS:** `.txt` → speech → `.ambe`, encoded in a worker thread and cached, as before.

## Tests

```bash
cd plugins/voice-announcements
PYTHONPATH=../../src python3 -m pytest tests/ -q
```
