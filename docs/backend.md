# Qué le manda el tótem al backend

Contrato completo de lo que sale del tótem y lo que entra. Todos los ejemplos de
este documento están generados a partir de capturas reales del equipo
(`tests/capturas/`), no escritos a mano: son bytes que el Berry mandó de verdad,
pasados por el parser de producción.

- [0. Qué cambia para el backend](#0-qué-cambia-para-el-backend) ← **empezar acá**
- [1. Panorama](#1-panorama)
- [2. Cómo se arman las URLs](#2-cómo-se-arman-las-urls)
- [3. Autenticación](#3-autenticación)
- [4. `POST` de métricas](#4-post-de-métricas)
- [5. `POST` de health](#5-post-de-health)
- [6. Lo que el tótem escucha: eventos de Pusher](#6-lo-que-el-tótem-escucha-eventos-de-pusher)
- [7. El ciclo de una sesión](#7-el-ciclo-de-una-sesión)

---

## 0. Qué cambia para el backend

Lo que hay que revisar antes de desplegar esta versión. El resto del documento
es la referencia completa.

### 0.1 `data.ecg` cambió de tipo — rompe lo que haya hoy

| | Antes | Ahora |
|---|---|---|
| Tipo | `array` de números | **`objeto`** con 7 arrays |
| Contenido | Una sola derivación (la I) | Las 7: `I`, `II`, `III`, `aVR`, `aVL`, `aVF`, `V` |

```jsonc
// antes
"ecg": [128, 130, 129, …]

// ahora
"ecg": { "I": [128, …], "II": [136, …], "III": [6, …],
         "aVR": [60, …], "aVL": [249, …], "aVF": [70, …], "V": [111, …] }
```

Un consumidor que espere un array va a romper. Es el único cambio incompatible.

### 0.2 Campos nuevos en `data`

| Campo | Qué es |
|---|---|
| `ecgPeaks` | Latidos detectados por el equipo, indexados dentro de la onda de este mismo POST. Sirve para marcarlos sobre el gráfico. |
| `ecgInfo` | Estado y configuración del ECG: `leadOff`, `weakSignal`, `gain`, `mode`, el desnivel del `st` y el código de arritmia `hrv`. |

Con `ecgInfo.leadOff` se puede mostrar "electrodo suelto" en vez de graficar una
señal inservible.

### 0.3 El payload es ~20 veces más grande

Medido sobre un segundo real de `tests/capturas/normal.bin`, serializado
compacto:

| | Antes | Ahora |
|---|---|---|
| Por POST | **394 B** | **7.936 B** (7,8 KB) |
| Valores de ECG | ≤ 25, una derivación | ~1.750 (250 × 7 derivaciones) |
| Por tótem por hora | 1,35 MB | **27,2 MB** |

Son dos cambios sumados: pasar de 1 a 7 derivaciones, y subir el tope de 25 a
250 muestras por segundo — antes se recortaba la onda al décimo de lo que el
equipo manda.

**Esto es lo que más conviene mirar**: almacenamiento, límites de tamaño de
request y timeouts, multiplicado por la cantidad de tótems.

### 0.4 `spo2Pulse` ya no descarta el pulso bueno

Antes, un SpO₂ fuera de rango blanqueaba también el pulso. Ahora cada mitad se
valida por separado, así que aparecen valores como `"-/83"` (sin saturación,
con pulso) que antes llegaban como `"- - /- -"`.

### 0.5 Hay un endpoint nuevo que implementar: `/health`

`POST {API_URL}/{TOTEM_ID}/health`, cada 60 s, haya o no sesión. Reporta si el
tótem está vivo, si el Berry está enganchado, si llegan las órdenes de Pusher y
si las sondas están conectadas. Contrato completo en [`health.md`](health.md),
ejemplos en §5.

**Mientras no exista**, cada reporte da 404 y ensucia el log del tótem. Se puede
apagar desde la configuración con `HEALTH_INTERVAL_SECONDS=0` hasta que esté.

### 0.6 Ya no se pierden muestras de ECG

Hasta esta versión, el POST se llevaba sólo lo acumulado desde el último cambio
de segundo del reloj y el resto se descartaba: llegaba el **47 %** de las
muestras, con el número por POST variando entre 1 y 250. Ahora llega el 100 %.

Ya está contado en §0.3 — se menciona acá porque explica por qué el volumen
sube más de lo que sugiere el cambio de 1 a 7 derivaciones.

---

## 1. Panorama

```
   Berry PM6750                    TÓTEM                        BACKEND
   (USB o BLE)                  (berry-monitor.exe)

   tramas 55AA  ────────────►  parser ──┬──►  POST  {base}/{id}/metrics
   250 Hz por derivación                 │      (1×/seg, sólo con sesión activa)
                                         │
                                         └──►  POST  {base}/{id}/health
                                                (1×/60s, siempre)

   Termómetro USB IR ─────────►  pisa la temperatura del payload

                                   ▲
                                   │  eventos
                                Pusher  ◄──── el backend dispara start/stop
```

Dos flujos de salida independientes y uno de entrada:

| | Cuándo | Frecuencia |
|---|---|---|
| `POST` métricas | **Sólo con sesión activa** (entre `start` y `stop`) | 1 por segundo |
| `POST` health | **Siempre**, desde antes de que el Berry conecte | Cada `HEALTH_INTERVAL_SECONDS` (60 por defecto) |
| Pusher | El tótem está suscripto todo el tiempo | Según el backend |

Que el health sea una tarea aparte es deliberado: el estado del tótem importa
**sobre todo cuando no hay sesión** — un tótem con el Berry desenchufado de
madrugada tiene que poder avisarlo.

---

## 2. Cómo se arman las URLs

Las dos salen de la misma base y el mismo id:

```
{API_URL}/{TOTEM_ID}/{API_ENDPOINT}      → métricas
{API_URL}/{TOTEM_ID}/{HEALTH_ENDPOINT}   → health
```

`API_URL` es **sólo la base del servicio**: el id del tótem no va ahí, va en
`TOTEM_ID`, y el último tramo en `API_ENDPOINT` / `HEALTH_ENDPOINT`. Así una
misma base sirve a todos los tótems.

Con `API_URL=https://api.ejemplo.com/vitals` y `TOTEM_ID=totem12`:

```
https://api.ejemplo.com/vitals/totem12/metrics
https://api.ejemplo.com/vitals/totem12/health
```

> El health cuelga de la misma base **por diseño**: es el mismo tótem informando
> otra cosa. Si el backend lo necesitara en otro host, hoy no se puede desde la
> configuración — habría que tocar `_build_api_url()` en `app.py`.

---

## 3. Autenticación

HTTP Basic, en las dos rutas:

```http
Authorization: Basic base64(API_USERNAME:API_PASSWORD)
Content-Type: application/json
```

El token se arma una vez al arrancar y no rota. TLS se valida contra el almacén
de certificados del sistema (truststore, ver `src/ssl_context.py`); en Windows,
`SSL_CERT_FILE_PATH` no interviene.

---

## 4. `POST` de métricas

`POST {API_URL}/{TOTEM_ID}/{API_ENDPOINT}` — una vez por segundo, mientras haya
sesión activa.

### 4.1 Ejemplo real

Generado desde `tests/capturas/normal.bin`: un segundo de datos con paciente
puesto. Las ondas van recortadas acá; en el POST real viajan enteras.

```json
{
  "timestamp": 1757001600000,
  "data": {
    "vitalSigns": {
      "heartRate": "86",
      "respRate": "28",
      "spo2Pulse": "95/84",
      "temperature": "22.2",
      "nibp": "- - /- -"
    },
    "ecg": {
      "I":   [249, 175, 232, 249, 249, 241, "… 299 en total"],
      "II":  [136, 105, 123, 159, 152, 123, "… 299 en total"],
      "III": [6, 56, 20, 1, 1, 8,           "… 299 en total"],
      "aVR": [60, 116, 78, 11, 18, 74,      "… 299 en total"],
      "aVL": [249, 187, 234, 249, 249, 244, "… 299 en total"],
      "aVF": [70, 80, 73, 59, 53, 64,       "… 299 en total"],
      "V":   [111, 60, 83, 144, 150, 109,   "… 299 en total"]
    },
    "ecgPeaks": [{ "sample": 131, "ms": 524.0 }],
    "ecgInfo": {
      "leadOff": false,
      "weakSignal": false,
      "gain": "x1",
      "mode": "diagnostico",
      "st": 0.0,
      "hrv": "normal"
    },
    "spo2": [49, 52, 58, 64, 69, 74, "… 59 en total"],
    "resp": [1, 1, 1, 1, 1, 1,       "… 60 en total"]
  }
}
```

> Los valores de ECG pegados a 249 no son un error de transcripción: esa captura
> se grabó con `ECG_GAIN=x1` y el amplificador satura. Ver [`ecg.md`](ecg.md)
> §10.

### 4.2 Los campos

| Campo | Tipo | Qué es |
|---|---|---|
| `timestamp` | int | Milisegundos desde epoch, del tótem (`time.time() * 1000`). |
| `data.vitalSigns` | objeto de **strings** | Los números que se muestran. Ver §4.3. |
| `data.ecg` | 7 arrays | Las 7 derivaciones. **Un valor por derivación por instante**, no 7 muestras seguidas. Ver `ecg.md`. |
| `data.ecgPeaks` | array | Latidos detectados por el equipo, indexados **dentro de la ventana de este POST**. |
| `data.ecgInfo` | objeto | Estado y configuración del ECG, incluido el desnivel del ST. |
| `data.spo2` | array | Onda pletismográfica. |
| `data.resp` | array | Onda de respiración. |

### 4.3 Cuántos valores trae cada onda

| Onda | Frecuencia del equipo | Por POST (1 seg) |
|---|---|---|
| `ecg` — **cada** derivación | **250 Hz** | **~250 valores** |
| `ecg` — las 7 juntas | 1.750 valores/seg | ~1.750 valores |
| `spo2` | 50 Hz | ~50 valores |
| `resp` | 50 Hz | ~50 valores |

Las tres frecuencias están **medidas sobre las capturas**, no tomadas del
manual: la relación entre tipos de paquete da 5,00 paquetes de ECG por cada uno
de SpO₂ y de respiración, idéntica en las cuatro capturas. Con SpO₂ y
respiración a 50 Hz, el ECG va a 250 Hz.

**Cada paquete del equipo trae un valor de cada derivación** — los 7 bytes son
las 7 derivaciones del mismo instante, no 7 muestras seguidas de una. Ver
[`ecg.md`](ecg.md).

**No es un número fijo.** El POST se lleva todo lo que llegó desde el POST
anterior, y ese intervalo es `sleep(1)` más lo que tarde la request, así que
ronda los 250 pero varía unas pocas muestras por POST. El primer POST de una
sesión suele traer menos, porque el intervalo desde el arranque es más corto.

Lo que **no** pasa es que se pierdan muestras: lo que no entró en un POST entra
en el siguiente. Ver §4.4.

### 4.4 Las ondas son un flujo, los vitales son estado

Cada POST **consume** las ondas: lo que viaja en uno no vuelve a viajar en otro,
y lo que llega mientras tanto va al siguiente. `vitalSigns` y `ecgInfo` no se
consumen — son el último valor conocido y se republican aunque el equipo no
mande uno nuevo.

> **Cambió en esta versión.** Antes las ondas se vaciaban por reloj, en cada
> cambio de segundo, y el POST sólo copiaba sin consumir. Como el POST nunca
> queda alineado con esa frontera, cada uno se llevaba un pedazo y el resto se
> perdía: medido con reloj real, llegaba el **47 %** de las muestras de ECG, con
> el número por POST ciclando entre 1 y 250. Ahora la cobertura es del 100 %.
>
> Para el backend esto significa más valores de ECG por POST y un tamaño de
> payload más parejo. El impacto total está medido en [§0.3](#03-el-payload-es-20-veces-más-grande).

**`ecgPeaks[].sample`** es el índice dentro de `data.ecg` de este mismo POST, y
`ms` ese índice llevado a tiempo. Sirve para marcar el latido sobre la onda que
viaja al lado. Se vacían junto con la ventana, así que nunca apuntan a muestras
de un POST anterior.

**Las muestras de ECG traen la línea de base en 128** (medido; ver `ecg.md`).
Para tratarlas como amplitudes hay que restar 128.

### 4.5 `vitalSigns` son strings, y por qué

Todos los valores son strings, incluidos los numéricos, y el "sin dato" viaja
como un centinela de guiones:

| Clave | Ejemplo con dato | Sin dato |
|---|---|---|
| `heartRate` | `"81"` | `"-"` |
| `respRate` | `"18"` | `"-"` |
| `spo2Pulse` | `"96/83"` (SpO₂/pulso) | `"- - /- -"` |
| `temperature` | `"22.3"` | `"-"` |
| `nibp` | `"115/52"` (sistólica/diastólica) | `"- - /- -"` |

Cada mitad de `spo2Pulse` se valida por separado: un SpO₂ en error con un pulso
bueno manda `"-/83"`, no borra los dos.

> El `115/52` del ejemplo sale de `tests/capturas/nibp_medicion.bin`: una
> medición de presión real de punta a punta.

**La temperatura puede venir del termómetro USB.** Si `THERMOMETER_ENABLED` está
en `true` y hay una lectura válida, esa pisa la del Berry sobre el payload, ya
convertida a Celsius. No hay una clave `thermometer` aparte: viaja siempre en
`vitalSigns.temperature`.

### 4.6 Cuándo NO manda

El POST se saltea si el payload no tiene ni un dato útil — todos los
`vitalSigns` en su centinela **y** todas las ondas vacías o en cero. Evita
inundar el backend con paquetes sin contenido cuando no hay nadie puesto.

### 4.7 Respuesta y reintentos

Espera **`200`**. Cualquier otro código se loguea como error y el paquete se
descarta: no hay cola ni reenvío posterior, el siguiente POST llega un segundo
después con datos más frescos.

Ante error de red reintenta hasta **3 veces**, con 0,5 s entre intentos.

---

## 5. `POST` de health

`POST {API_URL}/{TOTEM_ID}/{HEALTH_ENDPOINT}` — cada `HEALTH_INTERVAL_SECONDS`,
haya o no sesión. El contrato completo está en **[`health.md`](health.md)**; acá
van los ejemplos.

Acepta `200`, `201` o `204`. Nunca propaga excepciones: un backend caído no
puede tumbar el monitoreo.

### 5.1 Tótem sano y ocioso

De `tests/capturas/ocioso.bin`: todo enchufado, nadie puesto. **Es el estado del
99 % del día y tiene que dar `ok`.**

```json
{
  "totemId": "totem12",
  "status": "ok",
  "uptimeSeconds": 3742.5,
  "device": {
    "transport": "usb", "port": "COM3",
    "connected": true, "portOpen": true, "readerAlive": true,
    "lastFrameSecondsAgo": 0.2, "dataFresh": true
  },
  "pusher": {
    "connected": true, "state": "connected",
    "socketId": "789012.3456789", "threadAlive": true
  },
  "sensors": {
    "spo2":        { "status": "sin_paciente",     "kind": "sin_paciente", "expected": true,  "secondsAgo": 0.3, "stale": false },
    "temperature": { "status": "ok",               "kind": "ok",           "expected": true,  "secondsAgo": 0.3, "stale": false },
    "ecg":         { "status": "electrodo_suelto", "kind": "sin_paciente", "expected": false, "secondsAgo": 0.3, "stale": false,
                     "leadOff": true, "weakSignal": false }
  },
  "disconnectedSensors": [],
  "expectedSensors": ["spo2", "temperature"],
  "sensorsWithoutPatient": ["ecg", "spo2"],
  "session": { "active": false, "secondsElapsed": null }
}
```

Fijate que el SpO₂ dice `sin_paciente` y el ECG `electrodo_suelto` y el estado
general sigue en `ok`: **sin nadie puesto, eso es lo sano**. Alertar por eso
sería ruido permanente.

### 5.2 Una sonda desenchufada del equipo

De `tests/capturas/spo2_sonda_desconectada.bin`. Esto sí es una alerta: alguien
tiene que ir a enchufar algo.

```json
{
  "status": "degraded",
  "sensors": {
    "spo2": { "status": "sensor_desconectado", "kind": "falla", "expected": true, "stale": false }
  },
  "disconnectedSensors": ["spo2"],
  "expectedSensors": ["spo2", "temperature"]
}
```

### 5.3 Una sonda que este tótem no lleva

Mismo tótem ocioso, pero configurado con `HEALTH_EXPECTED_SENSORS=spo2` porque
mide la temperatura con el termómetro USB IR:

```json
{
  "status": "ok",
  "sensors": {
    "temperature": { "status": "t1_desconectado", "kind": "falla", "expected": false }
  },
  "disconnectedSensors": [],
  "expectedSensors": ["spo2"]
}
```

El estado se informa igual —`kind: "falla"`— pero con `expected: false` no mueve
el estado general. **No alertar no es lo mismo que ocultar.**

### 5.4 Qué mirar del lado del backend

| Para saber… | Mirar |
|---|---|
| Si el tótem está vivo | Que el POST llegue. Ya lo contesta. |
| Si el Berry está enganchado | `device.dataFresh` (no `device.connected` solo) |
| Si llegan las órdenes | `pusher.connected` |
| Si hay que mandar a alguien | `disconnectedSensors` no vacío |
| Si hay una medición en curso | `session.active` |

---

## 6. Lo que el tótem escucha: eventos de Pusher

El tótem se suscribe al canal `PUBLIC_CHANNEL` y escucha tres eventos:

| Evento | Nombre | Qué hace |
|---|---|---|
| Arranque | `START_EVENT_NAME` (default `start-monitoring`) | Limpia el estado de la sesión anterior y empieza a postear métricas. |
| Parada | `STOP_EVENT_NAME` (default `stop-monitoring`) | Corta el envío y limpia. |
| Presión | **`start-blood-pressure`** (fijo) | Borra la presión anterior y le pide al equipo una medición nueva. |

Los dos primeros tienen el nombre configurable; **el de presión está fijo en el
código** (`app.py`), no se configura.

No se lee el contenido del evento: alcanza con que llegue.

### 6.1 Por qué el `start` limpia

El `stop` puede no llegar nunca —pestaña cerrada, Pusher caído, la app del otro
lado reiniciando— así que el `start` también limpia. Sin eso, el primer POST de
una medición nueva podría llevarse datos de la anterior.

Por la misma razón existe `MAX_SESSION_MINUTES`: si el `stop` nunca llega, la
sesión se corta sola pasado ese tope. Sin eso el tótem postea una vez por segundo
para siempre. `0` desactiva el corte.

---

## 7. El ciclo de una sesión

```
  tótem arranca
     │
     ├─► POST /health cada 60s ────────────────────────────────► (para siempre)
     │
     ├─► conecta al Berry (reintenta hasta lograrlo)
     │
     ├─► se suscribe al canal de Pusher
     │
     │   ◄── evento start-monitoring
     ├─► limpia estado, POST /metrics cada 1s ──────────────────►
     │
     │   ◄── evento start-blood-pressure
     ├─►    limpia la presión, el equipo infla el manguito
     │      (el resultado aparece en vitalSigns.nibp del POST siguiente)
     │
     │   ◄── evento stop-monitoring   … o MAX_SESSION_MINUTES
     └─► corta el envío y limpia
```

---

## 8. Dónde está cada cosa

| Archivo | Qué aporta |
|---|---|
| `app.py` | `send_data()` (métricas), `send_health()`, `_build_api_url()`, los handlers de Pusher. |
| `config.py` | Lee y valida la config; arma las URLs. |
| `src/data_parser.py` | De las tramas del equipo al objeto `data`. |
| `src/health.py` | Arma el objeto de health y calcula `status`. |
| [`health.md`](health.md) | Contrato completo del `/health`. |
| [`ecg.md`](ecg.md) | Las 7 derivaciones, el muestreo, la línea de base. |
| [`protocolo_berry.md`](protocolo_berry.md) | El protocolo del equipo, aguas arriba de todo esto. |
| [`configuracion.md`](configuracion.md) | Todas las claves de configuración. |
