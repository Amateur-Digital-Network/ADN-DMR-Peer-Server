# ADN DMR Peer Server

**Version 2.5.5** — pairs with **adn-monitor 2.0.0-rc.4** (report v2 slim wire + JSON HELLO).

ADN DMR conference bridge server. Configuration is YAML; the codebase follows clean architecture (domain, application, infrastructure). v2 adds integrated **PROXY**, **SubscriptionStore** routing, report v2 to the monitor, and a unified **`adn-server.py`** entrypoint (`--echo`, `--doctor`, `--no-proxy`). See [CHANGELOG.md](CHANGELOG.md).

## License

GPL v3. Derived from FreeDMR / HBlink.

## Requirements

- Python 3.10+
- Dependencies: `pip install -r requirements.txt`
- **ffmpeg** (system package) — required for voice/TTS. Install via your distro:
  - Debian/Ubuntu: `apt install ffmpeg`
  - openSUSE: `zypper install ffmpeg`
  - Fedora/RHEL: `dnf install ffmpeg` or `yum install ffmpeg`

## Configuration

Copy `adn-server.example.yaml` to `adn-server.yaml` and edit with your settings. Production config is not committed.

The example includes an **integrated hotspot proxy** (`PROXY`) and optional **MySQL self-service** (`SELF_SERVICE`). For self-service, install the optional extra: `pip install -e ".[selfservice]"`. See [Hotspot proxy (integrated)](docs/en/server/user-guide/hotspot-proxy.md). Disable standalone **`adn-proxy`** if you use the integrated proxy on the same host.

### Voice configuration

Voice features (announcements, TTS, recording) use a separate config file. Copy `adn-voice.example.yaml` to `adn-voice.yaml` and edit. If the file does not exist, voice features are disabled (no error). Changes are hot-reloaded every 15 seconds.

- **Each item** (ANNOUNCEMENTS, TTS_ANNOUNCEMENTS) has its own `LANGUAGE` and `ENABLED: true` to activate.
- **ANNOUNCEMENT_LANGUAGES** is optional (for voice ident only); announcements/TTS work without it.
- **TTS** requires ffmpeg + vocoder (TTS_VOCODER_CMD or TTS_AMBESERVER_HOST). Pipeline: `.txt` → gTTS → `.mp3` → ffmpeg → `.wav` → vocoder → `.ambe`. First time: create `Audio/<LANG>/ondemand/<FILE>.txt` with the text. See [Voice, announcements, and TTS](docs/en/server/user-guide/voice-and-tts.md).

## Documentation (MkDocs)

Documentation (MkDocs): English under **`docs/en/`**, Spanish under **`docs/es/`** (`server/` and `monitor/` in each). **Install the doc stack first** (includes **Material**); otherwise you may see `Unrecognised theme name: 'material'` if `mkdocs` on your `PATH` is not the same environment.

```bash
python3 -m pip install -r requirements-docs.txt
python3 -m mkdocs build -f mkdocs.yml          # → site/en/
python3 -m mkdocs build -f mkdocs.es.yml       # → site/es/
```

Use the same `python3` you use for the project (e.g. pyenv’s `3.11.8`). Preview the combined tree: `cd site && python3 -m http.server` then open **`/en/`** and **`/es/`**, or run `python3 -m mkdocs serve -f mkdocs.yml` for English only.

Output: **`site/en/`** and **`site/es/`** under gitignored **`site/`**.

## Tests

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest tests/ -q
```

See [Testing](docs/en/server/development/testing.md) in the docs site. File index: [`tests/README.md`](tests/README.md).

## Run

```bash
pip install -r requirements.txt
python adn-server.py
```

Options:

```bash
python adn-server.py -c /path/to/adn-server.yaml
python adn-server.py --logging DEBUG
python adn-server.py --doctor          # config, ports, peers (exit 1 on errors)
python adn-server.py --no-proxy        # disable integrated PROXY
```

## Echo (playback)

Separate process (same binary) records group voice and plays it back on TG 9990.

```bash
cp adn-echo.example.yaml adn-echo.yaml
python adn-server.py --echo -c adn-echo.yaml
```

See [Echo (playback)](docs/en/server/user-guide/echo.md) in the docs site for an overview.

**systemd:** example units in `examples/systemd/` (`adn-server.service`, `adn-echo.service`).

## Acknowledgments

Thanks to **[Esteban Mackay, HP3ICC](https://gitlab.com/hp3icc)**, for ideas, help, testing, and suggestions throughout development.

---

[Español](README_es.md)
