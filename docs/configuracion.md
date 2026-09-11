# Configuración del tótem

Todas las claves, qué hacen y cómo setearlas.

- [1. Dónde vive la configuración](#1-dónde-vive-la-configuración)
- [2. Cómo setearla](#2-cómo-setearla)
- [3. Las claves](#3-las-claves)
- [4. Ejemplo completo](#4-ejemplo-completo)
- [5. Casos frecuentes](#5-casos-frecuentes)
- [6. Verificar que quedó bien](#6-verificar-que-quedó-bien)

---

## 1. Dónde vive la configuración

Un solo archivo JSON, por usuario de Windows:

```text
%APPDATA%\BerryMed Monitor\credentials.json
```

Ruta típica:

```text
C:\Users\<usuario>\AppData\Roaming\BerryMed Monitor\credentials.json
```

El monitor lo busca en este orden:

1. `BERRYMED_CONFIG_DIR`, si la variable de entorno está definida (para pruebas).
2. `%APPDATA%\BerryMed Monitor` en Windows.
3. `~/.berrymed`, si ya tiene una config propia.
4. La config de Windows, si se está corriendo bajo WSL.

> Es **por usuario**: si el tótem corre con una cuenta distinta a la que usaste
> para configurarlo, no va a encontrar el archivo.

---

## 2. Cómo setearla

### Con la interfaz gráfica (lo normal)

```powershell
.\dist\berry-configure.exe
```

Abre una ventana con los campos agrupados por sección. Se arma sola desde
`src/config_schema.py`, así que siempre muestra todas las claves de la versión
que tengas: **si un campo no aparece, el `.exe` es viejo** — rebuildealo.

### Con el asistente de texto

Si la ventana no abre (sin entorno gráfico, escritorio remoto limitado):

```powershell
.\dist\berry-configure.exe --cli
```

### Editando el JSON a mano

Sirve para cambiar una sola clave sin pasar por todo el asistente. Es un JSON
plano, claves en mayúsculas, todos los valores como **string**:

```json
{ "HEALTH_INTERVAL_SECONDS": "0" }
```

Después de editar a mano, arrancá el monitor y mirá la primera línea del log:
imprime la config que leyó (con los secretos tapados).

---

## 3. Las claves

Obligatorias en **negrita**. El resto tiene default y se puede omitir.

### API

| Clave | Default | Qué hace |
|---|---|---|
| **`TOTEM_ID`** | — | Identificador del tótem. Va en el medio de la URL y tiene que coincidir con el del canal de Pusher. |
| **`API_URL`** | — | **Sólo la base** del servicio, sin el tótem ni el endpoint. Ej: `https://api.ondoctor365.com/v1/medical_devices` |
| `API_ENDPOINT` | `metrics` | Último tramo de la URL de métricas, después del Totem ID. |
| **`API_USERNAME`** | — | Usuario del HTTP Basic. |
| **`API_PASSWORD`** | — | Contraseña del HTTP Basic. Se tapa en el log. |
| `SSL_CERT_FILE_PATH` | *(vacío)* | Bundle `.pem`; se exporta como `SSL_CERT_FILE`. En Windows ya no interviene en la validación TLS: la API y Pusher validan contra el almacén del sistema (`src/ssl_context.py`). |
| `MAX_SESSION_MINUTES` | `30` | Si el evento de stop nunca llega, la sesión se corta sola pasados estos minutos. **`0` desactiva el corte** — y entonces el tótem puede quedar posteando para siempre. |

La URL final queda `{API_URL}/{TOTEM_ID}/{API_ENDPOINT}`. Ver
[`backend.md`](backend.md) §2.

### Health

| Clave | Default | Qué hace |
|---|---|---|
| `HEALTH_ENDPOINT` | `health` | Último tramo de la URL de health. Cuelga de la misma base y el mismo Totem ID que las métricas. |
| `HEALTH_INTERVAL_SECONDS` | `60` | Cada cuánto se reporta el estado. **`0` desactiva el reporte.** |
| `HEALTH_EXPECTED_SENSORS` | `spo2,temperature` | Sondas que **este** tótem lleva puestas, separadas por coma. Sólo la desconexión de éstas pone el estado en `degraded`. Vacío = no vigilar ninguna. |

> **`HEALTH_EXPECTED_SENSORS` importa más de lo que parece.** Si el tótem mide la
> temperatura con el termómetro USB y no tiene la sonda del Berry, el equipo la
> informa desconectada **en cada paquete, para siempre**: dejar el default deja
> el health en `degraded` de forma permanente. Ver §5.
>
> Sólo `spo2` y `temperature` se pueden vigilar — son las únicas dos
> desconexiones que el protocolo del equipo transmite. Cualquier otro valor se
> ignora con un aviso en el log.

### Pusher

| Clave | Default | Qué hace |
|---|---|---|
| **`PUSHER_KEY`** | — | App key de Pusher. Por ahí llegan las órdenes. |
| `PUSHER_CLUSTER` | `us2` | Cluster de la app de Pusher. |
| **`PUBLIC_CHANNEL`** | — | Canal al que se suscribe el tótem. Suele ser `totem.<TOTEM_ID>`. |
| `START_EVENT_NAME` | `start-monitoring` | Evento que habilita el envío. |
| `STOP_EVENT_NAME` | `stop-monitoring` | Evento que lo corta. |

> El evento de presión es **`start-blood-pressure` y está fijo en el código**: no
> se configura.

### Equipo (Berry PM6750)

| Clave | Default | Valores | Qué hace |
|---|---|---|---|
| `DEVICE_CONNECTION` | `bt` | `bt` · `usb` | `bt` = Bluetooth LE, `usb` = serie/RS232. |
| `DEVICE_PORT` | `COM3` | | Puerto serie del Berry. **Sólo se usa con `usb`.** |

> Con `usb`, el puerto puede tardar casi un minuto en aparecer después de
> arrancar Windows. El monitor reintenta solo.

### Parámetros del PM6750

| Clave | Default | Valores | Qué hace |
|---|---|---|---|
| `ECG_MODE` | `monitor` | `operacion` (1–25 Hz) · `monitor` (0,5–75 Hz) · `diagnostico` (0,05–100 Hz) | Filtro del ECG. `diagnostico` es el único que conserva el segmento ST que se reporta en `ecgInfo.st`. |
| `ECG_GAIN` | `x1` | `x0.25` · `x0.5` · `x1` · `x2` | **Bajarla si la onda satura** (valores pegados a 1 o 249). Ver [`ecg.md`](ecg.md) §10. |
| `RESP_GAIN` | `x1` | `x0.25` · `x0.5` · `x1` · `x2` | Ganancia del canal de respiración. |
| `NIBP_MODE` | `adulto` | `adulto` · `nino` · `neonato` | Define el rango de presión del manguito: 40–300, 40–210 y 40–140 mmHg. |
| `NIBP_TARGET_PRESSURE_MMHG` | *(sin valor)* | | Presión objetivo del inflado. **No aparece en el configurador**: se escribe a mano en el JSON. Sin valor, el equipo usa su default. Fuera del rango del modo, se ignora con un aviso. |

### Termómetro USB IR

| Clave | Default | Qué hace |
|---|---|---|
| `THERMOMETER_ENABLED` | `false` | Dispositivo aparte del PM6750. Con una lectura válida, **su valor pisa la temperatura** del payload. |
| `THERMOMETER_PORT` | `COM5` | Puerto serie. **Tiene que ser distinto al del Berry.** |
| `THERMOMETER_BAUD` | `115200` | Baudios. |
| `THERMOMETER_RECONNECT_SECONDS` | `2.0` | Espera entre reintentos de conexión. |

---

## 4. Ejemplo completo

```json
{
  "TOTEM_ID": "totem12",
  "API_URL": "https://api.ejemplo.com/v1/medical_devices",
  "API_ENDPOINT": "metrics",
  "API_USERNAME": "totem",
  "API_PASSWORD": "••••••••",
  "SSL_CERT_FILE_PATH": "C:\\BerryMed\\cacert.pem",
  "MAX_SESSION_MINUTES": "30",

  "HEALTH_ENDPOINT": "health",
  "HEALTH_INTERVAL_SECONDS": "60",
  "HEALTH_EXPECTED_SENSORS": "spo2,temperature",

  "PUSHER_KEY": "abc123def456",
  "PUSHER_CLUSTER": "us2",
  "PUBLIC_CHANNEL": "totem.totem12",
  "START_EVENT_NAME": "start-monitoring",
  "STOP_EVENT_NAME": "stop-monitoring",

  "DEVICE_CONNECTION": "usb",
  "DEVICE_PORT": "COM3",

  "ECG_MODE": "monitor",
  "ECG_GAIN": "x0.5",
  "RESP_GAIN": "x1",
  "NIBP_MODE": "adulto",

  "THERMOMETER_ENABLED": "true",
  "THERMOMETER_PORT": "COM5",
  "THERMOMETER_BAUD": "115200",
  "THERMOMETER_RECONNECT_SECONDS": "2.0"
}
```

---

## 5. Casos frecuentes

### El tótem no tiene sonda de temperatura del Berry

Mide con el termómetro USB IR. El equipo reporta la sonda desconectada
permanentemente y el health quedaría en `degraded` para siempre:

```json
{ "HEALTH_EXPECTED_SENSORS": "spo2" }
```

### El backend todavía no armó el `/health`

Cada reporte va a dar 404 y llenar el log rotado. Hasta que exista:

```json
{ "HEALTH_INTERVAL_SECONDS": "0" }
```

### La onda de ECG sale recortada

Valores pegados a 1 y 249 con `leadOff: false`: no son los electrodos, es el
amplificador saturando. Bajá la ganancia:

```json
{ "ECG_GAIN": "x0.5" }
```

### Probar sin backend real

```powershell
python tools\mock_api_server.py
```

Y en la config, `API_URL` a `http://127.0.0.1:8080/vitals` — con `127.0.0.1`, no
`0.0.0.0`. El mock acepta cualquier path, así que `/metrics` y `/health` andan
sin configurar nada más.

### Sesiones que quedan abiertas

Si el evento de stop no llega de forma confiable, bajá el tope:

```json
{ "MAX_SESSION_MINUTES": "10" }
```

---

## 6. Verificar que quedó bien

Arrancá el monitor y mirá el arranque:

```powershell
.\dist\berry-monitor.exe
```

Tiene que imprimir, en las primeras líneas:

1. **La config leída**, con `api_password` y `key` tapadas como `***`. Si falta
   una clave que esperabas, el `.exe` es viejo o el JSON no es el que creés.
2. **Los dos endpoints**:
   ```text
   [DEBUG] Endpoint de métricas: https://api.ejemplo.com/v1/medical_devices/totem12/metrics
   [DEBUG] Endpoint de health: https://api.ejemplo.com/v1/medical_devices/totem12/health (cada 60s)
   ```
3. **La conexión al equipo**, si es USB:
   ```text
   [USB] Puerto COM3 abierto
   [USB] -> enables (48B): 55aa040703f155aa040802f155aa040f03e9...
   [USB] Datos OK tras 0.0s (64 bytes)
   ```
   Esa última línea es la que confirma que el Berry está transmitiendo.
4. **El termómetro**, si está habilitado: `[THERM] Reader started on COM5 @ 115200`.

Y cada 60 segundos, el health:

```text
[HEALTH] ok (sensores sueltos: ninguno)
```

Los secretos nunca se escriben al log: la salida se duplica a un archivo rotado
que sobrevive al proceso, así que la contraseña de la API y la key de Pusher van
tapadas.

---

## 7. Dónde está cada cosa

| Archivo | Qué aporta |
|---|---|
| `src/config_schema.py` | **La lista de campos.** El configurador se arma sola de acá: agregar un campo es agregarlo acá. |
| `config.py` | Lee el JSON, aplica defaults y valida. |
| `src/configure_gui.py` | La ventana del asistente. |
| `configure.py` | Punto de entrada; elige entre ventana y asistente de texto. |
| [`backend.md`](backend.md) | Qué se manda y a qué endpoints. |
| [`health.md`](health.md) | El contrato del `/health`. |
