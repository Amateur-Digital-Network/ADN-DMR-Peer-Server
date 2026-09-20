# Proxy OBP (puerto de entrada único)

La stanza opcional `OBP_PROXY` configura el listener fan-in para todos los sistemas `MODE: OPENBRIDGE`. Con el proxy activo, las instancias OpenBridge son **inject-only** (sin `listenUDP` por bridge en `HBPProtocol`); el proxy gestiona todo el UDP OBP entrante.

## Activación

| YAML | Comportamiento |
|------|----------------|
| Sin `OBP_PROXY`, sin OPENBRIDGE | N/A (no se arranca proxy). |
| Sin `OBP_PROXY`, con OPENBRIDGE | **Proxy por defecto** (`LISTEN_PORT` 62032, `BIND_LEGACY_PORTS` true). |
| `OBP_PROXY.ENABLED: false` | Modo legacy: cada OPENBRIDGE hace bind en su `PORT`. |
| `OBP_PROXY.ENABLED: true` | El proxy gestiona toda la entrada OBP (igual que bloque ausente). |

## Configuración

```yaml
OBP_PROXY:
  ENABLED: true
  LISTEN_PORT: 62032      # puerto estándar OBP fan-in (pareja de PROXY 62031)
  LISTEN_IP: ""           # dirección de bind opcional
  BIND_LEGACY_PORTS: true # default: también escucha cada SYSTEMS.*.PORT
  DEBUG: false
```

Las secciones OPENBRIDGE no cambian (`PORT`, `NETWORK_ID`, `PASSPHRASE`, `TARGET_*`, ACL, etc.). Con proxy activo, `PORT` se conserva como metadato (`_REPORT_PORT` internamente) para monitor/report y listeners legacy opcionales.

## Migración por bridge (`BIND_LEGACY_PORTS: true`)

Con el flag global activo, cada OPENBRIDGE migra de forma individual:

| `SYSTEMS.*.PORT` | Comportamiento |
|------------------|----------------|
| Igual a `OBP_PROXY.LISTEN_PORT` (p. ej. 62032) | Solo fan-in para ese bridge (sin listener legacy extra). |
| Omitido, `0` o vacío | Igual que `LISTEN_PORT` — solo fan-in (bridge migrado). |
| Otro puerto (p. ej. 62999) | Se mantiene el listener legacy de ese bridge. |

Ejemplo: migrar `OBP-CL2` al fan-in compartido mientras `OBP-EU` conserva `PORT: 62999`.

## Migración

1. Configs existentes con OPENBRIDGE sin stanza `OBP_PROXY` ya usan defaults (`BIND_LEGACY_PORTS: true`) — sin cambios en remotos.
2. Opcionalmente añadir bloque `OBP_PROXY` explícito para ajustar `LISTEN_PORT` / `BIND_LEGACY_PORTS`.
3. `BIND_LEGACY_PORTS: false` y cerrar puertos legacy cuando todos usen `LISTEN_PORT`.

- `NETWORK_ID` único entre OPENBRIDGE habilitados.
- `LISTEN_PORT` sin colisión con ningún `PORT` de sección si `BIND_LEGACY_PORTS` es true.
- `RELAX_CHECKS: true` recomendado para aprender la dirección del peer del primer paquete válido. Lo aprendido vive en la sesión del bridge, no en la config: `TARGET_IP` / `TARGET_PORT` se quedan como están escritos, y una recarga devuelve el enlace a ellos.

## ¿Por qué no cruzó esa llamada?

Pasa una captura por el mismo ingress que corre el servidor, sin conexión y
contra tu propio `adn-server.yaml`. No se envía nada ni se abre ningún puerto,
así que el servidor puede seguir funcionando:

```bash
tcpdump -i any -n -s 0 -w /tmp/obp.pcap 'udp and portrange 62000-63000'   # con un minuto sobra
adn-server -c adn-server.yaml --replay /tmp/obp.pcap
```

Captura un rango de puertos, no una lista: un peer que contesta desde donde no
se espera es justo el caso que interesa mirar, y un filtro estrecho lo esconde.

Cada trama vuelve con el bridge al que pertenece y un veredicto:

```
12:04:31  82.65.127.86:62201       OBP-FR       DMRD v1   2130001 -> 214          delivered
12:04:31  85.241.222.7:62268       OBP-PT       DMRE v5   2680015 -> 9            dropped (tg-filter-server) +BCSQ
12:04:32  203.0.113.9:50000        -            DMRD v1                           unmatched

3 datagram(s)
  OBP-FR
       1  delivered
  OBP-PT
       1  dropped: tg-filter-server
  (no bridge)
       1  unmatched
```

`unmatched` significa que ningún bridge habilitado pudo verificar la trama con
su passphrase, y `outbound` es lo que ha enviado este servidor — una captura sin
filtrar lleva las dos direcciones y solo se juzga lo que llegó
(`--replay-both-directions` juzga también el resto). `--system OBP-FR` mira un
solo enlace, `--replay-limit N` corta antes y `--replay-summary` deja solo el
recuento. Solo pcap clásico; un pcapng se convierte con
`editcap -F pcap in.pcapng out.pcap`.

Dos cosas que el informe no es. Termina donde empieza el enrutado: en una malla
la misma llamada llega legítimamente por varios bridges a la vez, y es el
control de bucles —después, en routing— quien se queda con una y descarta el
resto, así que una llamada que el servidor registró una vez puede aparecer aquí
entregada en más de un enlace. Y las tablas de alias y la lista de server-ids se
cargan en ejecución, no del YAML, así que sin conexión esas dos comprobaciones
se saltan en vez de adivinarse.

Ver también: [protocolo OpenBridge](../protocols/openbridge.md).
