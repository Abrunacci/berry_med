# ECG — qué manda el Berry y qué se envía al backend

Qué transmite el monitor BerryMed por ECG, cómo lo procesa la aplicación y qué
termina en el payload de la API.

Fuentes: manual técnico del PM6750 (`docs/manual_berry.pdf`) y verificación
sobre capturas reales del stream (`tools/ecg_audit.py`).

> Este documento cubre sólo el ECG. El protocolo completo — todos los comandos,
> todos los paquetes, las dos vías de conexión y el payload entero — está en
> **[`protocolo_berry.md`](protocolo_berry.md)**.

---

## 1. Resumen

- Con el cable de **5 electrodos (RA, LA, LL, RL, V)**, el equipo envía por ECG
  **7 derivaciones simultáneas**: **I, II, III, aVR, aVL, aVF, V**.
- Cada una a **~251 muestras por segundo** (un "instante" de las 7 derivaciones
  por paquete, ~251 paquetes/seg).
- La aplicación envía **las 7 derivaciones completas** en `data.ecg`, más las
  marcas de latido en `data.ecgPeaks` y el estado del ECG en `data.ecgInfo`.

---

## 2. Trama base

Todos los datos vienen en tramas con este formato:

```
[0x55, 0xAA, length, type, dato0, dato1, ... , checksum]
   0     1      2      3     4      5             -1
```

- `0x55 0xAA` — cabecera fija de inicio de trama.
- `length` — largo; el fin de trama es `inicio + length + 2`.
- `type` — identifica el contenido.
- `checksum` — `~sum(package[2:-1]) & 0xFF`.

Referencia: `src/data_parser.py` (`add_data`, `_check_sum`).

La app **habilita** el envío mandando comandos `55 AA 04 0X 01 CS` al conectar.
El equipo no transmite hasta recibirlos.

---

## 3. Paquetes de ECG

| type   | Nombre interno              | Contenido                              | Destino en el payload |
|--------|-----------------------------|----------------------------------------|-----------------------|
| `0x01` | `on_ecg_waveform_received`  | 7 derivaciones (onda)                  | `data.ecg` |
| `0x02` | `on_ecg_params_received`    | Estado, HR, FR, ST, código HRV         | `data.vitalSigns` + `data.ecgInfo` |
| `0x30` | `on_ecg_peak_received`      | Marca de pico/latido (QRS)             | `data.ecgPeaks` |

---

## 4. Detalle de cada paquete

### 4.1 Onda de ECG — `0x01` (las 7 derivaciones)

Ejemplo real de trama: `55 AA 0A 01  F9 89 01 08 F9 18 30  28`

- `length = 0x0A` → 7 bytes de datos + checksum.
- Cada byte es el valor de **una derivación** en ese instante (rango 0–250 según
  el manual, línea base en 128 = `0x80`).
- Orden: `[I, II, III, aVR, aVL, aVF, V]`.

| byte del paquete | `package[4]` | `[5]` | `[6]` | `[7]` | `[8]` | `[9]` | `[10]` |
|---|---|---|---|---|---|---|---|
| campo del manual | A2 | A3 | A4 | A5 | A6 | A7 | A8 |
| **derivación**   | **I** | **II** | **III** | **aVR** | **aVL** | **aVF** | **V** |

El orden está fijado en el código por `BMDataParser.ECG_LEADS`.

**Doble evidencia del orden:**

1. **Manual del PM6750** (pág. 10, tabla *Module→PC (data format)*): la fila
   `ECG waves / 0x01` lista las columnas A2…A8 como `I, II, III, aVR, aVL, aVF,
   V`, "Range from 0~250, transmit frequency 250bps".
2. **Verificación sobre la captura real.** Con ese orden se cumplen las
   relaciones fisiológicas que *deben* cumplirse entre derivaciones (Einthoven
   y Goldberger), con error máximo de **±2 LSB** — puro redondeo, porque las
   derivadas llevan una división por 2:

   | relación | error máx. medido |
   |---|---|
   | `II = I + III`      | 2 LSB |
   | `aVR = -(I + II)/2` | 1,5 LSB |
   | `aVL = (I - III)/2` | 2 LSB |
   | `aVF = (II + III)/2`| 1,5 LSB |

   Probando **todas** las permutaciones de las 3 primeras posiciones, sólo la
   del manual cumple Einthoven (error 0,86 LSB contra 97–117 LSB de cualquier
   otra). El orden no es una suposición.

> Corolario útil: **sólo I, II y V son independientes**. Las otras 4 son
> combinaciones lineales exactas de I y II. Se envían igual las 7 para que el
> backend no tenga que derivarlas.

