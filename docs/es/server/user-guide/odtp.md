# Pasarela ODTP (apps de móvil y terminales IP)

## Qué es

**ODTP** (Open DMR Terminal Protocol, el protocolo *Rewind* de BrandMeister)
permite que apps de móvil y terminales IP (Z3DMR, radios PoC…) usen un
master ADN con el DMR ID y la contraseña de su usuario, sin hotspot.

La pasarela es un proceso aparte del mismo binario,
**`adn-server.py --odtp`**. Cada terminal que inicia sesión es **un peer HBP**
del MASTER, desde su propio socket UDP, igual que un hotspot:

| Terminal | Peer HBP |
|----------|----------|
| Entra con un DMR ID de 7 cifras | ID de peer = DMR ID × 100 + `PEER_SUFFIX` (`98`; una segunda sesión del mismo ID usa `97`) |
| Entra con un número de 9 cifras | Ese número tal cual; habla como DMR ID = número ÷ 100 (el ID de un hotspot con su sufijo de 2 cifras) |
| Contraseña | Se reenvía: el salt del MASTER es el reto del terminal y su respuesta es el RPTK. La pasarela no ve ni guarda contraseñas; decide el MASTER (autoservicio o passphrase) |
| TG a los que se suscribe | `OPTIONS` `TERMINAL=1;TS2=<tgs>;` — los cambios en 0,1 s van en un solo RPTO |
| Voz | DMRD en TS2; el origen es siempre el DMR ID de la sesión, nunca el que pone el terminal en la cabecera. Una llamada sin terminador acaba 1 s después de su última trama, en los dos sentidos |
| Mensajes de texto, posición | Datos en paquetes DMR (ETSI estándar / MOTOROLA TMS / Hytera, NMEA a `LOCATION_DST`) |

Una contraseña incorrecta no recibe respuesta, como en BrandMeister, y nunca
tira una sesión ya conectada con ese ID.

## TERMINAL=1

`TERMINAL=1` en las `OPTIONS` de un peer indica un terminal: no tiene lista de
grupos de RX, así que el MASTER **no** le añade sus `TS1_STATIC` /
`TS2_STATIC` — el terminal oye solo aquello a lo que se suscribió. Todo lo
demás (enrutado, ACL, autoservicio, monitor) lo trata como cualquier peer; el
monitor los lista en *Terminales ODTP*.

## Configuración

Copia **`adn-odtp.example.yaml`** → **`adn-odtp.yaml`** (no se versiona):

| Clave (`ODTP:`) | Por defecto | Para qué |
|-----------------|-------------|----------|
| `LISTEN_IP` / `LISTEN_PORT` | `0.0.0.0` / `54006` | Puerto UDP al que se conectan las apps |
| `MASTER_IP` / `MASTER_PORT` | `127.0.0.1` / `62031` | MASTER donde entran los terminales (el puerto del proxy de hotspots si se quieren contraseñas de autoservicio) |
| `PEER_SUFFIX` | `98` | 0–99, sufijo del ID de peer |
| `SUBSCRIBERS_FILE` | — | `subscriber_ids.json` de RadioID para el indicativo del RPTC (se busca, no se carga) |
| `MAX_SESSIONS` | `500` | Sesiones en total. El `MAX_PEERS` del MASTER también las limita, compartido con sus hotspots: si está lleno, la app no recibe respuesta y cada reintento gasta un login |
| `MAX_SESSIONS_PER_IP` | `4` | Sesiones desde una IP |
| `MAX_LOGINS_PER_MINUTE` | `60` | Sesiones nuevas (= logins al MASTER) por minuto |
| `SMS_FORMAT` | `standard` | Texto a una radio que no ha escrito antes: `standard`, `tms` o `hytera` (la respuesta va en el formato que usó la radio) |
| `SMS_PREAMBLES` | `2` | Preámbulos CSBK antes de un texto |
| `LOCATION_DST` | `0` | ID privado que recibe la posición GPS de la app como datos NMEA (p. ej. un plugin D-APRS); `0` = no se envía |

Un terminal nuevo por encima de cualquier tope se ignora; los rechazos se
registran una vez por minuto con sus contadores. Un datagrama que no es un
keep-alive de Open DMR Terminal no abre sesión ni gasta login, ni tampoco un ID que no es un DMR ID de 24 bits. La sección se valida al
arrancar. Los cambios requieren reiniciar (`SIGHUP` se ignora; `SIGUSR2`
reabre los logs).

## Arranque

```bash
python adn-server.py --odtp -c adn-odtp.yaml
```

Producción: `examples/systemd/adn-odtp.service`. Abre `LISTEN_PORT` (UDP) en el
cortafuegos. Al parar, cada terminal recibe un CLOSE y cada peer un RPTCL.
