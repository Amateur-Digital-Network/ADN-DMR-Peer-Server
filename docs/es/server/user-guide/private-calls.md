# Llamadas privadas

## Descripción general

Las llamadas **unitarias (privadas)** usan un camino distinto a la voz de **grupo**. El router usa **`SUB_MAP`** (suscriptor → último sistema/slot/tiempo conocido) y reglas de colisión para decidir si y dónde reenviar.

## SUB_MAP

- Se rellena cuando las estaciones registran tráfico; persiste vía ruta pickle **`SUB_MAP`** configurada bajo **`ALIASES`**.
- Sirve para resolver **ID de radio de destino** a un **sistema destino** y **slot** para el reenvío privado.
- Los datos unitarios (SMS, ACK) cuyo destino se oyó por última vez en el mismo hotspot del que llegan no se devuelven a ese hotspot: las dos radios se oyen por RF y el eco transmitiría encima de su propio intercambio.
- Los datos unitarios a un ID individual oído por última vez en un sistema que no es OPENBRIDGE hace menos de 900 s no se reparten a los OpenBridge; la búsqueda en `SUB_MAP` los entrega localmente. Los suscriptores aprendidos por un OpenBridge, o las entradas más antiguas, mantienen el reparto.

<a id="unit-data-shared-master"></a>
### Datos unitarios en un MASTER compartido

Los datos unitarios (preámbulos CSBK, cabeceras de datos y bloques rate 1/2, 3/4 y 1: SMS, GPS/LRRP, ARS) solo van al hotspot donde se oyó por última vez su destino:

- A un ID que nunca se oyó en el MASTER, o cuyo hotspot ya no está conectado, se descartan en un MASTER compartido por varios hotspots (`MAX_PEERS` mayor que 1, como el destino del proxy integrado) en vez de enviarse a todos. Un MASTER de un solo peer mantiene el broadcast legacy.
- No se envían a un hotspot mientras ese hotspot lleva voz en el slot (en cualquier slot si es simplex). MMDVMHost acepta un CSBK o una cabecera de datos en medio de una llamada de red, pasa el slot a datos y descarta la voz hasta la siguiente ráfaga de sincronismo, así que quienes escuchaban oían un chicharreo cada vez que una radio enviaba su posición. Una radio tampoco transmite datos con el slot ocupado; lo que se descarta queda a cargo de los reintentos de la propia radio.

La voz privada no cambia.

<a id="sub_map-persistence"></a>
### Persistencia de SUB_MAP

`SUB_MAP` se guarda en `ALIASES.SUB_MAP_FILE` (`sub_map.pkl` si está vacío). Se revisa cada **300 s** (las entradas de más de 24 h se podan) y solo se escribe cuando cambió una ruta o la marca de tiempo de una entrada pasó a una hora nueva. El fichero se escribe en un `.tmp` y se renombra, así que una caída nunca lo deja truncado. `SIGHUP` y el apagado siempre lo escriben.

<a id="service-systems-sub_map_learn"></a>
### Sistemas de servicio (`SUB_MAP_LEARN`)

Cada trama de un sistema MASTER o PEER mueve su ID de origen a ese sistema en `SUB_MAP`. Un sistema de servicio que transmite con el ID de otra persona ocuparía el lugar de ese suscriptor hasta que vuelva a transmitir, y las llamadas privadas y datos unitarios dirigidos a él se entregarían al servicio:

- el loro **ECHO** reproduce cada llamada con el ID de quien llama, así que tras una prueba al 9990 el tráfico privado de esa persona apunta a `ECHO`;
- una baliza o un peer de anuncios que transmite con el ID de una persona o con un ID de servicio compartido;
- un puente ASL / EchoLink / DVSwitch que reenvía los IDs reales de quienes llaman desde otro modo.

Pon `SUB_MAP_LEARN: false` en esos sistemas:

```yaml
SYSTEMS:
  ECHO:
    MODE: MASTER
    SUB_MAP_LEARN: false   # el loro nunca pasa a ser donde está quien llama
```

Solo se omite el aprendizaje: el enrutado de grupo, las ACL y la entrega a suscriptores aprendidos en otros sistemas funcionan igual. El valor por defecto es `true` en todos los sistemas, también en uno llamado `ECHO`. Cuando una recarga o un reinicio encuentra un sistema con `false`, se borran las entradas de `SUB_MAP` que ya apuntaban a él. Los sistemas OpenBridge siempre aprenden (quien esté detrás del enlace tiene que seguir siendo localizable), así que la clave se rechaza en `MODE: OPENBRIDGE`. Las tramas que los plugins envían con `send_dmrd` tampoco actualizan `SUB_MAP`.

## OpenBridge frente a MASTER

El manejo privado usa ramas CSBK/datos/unit, búsqueda `SUB_MAP` y comprobaciones de slot ocupado donde aplique (ver `RoutingUseCases` en el código).

## TG / ID 4000 (unitaria)

Como en [Números especiales](special-numbers.md), una llamada **privada** a **4000** desactiva dinámicos y **no** se trata como ruta privada normal.

## Informes

Los eventos privados **START/END** pueden emitirse al cliente TCP de informes si **`REPORTS.REPORT`** está habilitado, análogo a voz de grupo (forma `PRIVATE VOICE,...` donde esté implementado).

Para detalles de ingreso de protocolo, ver [HBP](../protocols/hbp.md) y los casos de uso de routing en código (`RoutingUseCases._pvt_call_received`).
