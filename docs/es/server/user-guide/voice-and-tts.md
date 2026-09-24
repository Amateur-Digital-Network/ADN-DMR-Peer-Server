# Voz, anuncios y TTS

> **Los anuncios programados y el TTS son un plugin** — `plugins/voice-announcements/`, activado por defecto. La configuración no cambia (este fichero); el plugin la sigue cada 15 s y el servidor le autoriza exactamente los TGs e IDs DMR que usan sus ítems (se puede ajustar con `PLUGINS.send.voice-announcements`, ver [Plugins](plugins.md#envío-de-datos-y-voz-de-grupo-opcional)). El ident de voz, los 999x a demanda y el aviso de desconexión siguen en el núcleo.

## Ficheros de configuración

- **`adn-voice.yaml`** (opcional, no versionada) — fusionada en `config["VOICE"]`.
- Recarga en caliente por **mtime** del fichero (~bucle de 15 s).

Plantilla: `adn-voice.example.yaml`.

## Identidad de voz del servidor (`DMR_ID` configurable)

Toda la voz **originada por el servidor** usa un **DMR ID** configurable en `adn-voice.yaml`:

```yaml
VOICE:
  DMR_ID: 1000001        # opcional; predeterminado 1000001

  TTS_ANNOUNCEMENTS:
    - ENABLED: true
      FILE: texto1
      TG: 730500
      DMR_ID: 3109898    # opcional; hereda VOICE.DMR_ID si se omite
```

- **`VOICE.DMR_ID`** — ID RF predeterminado. **Opcional** — sin cambios en `adn-voice.yaml` existente.
- **`DMR_ID` por ítem** — override en cada fila de `ANNOUNCEMENTS` / `TTS_ANNOUNCEMENTS`. También opcional.
- **Callsign** — lo resuelve la base de suscriptores (`users` / `SUB_MAP`) para ese DMR ID; no va en voice config.

Aplica a:

- **ANNOUNCEMENTS** y **TTS_ANNOUNCEMENTS** programados (destino = el campo **`TG`** de cada ítem).
- Clips **bajo demanda** disparados marcando **9991–9999** (la reproducción usa TG de destino **9** en **TS2**; sigues marcando **999x** para solicitar el fichero).
- Líneas de voz de **desconectado / reflector** (igual: **TG 9** / **TS2**).

Ver [TG 9 — carril de servicio local](special-numbers.md#tg-9-local-service-lane-prompts-and-bridge-plumbing) por qué se usa TG 9 y qué debe estar habilitado en el hotspot.

- **Ident por voz** (destino **all-call** o **`OVERRIDE_IDENT_TG`**).

Ver [Números especiales — ID de voz del servidor](special-numbers.md#server-voice-source-id-configurable) para la tabla completa.

## Funciones

| Función | Descripción |
|---------|-------------|
| **Anuncios programados** | Frases AMBE desde `Audio/<lang>/ondemand/` según horario / intervalo. |
| **Anuncios TTS** | Texto → gTTS → MP3 → ffmpeg → WAV → vocoder → AMBE (ver tubería abajo). |
| **Ident por voz** | Identificación periódica (opcional); usa `VOICE_IDENT` en la config principal del servidor y `OVERRIDE_IDENT_TG` / all-call. |
| **Grabación** | Si está habilitada, graba AMBE a disco para tráfico en **`RECORDING_TG`** / **`RECORDING_TIMESLOT`** (ver plantilla `adn-voice.example.yaml`). |
| **Bajo demanda (9991–9999)** | Ver [Números especiales](special-numbers.md). |

## Tubería TTS (resumen)

1. Texto fuente en `.txt` bajo `Audio/<idioma>/ondemand/`.
2. **gTTS** produce **MP3**.
3. **ffmpeg** convierte a **WAV**.
4. Comando vocoder o **AMBEServer** produce **AMBE** para transmitir.

**ffmpeg** debe estar instalado en el sistema operativo.

Configura **`TTS_VOCODER_CMD`** o **`TTS_AMBESERVER_HOST`** / **`TTS_AMBESERVER_PORT`** en `adn-voice.yaml` para codificación; ver comentarios en `adn-voice.example.yaml`.

## Anti-colisión (QSO) con anuncios

Al programar anuncios, el servidor puede **esperar** si los slots objetivo están ocupados, **descartar** objetivos si aparece un QSO en vivo a mitad, y solo marcar estado de anuncio **horario** tras una lista de objetivos con éxito — evita pisar tráfico en vivo.

El plugin envía cada trama con `send_dmrd`, y el núcleo la inyecta como **PTT sintético de hotspot** en `PROXY.TARGET_SYSTEM` (el mismo MASTER que usa el proxy integrado). El enrutamiento reparte a hotspots puenteados y piernas OPENBRIDGE; los peers locales en ese MASTER siguen recibiendo tramas vía `send_system`. Mientras suena, la emisión ocupa ese slot del MASTER; una radio que lo toma la corta.

## Cola de emisión

Las **emisiones** en paralelo en **TG distintas** pueden ejecutarse a la vez; las emisiones en el **mismo TG** se **serializan** para que un anuncio termine antes que otro en ese TG.

## Ver también

- [Configuración](configuration.md) — rutas de ficheros de voz.
- [Echo](echo.md) — servicio de reproducción separado.
- [Números especiales](special-numbers.md) — 5000, 999x, comportamiento relacionado con grabación.
