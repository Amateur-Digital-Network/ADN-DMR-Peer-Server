# Documentación ADN Systems

Este sitio describe el **ADN DMR Peer Server** y el **ADN Monitor** como una pila operativa unida. El contenido se organiza por **producto** (`server/` frente a `monitor/`) en este **locale** español.

- **English:** mismo contenido bajo `docs/en/` — `mkdocs build -f mkdocs.yml` → `site/en/` (ver `docs/en/README.md` en el repositorio).

## ADN DMR Peer Server

El **ADN DMR Peer Server** es un puente de conferencia [GPL-3.0](https://www.gnu.org/licenses/gpl-3.0.html) para radio móvil digital (DMR). Está estructurado en capas de **arquitectura limpia** (dominio, aplicación, infraestructura).

### Qué hace el servidor

- Termina enlaces **HBP** (HomeBrew Protocol) hacia sistemas **MASTER** y **PEER** (hotspots, repetidores).
- Termina enlaces **OpenBridge** hacia otros servidores por UDP — **DMRE v5** (recomendado en ADN) o **DMRD** v1 en modo de compatibilidad.
- Ejecuta **enrutado de bridges** (`BRIDGES`): voz de grupo, control de bucle, ACL, **BCSQ** / **BCKA** opcionales.
- Soporta **llamadas privadas** (`SUB_MAP`), **voz**, **TTS**, **grabación** e **informes TCP** al monitor.

### Por dónde empezar (servidor)

| Quiero… | Empieza aquí |
|---------|----------------|
| Ejecutar y configurar | [Introducción](server/user-guide/introduction.md), [Configuración](server/user-guide/configuration.md) |
| Escribir un plugin drop-in | [Plugins](server/user-guide/plugins.md), skeleton `plugins/example/` |
| TG 4000, 999x, eco | [Números especiales](server/user-guide/special-numbers.md) |
| Llamadas privadas | [Llamadas privadas](server/user-guide/private-calls.md) |
| Voz / TTS | [Voz, anuncios y TTS](server/user-guide/voice-and-tts.md) |
| Panel legacy + servidor 2.x | [Proxy de informes](server/user-guide/report-proxy.md) |
| OpenBridge / DMRE | [OpenBridge](server/protocols/openbridge.md), [DMRE v5](server/protocols/dmre-v5.md), [Proxy OBP](server/user-guide/obp-proxy.md) |
| HBP | [HBP](server/protocols/hbp.md) |
| Código | [Arquitectura](server/development/architecture.md), [Comportamiento y temporizadores](server/development/behaviour-and-timers.md) |
| Créditos, licencia, linaje | [Créditos y licencia](server/user-guide/attribution.md) |

### Inicio rápido (servidor)

```bash
pip install -r requirements.txt
cp adn-server.example.yaml adn-server.yaml
# Edita DATABASE (MariaDB) y secretos antes de producción
python adn-server.py -c adn-server.yaml
```

Más: [Introducción](server/user-guide/introduction.md).

---

## ADN Monitor

Panel, **WebSocket** en vivo, API FastAPI, **MySQL** self-service — ver [Descripción general del monitor](monitor/index.md). El **proxy hotspot** está integrado en **adn-server**.

| Quiero… | Empieza aquí |
|---------|----------------|
| `adn-server.yaml` — `PROXY` / `SELF_SERVICE` integrados | [Proxy hotspot (integrado)](server/user-guide/hotspot-proxy.md) |
| `adn-server.yaml` — fan-in OpenBridge `OBP_PROXY` | [Proxy OBP](server/user-guide/obp-proxy.md) |
| `adn-monitor.yaml`, despliegue | [Configuración del monitor](monitor/configuration.md) |
| Proxy hotspot integrado | [Proxy hotspot](server/user-guide/hotspot-proxy.md) |
| Proxy hotspot (UDP, `PROXY`, rango de puertos) | [Proxy hotspot](monitor/hotspot-proxy.md) |
| Self-service | [Self-service](monitor/self-service.md) |
| Cómo encaja con el servidor | [Monitor e informes](server/user-guide/monitoring.md) |

---

## Locales

- **Español** — **`docs/es/`** (este árbol).
- **English** — **`docs/en/`**.

Ver [Traducciones](server/contributing/translations.md).
