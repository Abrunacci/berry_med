# Berry Med: cambios en mediciones y /health (29/10/2026)

Resumen para el responsable del backend: sólo lo que cambia, en formato «antes
era esto, ahora es esto». El contrato completo está en
[`backend.md`](backend.md) (§0.9, §0.10, §5 y §6) y [`health.md`](health.md).

**No hay cambios incompatibles**: el POST de métricas y el `/health` conservan
todas sus claves. Hay eventos nuevos, un campo nuevo y valores que cambian de
origen.

## 1. Medición por instrumento desde Pusher

| Tema | Antes | Ahora |
|---|---|---|
| Comandos de medición | Sólo `start-monitoring`, que publica todos los instrumentos. | Además, uno por instrumento: `start-ecg`, `start-spo2`, `start-nibp` y `start-temperature`. En el mismo canal `PUBLIC_CHANNEL`. Cada nombre se configura como `START_EVENT_NAME` (`START_ECG_EVENT_NAME`, `START_SPO2_EVENT_NAME`, `START_NIBP_EVENT_NAME`, `START_TEMPERATURE_EVENT_NAME`); estos son los defaults. |
| Qué publica | El POST de métricas con todo. | Con `start-<instrumento>`, el **mismo POST con las mismas claves**, pero sólo con ese instrumento. Lo demás va en "sin dato": `"- -"` / `"- - /- -"` en `vitalSigns`, listas vacías en las ondas, las 7 derivaciones vacías y `ecgInfo` con sus campos en `null`. |
| Campos por comando | — | `start-ecg`: `ecg`, `ecgPeaks`, `ecgInfo`, `resp`, `heartRate`, `respRate` (la respiración va con el ECG: mismos electrodos). `start-spo2`: `spo2`, `spo2Pulse`. `start-nibp`: `nibp`. `start-temperature`: `temperature`. |
| `start-nibp` | — | Hace lo mismo que `start-blood-pressure` (borra la presión anterior e infla el manguito), pero además publica sólo la presión. Mientras infla no sale ningún POST; el primero trae el resultado. |
| Pedido con otra medición en curso | `start-monitoring` limpiaba y volvía a arrancar. | Cualquier pedido (`start-*` o `start-monitoring`) **corta la medición en curso** y arranca la nueva: deja de publicar la anterior, desinfla el manguito si estaba inflando, limpia todo lo medido y reinicia el tope de `MAX_SESSION_MINUTES`. |
| Fin de una medición | `stop-monitoring` o `MAX_SESSION_MINUTES`. | Igual, para cualquier tipo de medición, o un pedido nuevo. |
| `start-monitoring`, `stop-monitoring`, `start-blood-pressure` | — | Sin cambios. |
| `/health` → `session` | `{ "active", "secondsElapsed" }` | Suma `measurement`: `"monitoring"`, `"ecg"`, `"spo2"`, `"nibp"`, `"temperature"`, o `null` sin sesión. |

## 2. Temperatura: sólo el termómetro USB

La sonda de temperatura del propio Berry (PM6750) ya no se usa.

| Tema | Antes (1.0.10) | Ahora |
|---|---|---|
| `vitalSigns.temperature` (métricas) | El termómetro USB si tenía lectura; si no, la sonda del Berry (sin sonda puesta, la temperatura ambiente o `"-"`). | **Sólo el termómetro USB.** Sin lectura, `"- -"`. |
| `sensors.temperature` (`/health`) | Estado de la sonda del Berry (`t1_desconectado`, …). | Estado del termómetro USB: `ok`, `termometro_desconectado` o `termometro_no_habilitado`. Suma `source: "usb_thermometer"` y `port`. |
| Tótem con termómetro USB y config por defecto (`spo2,temperature`) | `degraded` permanente: el Berry informaba su sonda suelta. | **`ok`**, sin tocar la configuración al actualizar. |
| Termómetro con el puerto que no abre | Sólo alertaba con `usb_thermometer` configurado: `disconnectedSensors: ["usb_thermometer"]`. | `degraded`, con `disconnectedSensors: ["temperature"]`. El motivo, en `thermometer.error`. |
| Tótem sin termómetro USB (`THERMOMETER_ENABLED=false`), config por defecto | `ok` o `degraded` según la sonda del Berry. | `degraded`: `sensors.temperature.status: "termometro_no_habilitado"`, `"temperature"` en `disconnectedSensors`. Un tótem que no mide temperatura se configura con `HEALTH_EXPECTED_SENSORS=spo2` y da `ok`. |
| `"usb_thermometer"` | Valor posible en `disconnectedSensors` y `expectedSensors`. | **Ya no aparece en el reporte**: se informa como `"temperature"`. En la configuración del tótem se sigue aceptando como sinónimo. |
| Bloque `thermometer` | Estado del termómetro; `expected` según `usb_thermometer`. | Misma forma, como detalle de `sensors.temperature` (puerto, error, última lectura). `expected` es el mismo que el de `sensors.temperature`; la desconexión se cuenta una sola vez. |

## 3. Qué revisar en el backend

1. Si se alerta por `"usb_thermometer"` en `disconnectedSensors`, pasar a
   `"temperature"`.
2. Para usar la medición por instrumento, disparar `start-<instrumento>` en
   lugar de `start-monitoring` (+ `start-blood-pressure`).
3. Opcional: guardar `session.measurement` junto al reporte de health.
