# Plugins

Drop-in extensions live under `plugins/<name>/`. The server loads them at runtime from this directory (default: `<project_root>/plugins`).

## Reference skeleton

[`example/`](example/) is the only plugin versioned in this repository. It is **disabled by default** (`enabled: false` in `config.yaml`). Use it as a minimal, working template (clean architecture layout, event handling, JSON output on call end).

To try it locally, set `enabled: true` in `plugins/example/config.yaml` and reload the server (`systemctl reload adn-server`).

## Layout

```
plugins/<plugin-name>/
  config.yaml              # enabled: true|false (+ plugin options)
  plugin/
    __init__.py            # create_plugin()
    domain/
    application/
    infrastructure/
  tests/
```

## Documentation

Full framework guide (contract, events, threading, `PLUGINS` server config):

- English: [Plugins (user guide)](../docs/en/server/user-guide/plugins.md)
- Spanish: [Plugins (guía de usuario)](../docs/es/server/user-guide/plugins.md)

Tests for each plugin run from that plugin's directory:

```bash
cd plugins/example
python3 -m pytest tests/ -q
```
