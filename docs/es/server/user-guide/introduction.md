# Introducción

## Propósito

Este servicio es un **peer y bridge DMR**. Implementa:

- **HBP** por UDP hacia sistemas **MASTER** y **PEER** (tramas DMRD, autenticación, pings).
- **OpenBridge** por UDP hacia otras redes — **DMRE v5** (versión embebida 5, BLAKE2b, saltos) es el modo **recomendado** entre servidores en ADN; **DMRD** v1 sigue disponible para compatibilidad (ver [OpenBridge](../protocols/openbridge.md#dmre-and-openbridge-v5)).
- **Trunking DMR Tier III** en un MASTER con `TRUNKING: true`: los emplazamientos dmrtc detrás de DMRGateway `TrunkingEnabled=1` (protocolo Homebrew DMR trunking v0.6) se suscriben a sus talkgroups, registran sus radios y llevan muchas llamadas a la vez (ver [Trunking DMR Tier III](tier3-trunking.md)).

La configuración es **YAML** (`adn-server.yaml`), fusionada en tiempo de ejecución con ajustes de voz opcionales (`adn-voice.yaml`). La plantilla incluida es `adn-server.example.yaml`.

## Diseño

Enrutado, temporizadores, control de bucle OpenBridge y manejo de protocolo están en módulos de **aplicación** e **infraestructura** detrás de **ports** estables; la capa de **dominio** contiene tipos y reglas sin E/S. Así el sistema es más fácil de seguir y extender.

## Subsistemas principales

| Subsistema | Rol |
|------------|-----|
| **Bridge router** | Tabla `BRIDGES`: qué sistemas reenvían qué TG en qué slot; bridges dinámicos; bridges estáticos/stat; **restauración MariaDB de TG dinámicos** al reconectar. |
| **Protocolo HBP** | Autenticación, ingreso/salida DMRD, repetición a peers, filtros TG, seguimiento de **sesión UA por peer**. |
| **OpenBridge** | Ingreso DMRE, límite de saltos, control de bucle (`min(1ST)`), BCSQ/BCKA si están habilitados. |
| **Voz** | Ficheros AMBE, anuncios programados, tubería TTS, reproducción bajo demanda (TG 9991–9999). |
| **Informes** | Canal TCP netstring hacia **adn-monitor** (y paneles compatibles): config, estado de bridges, eventos de llamada (informe v2 JSON). |
| **Proxy hotspot** | Fan-in UDP integrado opcional (`PROXY` en `adn-server.yaml`) y **self-service** MySQL (`SELF_SERVICE`) para opciones de hotspot desde el panel. |
| **Proxy OBP** | Fan-in UDP OpenBridge integrado opcional (`OBP_PROXY` en `adn-server.yaml`; por defecto si hay sistemas OPENBRIDGE). |

## Programas relacionados

- **Echo / playback** — `adn-server.py --echo` con `adn-echo.yaml` mínimo; ver [Echo](echo.md).
- **Proxy hotspot integrado** — `PROXY` en **`adn-server.yaml`**; ver [Proxy hotspot](hotspot-proxy.md).
- **Proxy OBP integrado** — fan-in `OBP_PROXY` para OpenBridge; ver [Proxy OBP](obp-proxy.md).
- **Proxy de informes (paneles legacy)** — **[ADN-report-proxy](https://github.com/ce5rpy/ADN-report-proxy)** opcional para que **adn-server 2.x** alimente monitores antiguos estilo HBMonitor / FDMR (wire v1); ver [Proxy de informes](report-proxy.md). No se usa con **adn-monitor 2.x**.

## Siguientes pasos

- [Configuración](configuration.md) — ficheros, `GLOBAL`, **MASTER** / **PEER** / **OPENBRIDGE**, ACL, **`DATABASE`**, informes, **`PROXY`**, **`SELF_SERVICE`**, alias, fusión de voz.
- [Bridges y talkgroups](bridges-and-talkgroups.md) — cómo funciona `BRIDGES`.
- [Enrutado de voz y contención](../development/routing-and-contention.md) — el flujo completo de paquetes, reglas de contención, SINGLE, mapeo de slot y divergencias.
- [Números especiales](special-numbers.md) — TG 4000, servicios de información, eco.
- [Proxy hotspot](hotspot-proxy.md) — **`PROXY`** / **`SELF_SERVICE`** integrados en `adn-server.yaml`.
- [Proxy OBP](obp-proxy.md) — fan-in OpenBridge **`OBP_PROXY`** integrado.
- [ADN Monitor](../../monitor/index.md) — panel, `adn-monitor.yaml`, UI self-service (repo aparte, desplegado con el servidor).
- [Rendimiento (2.x)](../development/performance.md) — mejoras de CPU/RAM en esta versión y qué las provoca.
- [Créditos y licencia](attribution.md) — ADN → FreeDMR → hblink3, licencia.
