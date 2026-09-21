# ADN DMR Peer Server

**Versión 2.6** — emparejado con **adn-monitor 2.6** (report v2 slim wire + HELLO JSON).

Servidor puente de conferencia DMR de ADN. La configuración es YAML; el código sigue arquitectura limpia (domain, application, infrastructure). v2 agrega **PROXY** integrado, enrutamiento **SubscriptionStore**, report v2 hacia el monitor, y un punto de entrada unificado **`adn-server.py`** (`--echo`, `--doctor`, `--no-proxy`). Ver [CHANGELOG.md](CHANGELOG.md).

## Licencia

GPL v3. Derivado de FreeDMR / HBlink.

## Requisitos

- Python 3.10+
- Dependencias: `pip install -r requirements.txt`
- **ffmpeg** (paquete del sistema) — requerido para voz/TTS. Instálalo según tu distro:
  - Debian/Ubuntu: `apt install ffmpeg`
  - openSUSE: `zypper install ffmpeg`
  - Fedora/RHEL: `dnf install ffmpeg` o `yum install ffmpeg`

## Configuración

Copia `adn-server.example.yaml` a `adn-server.yaml` y edita con tus datos. La config de producción no se commitea.

El ejemplo incluye un **proxy de hotspots integrado** (`PROXY`) y **self-service MySQL** opcional (`SELF_SERVICE`). Para self-service, instala el extra opcional: `pip install -e ".[selfservice]"`. Ver [Proxy de hotspots (integrado)](docs/es/server/user-guide/hotspot-proxy.md). Desactiva el **`adn-proxy`** standalone si usas el proxy integrado en el mismo host.

### Configuración de voz

Las funciones de voz (anuncios, TTS, grabación) usan un archivo de configuración aparte. Copia `adn-voice.example.yaml` a `adn-voice.yaml` y edita. Si el archivo no existe, las funciones de voz quedan deshabilitadas (sin error). Los cambios se recargan en caliente cada 15 segundos.

- **Cada ítem** (ANNOUNCEMENTS, TTS_ANNOUNCEMENTS) tiene su propio `LANGUAGE` y `ENABLED: true` para activarse.
- **ANNOUNCEMENT_LANGUAGES** es opcional (solo para el ident de voz); los anuncios/TTS funcionan sin ella.
- **TTS** requiere ffmpeg + vocoder (TTS_VOCODER_CMD o TTS_AMBESERVER_HOST). Pipeline: `.txt` → gTTS → `.mp3` → ffmpeg → `.wav` → vocoder → `.ambe`. La primera vez: crea `Audio/<IDIOMA>/ondemand/<ARCHIVO>.txt` con el texto. Ver [Voz, anuncios y TTS](docs/es/server/user-guide/voice-and-tts.md).

## Documentación (MkDocs)

Documentación (MkDocs): inglés en **`docs/en/`**, español en **`docs/es/`** (`server/` y `monitor/` en cada una). **Instala primero el stack de docs** (incluye **Material**); de lo contrario puedes ver `Unrecognised theme name: 'material'` si el `mkdocs` de tu `PATH` no es el mismo entorno.

```bash
python3 -m pip install -r requirements-docs.txt
python3 -m mkdocs build -f mkdocs.yml          # → site/en/
python3 -m mkdocs build -f mkdocs.es.yml       # → site/es/
```

Usa el mismo `python3` que usas para el proyecto (p. ej. el `3.11.8` de pyenv). Previsualiza el árbol combinado: `cd site && python3 -m http.server` y abre **`/en/`** y **`/es/`**, o ejecuta `python3 -m mkdocs serve -f mkdocs.yml` solo para inglés.

Salida: **`site/en/`** y **`site/es/`** bajo **`site/`** (ignorado por git).

## Tests

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest tests/ -q
```

Ver [Testing](docs/en/server/development/testing.md) en el sitio de docs (aún sin traducir). Índice de archivos: [`tests/README.md`](tests/README.md).

## Ejecutar

```bash
pip install -r requirements.txt
python adn-server.py
```

Opciones:

```bash
python adn-server.py -c /path/to/adn-server.yaml
python adn-server.py --logging DEBUG
python adn-server.py --doctor          # config, puertos, peers (sale con 1 si hay errores)
python adn-server.py --no-proxy        # desactiva el PROXY integrado
```

## Echo (reproducción)

Proceso aparte (mismo binario) que graba voz de grupo y la reproduce en el TG 9990.

```bash
cp adn-echo.example.yaml adn-echo.yaml
python adn-server.py --echo -c adn-echo.yaml
```

Ver [Echo (reproducción)](docs/es/server/user-guide/echo.md) en el sitio de docs para una descripción general.

**systemd:** unidades de ejemplo en `examples/systemd/` (`adn-server.service`, `adn-echo.service`).

## Agradecimientos

Gracias a **[Esteban Mackay, HP3ICC](https://gitlab.com/hp3icc)**, por las ideas, ayudas, pruebas y sugerencias a lo largo del desarrollo.

---

[English](README.md)
