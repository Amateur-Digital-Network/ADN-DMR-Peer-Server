# Example plugin

Minimal **reference skeleton** for adn-server drop-in plugins. Disabled by default.

## What it does

- On every bus event: `DEBUG` log line `(EXAMPLE) …`.
- On `VoiceCallEnd` / `UnitDataEnd`: writes one JSON file under `example-events/` with call metadata, duration, and frame/packet counts.

No HTTP, no filters, no secrets — study or copy this tree when authoring your own plugin.

## Enable locally

```bash
# Edit plugins/example/config.yaml → enabled: true
systemctl reload adn-server   # or SIGHUP
```

After a voice or unit-data session, check `example-events/<stream_id>.json` under the server project root.

## Tests

```bash
cd plugins/example
python3 -m pytest tests/ -q
```

See also the [Plugins user guide](../../docs/en/server/user-guide/plugins.md).