> Los 7 bytes son 7 derivaciones del **mismo instante**, no 7 muestras
> consecutivas en el tiempo. Concatenarlos en un solo array mezcla los canales
> y produce una señal sin sentido (evidencia en §5).

### 4.2 Parámetros de ECG — `0x02`

| byte         | Significado (manual pág. 10)    | Destino                  |
|--------------|---------------------------------|--------------------------|
| `package[4]` | estado (`ECG status`)           | `ecgInfo.leadOff`, `.weakSignal`, `.gain`, `.mode` |
| `package[5]` | frecuencia cardíaca (0–250)     | `vitalSigns.heartRate`   |
| `package[6]` | frecuencia respiratoria (0–250) | `vitalSigns.respRate`    |
| `package[7]` | ST (signed char, −100…+100 = −1 mV…+1 mV) | `ecgInfo.st` (en mV) |
| `package[8]` | código HRV (arritmia)           | `ecgInfo.hrv`            |

`0` en HR/FR se traduce a `"-"` (sin dato).

Bits del byte de estado (manual pág. 10):

| bits | Significado |
|---|---|
| BIT0 | Señal débil (`1` = débil) |
| BIT1 | **Electrodo suelto** (`1` = lead off) |
| BIT3~2 | Ganancia: `00` x0.25, `01` x0.5, `10` x1, `11` x2 |
| BIT5~4 | Modo: `00` operación, `01` monitor, `10` diagnóstico |

Códigos HRV (`BMDataParser.HRV_CODES`): `0x00` analizando, `0x01` normal,
`0x02` paro, `0x03` fibrilación ventricular, `0x04` R sobre T, `0x05`–`0x07`
extrasístoles ventriculares, `0x08` bigeminismo, `0x09` trigeminismo,
`0x0A` taquicardia, `0x0B` bradicardia, `0x0C` latido perdido.

### 4.3 Pico / latido — `0x30`

Paquete sin bytes de datos: el paquete en sí es el evento. El equipo manda
**uno por cada latido (QRS) detectado**.

Se guarda en `data.ecgPeaks` como la posición del latido dentro de la ventana de
ECG del mismo POST, para poder marcarlo sobre la onda:

```json
{"sample": 192, "ms": 768.0}
```

`sample` es el índice dentro de `data.ecg.*`; `ms` es ese índice llevado a
tiempo con `ECG_SAMPLE_RATE_HZ` (250 Hz nominal).

Sirve para marcar latidos sobre la tira y para calcular variabilidad R-R, que el
`0x02` no permite porque ahí el HR ya viene promediado y a 1 Hz.

**Este paquete no figura en el manual** — la tabla *Module→PC* de la pág. 10 no
lo lista. El nombre viene del SDK de BerryMed y que sea la marca de QRS se
verificó midiendo: usando los paquetes de onda como reloj (~251 Hz), el
intervalo mediano entre `0x30` es de **718 ms ≈ 84 lpm**, contra los **86 lpm**
que reportaba el `0x02` en la misma captura.

---

## 5. Evidencia: por qué son 7 derivaciones y no 7 muestras

Sobre 7.505 paquetes `0x01` capturados con electrodos, se midió la variación de
cada byte:

| Medición | pos0 | pos1 | pos2 | pos3 | pos4 | pos5 | pos6 |
|----------|------|------|------|------|------|------|------|
| Salto **en el tiempo** (misma posición, paquete a paquete) | 20.5 | 2.5 | 22.0 | 17.5 | 21.5 | 17.9 | 7.2 |

**Salto DENTRO del mismo paquete (entre bytes consecutivos): 111.3**

Interpretación:

- Si los 7 bytes fueran muestras consecutivas a 1756 Hz, el salto entre ellas
  sería **chico** (señal suave). Es **enorme (111)**.
- En cambio, **cada posición por separado, a lo largo del tiempo, es suave**
  (saltos de 2.5 a 22) → cada posición es un **canal coherente** (una
  derivación) muestreado a ~251 Hz.

Conclusión: **7 bytes = 7 derivaciones del mismo instante.**

---

## 6. Números reales de la captura (30 s, con electrodos)

| Métrica | Valor |
|---|---|
| Bytes crudos recibidos | 109.056 |
| Tramas totales | 10.618 |
| Paquetes `0x01` (onda ECG) | 7.505 |
| Derivaciones por paquete | **7** (100%) |
| Frecuencia de paquetes | **250,7 /seg** |
| → muestras/seg por derivación | **~251 Hz** |
| Paquetes `0x02` (HR/FR) | 30 (~1/seg) |
| Paquetes `0x30` (pico) | 28 (1 por latido) |
| Paquetes `0xFE` (onda SpO₂) | 1.499 (~50/seg) |
| Paquetes `0xFF` (onda RESP) | 1.500 (~50/seg) |
| Checksums inválidos | 0 |

