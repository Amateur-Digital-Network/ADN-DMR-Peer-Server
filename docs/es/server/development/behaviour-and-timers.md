# Comportamiento y temporizadores

## Bucles de control estables

El servidor usa tareas **Twisted** `LoopingCall` para trabajo periódico: reglas de bridge, poda de flujos, refresco de opciones OpenBridge, recarga de alias, recarga de config de voz, descargas de seguridad, informes y pings de mantenimiento.

Los intervalos forman parte del **comportamiento observable** del producto (operadores e integradores pueden apoyarse en la temporización para diagnóstico). Evita **refrescos extra** o trabajo duplicado en **rutas calientes** (p. ej. manejadores por paquete para OpenBridge) cuando la misma preocupación ya la cubre el bucle programado — mantiene la carga predecible y evita aplicar reglas dos veces.

## Visibilidad de la configuración

El estado en tiempo de ejecución vive en un **`config` dict** compartido: opciones de peers, `SUB_MAP`, campos de plano de control OpenBridge (`_bcsq`, `_bcka`), y similares. Los adaptadores actualizan esta estructura; los casos de uso la leen. Así coincide con cómo se inspecciona el proceso en ejecución en logs y escenarios de soporte.

## Intervalos clave de temporizadores (contrato operativo)

Los siguientes intervalos forman parte del comportamiento actual en ejecución:

| Bucle | Intervalo | Rol |
|------|-----------|-----|
| `rule_timer` | **52s** | Progresión de timeout y estado on/off de bridges. |
| `stream_trimmer` | **5s** | Limpieza de streams, manejo de timeout y cierre de estado de llamada. |
| `bridge_reset` | **6s** | Limpieza de flags de reset y cierre de resets pendientes. |
| OPTIONS refresh | **por evento** | TG estáticas / reflector vía **RPTO**, **startup/reload** (`apply_startup_bridges`), fallback **dmrd** sin source. Sin loop periódico de 26s (**D-28**). |
| `dynamic_tg_purge_loop` | **60s** | Purga filas **SINGLE=1** expiradas de `peer_dynamic_tgs` y `_PEER_UA_SESSIONS` en memoria. |
| `lst_seen` (reconcile self-service) | **120s** | Reconcilia `Clients.logged_in` contra los peers conectados actualmente: los conectados quedan `logged_in=1`, el resto `0`. Corre con `now=True` en el primer tick para limpiar flags obsoletos inmediatamente tras un reinicio del servidor. Evita que el monitor autentique hotspots desconectados vía login-by-IP. |
| `statTrimmer` | **303s** | Limpieza de bridges STAT obsoletos y estados transitorios. |

Si cambias uno de estos intervalos, documenta el impacto operativo en monitorización, comportamiento de bucles y troubleshooting.

## Constantes de contención de voz

Estas constantes definen el comportamiento por paquete y por sesión. Están
documentadas en detalle en [Enrutado de voz y contención](routing-and-contention.md).

| Constante | Valor | Rol |
|---|---|---|
| `STREAM_TO` | **0.36 s** | Ventana para considerar un stream "activo" (entre paquetes). |
| `_STALE_PEER_SESSION_TIMEOUT` | **5.0 s** | Una sesión per-peer sin frames se considera muerta (VTERM perdido). |
| `GROUP_HANGTIME` | **5 s** (default config, por sistema) | Bloqueo tras fin de QSO antes de aceptar otro TG en ese slot. |
| `DEFAULT_UA_TIMER` | configurable (minutos, por sistema) | Duración de bridges dinámicos (User Activated). |

## Alcance de VTERM in-band

La señalización in-band en voice terminator (VTERM) está acotada a:

- tipo de llamada **`group`**
- tipo de llamada **`vcsbk`**

No se aplica en rutas VTERM **unit/private**.

## Notas de comportamiento de packet-control

Comportamiento actual para deduplicación y orden de streams:

- La deduplicación por hash en OBP se evalúa con guardia **`seq > 0`**.
- En HBP se calcula/guarda CRC también para `seq == 0`, pero la caída por duplicado por CRC sigue guardada por **`seq > 0`**.
- Esto evita sobre-descartar casos de primer paquete y mantiene protección de duplicados de stream.

<a id="loop-guard"></a>
## Loop guard (protección contra bucles)

El control de bucles por stream ID (`*LoopControl*`) solo detecta un stream que vuelve con su propio stream ID. Un puente que transcodifica (YSF2DMR, DVSwitch, pasarelas ASL/EchoLink) vuelve a codificar el audio y abre un stream ID **nuevo**, y cada vuelta reinicia el límite de 180 s. La comprobación de TG ocupado deja pasar a propósito a la misma persona. Así que un bucle a través de puentes (TG A → YSF → TG B → … → TG A) daría vueltas sin fin.

Esos puentes conservan el ID de quien habla, y una persona no puede transmitir desde dos entradas a la vez. Al empezar un stream de voz de grupo, `application/routing/loop_guard.py` lo considera **eco** cuando el mismo `rf_src` tiene otro stream (con otro stream ID) en el **mismo TG** desde **otra entrada** (sistema y peer; un enlace OPENBRIDGE cuenta como una sola entrada) que sigue activo o terminó hace menos de `GLOBAL.LOOP_GUARD_HOLD` segundos (1 s por defecto; el eco de un puente empieza con la original aún en el aire, así que la ventana solo tiene que cubrir pasadas muy cortas). El validador mantiene la ventana por debajo de 2,0 s, el retardo fijo del loro (`_PLAYBACK_DELAY_S`), así que el loro nunca se detecta se llame como se llame su sistema o TG. Un puente que pasa a **otro** TG no es un bucle y no se toca; un bucle siempre vuelve al TG donde empezó.

- `LOOP_GUARD` por sistema: `log` (por defecto) escribe un aviso `*LoopGuard*` por stream eco y lo deja pasar; `true` lo descarta (en el enrutado y en la reemisión local del MASTER a sus otros peers); `false` ni comprueba ni registra los streams que entran por ahí (una opción extra para el loro **ECHO**).
- Exentos: los IDs de voz del servidor (`all_server_voice_ids`, p. ej. 1000001; varios servidores emiten balizas con ellos a la vez) y las tramas de plugins y anuncios.
- Coste: un veredicto por stream, en su primera trama, cacheado por stream ID. Las tramas de un stream ya asignado a su slot no llegan aquí. El trimmer de streams olvida las entradas a los 300 s.
- El multipath OBP (la misma llamada por dos caminos OpenBridge) conserva el stream ID y no es eco.

No cubre: un puente que transmite con **su propio** ID en vez del de quien habla.