---

## 7. Qué recibimos vs. qué enviamos

| | El Berry ENVÍA | La app ENVÍA al backend |
|---|---|---|
| Derivaciones de ECG | 7 (I, II, III, aVR, aVL, aVF, V) | 7 (`data.ecg`) |
| Frecuencia de la onda | ~251 Hz por derivación | ~251 Hz por derivación |
| Frecuencia cardíaca (HR) | sí (`0x02`) | sí (`vitalSigns.heartRate`) |
| Frecuencia respiratoria | sí (`0x02`) | sí (`vitalSigns.respRate`) |
| ST y código HRV (`0x02`) | sí | sí (`ecgInfo.st`, `.hrv`) |
| Estado del ECG / lead-off (`0x02`) | sí | sí (`ecgInfo.leadOff`, `.weakSignal`, `.gain`, `.mode`) |
| Marca de pico/QRS (`0x30`) | sí (1 por latido) | sí (`data.ecgPeaks`) |

---

## 8. Formato del payload

```jsonc
{
  "timestamp": 1754400000000,
  "data": {
    "ecg": {                           // las 7 derivaciones
      "I":   [128, 130, ...],          // ~251 valores/seg cada una
      "II":  [131, 133, ...],
      "III": [...], "aVR": [...],
      "aVL": [...], "aVF": [...],
      "V":   [...]
    },
    "ecgPeaks": [                      // latidos (QRS) de esta ventana
      {"sample": 192, "ms": 768.0}     // posición dentro de ecg.*
    ],
    "ecgInfo": {                       // estado y diagnóstico del ECG
      "leadOff": false, "weakSignal": false,
      "gain": "x1", "mode": "monitor",
      "st": 0.06, "hrv": "normal"
    },
    "spo2": [...],
    "resp": [...],
    "vitalSigns": { "heartRate": "85", "nibp": "- - /- -", ... }
  }
}
```

Todos los arrays se **vacían y se rearman cada segundo** (una ventana de 1 s por
POST). Volumen: ~251 × 7 ≈ **1.750 valores/seg**, ~10 KB de JSON por POST.

`ecgPeaks` comparte ventana con la onda: `sample` es el índice dentro de
`data.ecg.*` del mismo POST, así que el latido se puede marcar directamente
sobre la tira sin correlacionar timestamps.

Sin dato se representa como `"-"` o `"- -"` en `vitalSigns`, y como `null` en
`ecgInfo`.

---

## 9. Cómo auditar el stream

Herramienta: `tools/ecg_audit.py` (captura y analiza el stream real).

```bash
python tools/ecg_audit.py --port COM6 --seconds 30 --record captura.bin
```

- Sección `[0]` — bytes/tramas/tipos (confirma que el equipo transmite).
- Sección `[2]` — derivaciones por paquete (dice 7) y ejemplos de valores.
- Sección `[6]` — frecuencia real.

Para volver a analizar una captura guardada, sin el equipo:

```bash
python tools/ecg_audit.py --file captura.bin
```

Nota operativa (WSL): el equipo es un STM32 VCP; si se usó `usbip`, hacer
`usbipd unbind` + reconectar el USB antes de usarlo en Windows, o el puerto
queda mudo (0 bytes).

---

## 10. Pendientes

- **⚠️ La captura de referencia está saturada.** El **98,1 %** de los paquetes
  `0x01` tienen al menos un canal pegado al tope (≤2 o ≥247): quedaron sólo 142
  muestras limpias de 7.505. Alcanzó para confirmar el orden de las
  derivaciones, pero **no sirve para evaluar calidad de señal**. Dato clave: en
  esa misma captura el equipo reportaba `leadOff: False` y `gain: x1`, o sea que
  **no era electrodo suelto sino el amplificador saturando**. Conviene repetir
  con `ECG_GAIN=x0.5` (o `x0.25`) y electrodos bien puestos (guía AHA: RA
  blanco, LA negro, RL verde, LL rojo, V marrón).

- **Decidir el modo de ECG según el uso.** Hoy queda en `monitor` (0,5–75 Hz),
  que es lo que el equipo usa de fábrica. Si el uso es diagnóstico, poner
  `ECG_MODE=diagnostico` (0,05–100 Hz): el modo monitor filtra justamente el
  segmento ST que se reporta en `ecgInfo.st`.

- **Aprovechar `ecgInfo` en el backend/UI.** Con `leadOff` y `weakSignal` se
  puede avisar "electrodo suelto" en vez de graficar una señal inservible, y con
  `hrv` mostrar la arritmia que el propio equipo detecta.

- Pendientes de otros parámetros (presión arterial media, segundo canal de
  temperatura, `0x31`): ver [`protocolo_berry.md`](protocolo_berry.md) §10.
