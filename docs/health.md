# `/health` — qué informa el tótem sobre su estado

Cómo el tótem reporta si está en condiciones de trabajar, qué mide para
decidirlo y qué objeto postea.

Fuentes: manual técnico del PM6750 (`docs/manual_berry.pdf`) para los estados de
los sensores, e implementación en `src/health.py`.

> El protocolo completo del equipo — comandos, paquetes y el payload de
> métricas — está en **[`protocolo_berry.md`](protocolo_berry.md)**.

---

## 1. Resumen

- El tótem **postea** su estado a `{API_URL}/{TOTEM_ID}/{HEALTH_ENDPOINT}`. No
  hay un endpoint que consultar del lado del tótem: es un cliente, no escucha en
  ningún puerto.
- Corre en su propia tarea, cada `HEALTH_INTERVAL_SECONDS` (default 60), y
  **arranca antes de que el equipo conecte**.
- Responde cuatro preguntas, en orden de importancia:

  | # | Pregunta | Dónde se contesta |
  |---|---|---|
  | 1 | ¿El tótem está vivo? | que el POST llegue ya lo contesta |
  | 2 | ¿El Berry está enganchado? | `device` |
  | 3 | ¿Llegan las órdenes del backend? | `pusher` |
  | 4 | ¿Las sondas están conectadas? | `disconnectedSensors` |

- Y agrega un aviso aparte, que no cambia el estado: si el tótem tiene
  certificados de la cadena del backend vencidos o por vencer
  (`certificates.warnings`, §6).

---

## 2. La regla: nada se lee de un flag, todo se mide

Es la decisión que condiciona el resto del diseño, y viene de un bug real: la
app tenía un `connected` que quedaba en `True` para siempre si el hilo lector se
moría. Un health basado en ese flag habría informado "todo bien" sobre un cable
muerto — que es justamente el caso que hay que detectar.

Por eso:

- **El equipo** no se pregunta por un booleano sino por `link_status()`, que
  mira lo real: puerto abierto, hilo lector vivo y **antigüedad de la última
  trama**. Un enlace abierto por el que hace 30 s que no llega nada está caído
  aunque el puerto siga abierto.
- **Pusher** se lee de `connection.state` de pysher, no de un flag propio.

---

## 3. Sensores: falla ≠ ausencia de paciente

La distinción que decide si un sensor es una alerta o un dato.

El manual separa las dos cosas explícitamente para el SpO₂ (pág. 12):

```
0x01   sensor off     la sonda no está conectada al equipo
0x02   no finger      la sonda está puesta, no hay paciente
```

**Sólo la primera es una falla.** La segunda es el estado normal de un tótem
esperando al primer paciente: alertar por ella sería ruido permanente, todo el
día. Lo mismo en temperatura, donde el manual usa la misma palabra
(`T1 sensor off`).

### 3.1 Qué sirve como falla y qué no

| Sensor | Estado | ¿Falla? | Por qué |
|---|---|---|---|
| SpO₂ | `sensor_desconectado` (`0x01`) | **Sí** | la sonda no está en el equipo |
| SpO₂ | `sin_paciente` (`0x02`) | No | sonda puesta, nadie conectado |
| SpO₂ | `buscando_pulso`, `busqueda_demasiado_larga` | No | en progreso |
| Temperatura | `t1_desconectado`, `t2_desconectado`, `t1_y_t2_desconectados` | **Sí** | la sonda no está en el equipo |
| ECG | `electrodo_suelto` (BIT1) | No | ver 3.2 |
| ECG | `senal_debil` (BIT0) | No | calidad de señal, no conexión |
| NIBP | `manguito_flojo` y demás | No | resultado de una medición, no un estado |

### 3.2 El ECG no puede informar una falla

Aunque el nombre sugiera otra cosa. El lead-off se detecta **por impedancia
entre electrodos**, así que sin nadie conectado da positivo siempre: no
distingue un cable desenchufado del equipo de un tótem ocioso.

### 3.3 El NIBP tampoco

`manguito_flojo` es el resultado de una medición que salió mal, no un estado
permanente del equipo, y sólo aparece si alguien pidió una medición.

### 3.5 No todos los tótems llevan las mismas sondas

Una falla sólo cuenta si esa sonda se usa en **este** tótem. El caso concreto:
cuando la temperatura se mide con el termómetro USB IR, la sonda de temperatura
del Berry no se conecta nunca, y el equipo informa `t1_desconectado` en cada
paquete, de forma permanente. Sin esta condición ese tótem reporta `degraded`
para siempre — el mismo ruido permanente que el "no finger" del SpO₂, y por la
misma razón: una alerta que suena siempre no es una alerta.

Se declara en `HEALTH_EXPECTED_SENSORS` (§8). Sólo `spo2` y `temperature` se
pueden vigilar: son las únicas dos desconexiones que el protocolo transmite
(§3.4).

El estado de una sonda no vigilada **se sigue publicando**, con
`expected: false` al lado. No alertar no es lo mismo que ocultar.

Esto no se dedujo leyendo el código: apareció cuando las capturas del equipo
real mostraron `t1_desconectado` en los cinco escenarios, incluido el que tenía
un paciente puesto y todo lo demás sano.

### 3.4 Límite conocido

**Las únicas dos fallas de sensor detectables son SpO₂ y temperatura.** Si
alguien desconecta el cable de ECG o el manguito del Berry, el `/health` no se
entera: el equipo no informa nada que lo distinga de "no hay paciente". No es
algo que se pueda agregar después — el protocolo no lo transmite.

---

## 4. El objeto que se postea

```json
{
  "totemId": "01ksj3g36a6rnvgwszt7fs4jjn",
  "status": "ok",
  "uptimeSeconds": 3612.4,
  "device": {
    "transport": "usb",
    "port": "COM6",
    "connected": true,
    "portOpen": true,
    "readerAlive": true,
    "lastFrameSecondsAgo": 0.2,
    "dataFresh": true
  },
  "pusher": {
    "connected": true,
    "state": "connected",
    "socketId": "987.654",
    "threadAlive": true
  },
  "sensors": {
    "spo2": {
      "status": "sin_paciente",
      "secondsAgo": 0.0,
      "stale": false,
      "kind": "sin_paciente",
      "expected": true
    },
    "ecg": {
      "status": "electrodo_suelto",
      "leadOff": true,
      "weakSignal": false,
      "secondsAgo": 0.0,
      "stale": false,
      "kind": "sin_paciente",
      "expected": false
    }
  },
  "disconnectedSensors": [],
  "expectedSensors": ["spo2"],
  "sensorsWithoutPatient": ["ecg", "spo2"],
  "session": { "active": false, "secondsElapsed": null },
  "certificates": {
    "checked": true,
    "checkedSecondsAgo": 3598.2,
    "inspected": 2,
    "warnDays": 30,
    "warnings": [
      {
        "subject": "ISRG Root X2",
        "issuer": "ISRG Root X1",
        "stores": ["CA"],
        "notAfter": "2025-09-15T16:00:00Z",
        "daysLeft": -360,
        "expired": true,
        "sha1": "151682F5218C0A511C28F4060A73B9CA78CE9A53"
      }
    ],
    "error": null
  }
}
```

### 4.1 Campos

| Campo | Qué es |
|---|---|
| `status` | `ok` \| `degraded` \| `down`. Ver §5. |
| `uptimeSeconds` | Segundos desde que arrancó el proceso. |
| `device.connected` | Puerto abierto **y** hilo lector vivo. |
| `device.readerAlive` | El hilo lector sigue corriendo. Si es `false` con `portOpen: true`, el lector murió. |
| `device.lastFrameSecondsAgo` | Antigüedad de la última trama. `null` = nunca llegó nada. **La señal que más importa.** |
| `device.dataFresh` | `lastFrameSecondsAgo <= 5`. |
| `pusher.state` | Estado crudo de pysher: distingue "conectando" de "se cayó". |
| `sensors.<s>.status` | Último estado que informó el equipo. |
| `sensors.<s>.kind` | `falla` \| `sin_paciente` \| `ok` \| `info`. **Es el campo a mirar**: evita conocer las tablas del protocolo. |
| `sensors.<s>.secondsAgo` | Antigüedad de ese estado. |
| `sensors.<s>.stale` | `secondsAgo > 30`. Un estado viejo no describe la realidad. |
| `sensors.<s>.expected` | Si este tótem lleva esa sonda. Con `false`, su desconexión se informa pero **no alerta**. Ver §3.5. |
| `disconnectedSensors` | Sondas que este tótem usa y no están conectadas. **Esto sí es una alerta.** |
| `expectedSensors` | Qué sondas se vigilan acá. Permite distinguir "no hay alerta" de "no se está mirando". |
| `sensorsWithoutPatient` | Conectadas pero sin nadie puesto. Informativo. |
| `session.active` | Si hay una sesión de medición en curso. |
| `certificates.warnings` | Certificados de la cadena del backend, guardados en el tótem, vencidos o que vencen en menos de `warnDays` días. **Es un aviso**: no cambia `status`. Ver §6. |
| `certificates.warnings[].daysLeft` | Días que le quedan. Negativo = vencido hace esos días (`expired: true`). |
| `certificates.warnings[].stores` | En qué almacén de Windows está: `CA` (intermedios) o `ROOT` (raíces). |
| `certificates.checked` | Si la revisión se pudo hacer. Con `false`, el motivo va en `certificates.error`. |
| `certificates.inspected` | Cuántos certificados de la cadena se encontraron en el almacén. Distingue "no hay avisos" de "no había nada que mirar". |
| `certificates.checkedSecondsAgo` | Antigüedad de la revisión: se hace una vez por día. |

> Los sensores que el equipo todavía no reportó **no aparecen** en `sensors`. No
> se inventa un "desconocido" que taparía el dato real.

---

## 5. Cómo se calcula `status`

```
down       el equipo no está enganchado o no llegan datos frescos,
           o Pusher está caído
degraded   todo lo anterior bien, pero hay al menos una sonda desconectada
ok         todo lo anterior bien y ninguna sonda desconectada
```

Un sensor suelto **no** baja a `down`: es información clínica, no una falla de
operación, y no debería disparar la misma alarma que un tótem incomunicado.

### 5.1 Comportamiento verificado

| Escenario | `status` | Qué lo delata |
|---|---|---|
| Tótem ocioso, sin paciente | `ok` | — |
| Paciente puesto, midiendo | `ok` | — |
| Sonda de SpO₂ desenchufada | `degraded` | `disconnectedSensors: ["spo2"]` |
| Sonda de temperatura desenchufada | `degraded` | `disconnectedSensors: ["temperature"]` |
| Sonda de temperatura desenchufada, tótem sin esa sonda | `ok` | `sensors.temperature.expected: false` |
| Manguito flojo | `ok` | resultado clínico, no falla |
| Hilo lector muerto | `down` | `readerAlive: false` |
| Enlace mudo hace 30 s | `down` | `dataFresh: false` con `connected: true` |
| Pusher caído | `down` | `pusher.connected: false` |
| El Berry nunca enganchó | `down` | `lastFrameSecondsAgo: null` |

---

## 6. Certificados del tótem

Además de las cuatro preguntas, el health avisa si en el almacén de
certificados de Windows del tótem hay un certificado de la cadena del backend
**vencido o que vence en menos de 30 días**.

Viene de un incidente real, en septiembre de 2026 (ver
[`certificado_vencido.md`](certificado_vencido.md)): algunos tótems tenían
guardado el cruce viejo "ISRG Root X2" emitido por "ISRG Root X1", vencido
hacía un año, y dejaron de conectar el día que ese certificado pasó a decidir
la validación. Nadie lo sabía hasta que fallaron.

Qué se mira:

- Lo que Windows ofrece para validar servidores: almacenes `CA` y `ROOT`, con
  propósito de autenticación de servidor. Es lo mismo que carga Python.
- De eso, **sólo** los certificados con el sujeto de algún emisor de la cadena
  que manda el backend. Un almacén de Windows trae decenas de certificados
  vencidos que no tienen nada que ver (raíces viejas de Microsoft, de
  timestamping): avisar por ellos sería ruido permanente. La cadena se toma de
  una conexión real, así que si cambia la jerarquía de Let's Encrypt la
  revisión la sigue sola.

Cuándo: al arrancar y después una vez por día. Si la revisión falla (sin red,
backend caído) se reintenta a la hora. Corre en un hilo aparte y nunca frena ni
tumba el reporte. Cada revisión deja una línea `[CERT]` en el log.

**No cambia `status`.** Con truststore, un intermedio vencido en el almacén ya
no corta la conexión de berry-monitor: es un aviso para ir a limpiar el tótem
(`tools/reparar_ssl.ps1`), no una falla de operación. Una raíz por vencer sí
cortaría, y para eso está el margen de 30 días.

Fuera de Windows (desarrollo en WSL o Linux) o con una API `http://` no hay
nada que revisar: sale `checked: false` con el motivo en `error`.

---

## 7. Por qué es una tarea aparte

El reporte **no** viaja colgado del POST de métricas, por dos razones:

1. `send_data()` sólo corre con sesión activa, y el health importa sobre todo
   **cuando no hay ninguna**: un tótem con el Berry desenchufado de madrugada
   tiene que poder avisarlo.
2. Arranca **antes** del bucle de conexión, porque "el Berry no engancha" es uno
   de los estados a reportar. Si esperara a que el equipo conecte, ese caso
   nunca se informaría.

Nunca propaga excepciones: un backend caído o un error armando el objeto no
puede tumbar el monitoreo.

---

## 8. Configuración

| Clave | Default | Qué hace |
|---|---|---|
| `HEALTH_ENDPOINT` | `health` | Último tramo de la URL. Cuelga de la misma base y el mismo `TOTEM_ID` que las métricas. |
| `HEALTH_INTERVAL_SECONDS` | `60` | Cada cuánto se reporta. **`0` desactiva el reporte.** |
| `HEALTH_EXPECTED_SENSORS` | `spo2,temperature` | Sondas que este tótem lleva, separadas por coma. Sólo la desconexión de éstas mueve `status`. Vacío = ninguna. Ver §3.5. |

Las tres se cargan desde `berry-configure.exe`.

> `HEALTH_EXPECTED_SENSORS` ausente equivale a vigilar las dos: un tótem ya
> instalado no tiene la clave en su `credentials.json` y su comportamiento no
> cambia al actualizar. El default avisa de más antes que de menos.
>
> **Si el tótem mide la temperatura con el termómetro USB, poné `spo2` solo.**
> Con la sonda del Berry ausente, dejarlo en el default deja el health en
> `degraded` de forma permanente.

---

## 9. Dónde está cada cosa

| Archivo | Qué aporta |
|---|---|
| `src/health.py` | Arma el objeto y calcula `status`. |
| `src/cert_check.py` | Revisa los certificados del tótem contra la cadena del backend (§6). |
| `src/pm6750_protocol.py` | Tablas de estados y la clasificación `SENSOR_DESCONECTADO` / `SENSOR_SIN_PACIENTE`. |
| `src/data_parser.py` | Registra el estado de cada sensor en `sensor_status`, aparte del payload de métricas. |
| `src/serial_manager.py` · `src/bluetooth_manager.py` | `link_status()`, mismo contrato en los dos transportes. |
| `app.py` | `send_health()` (el loop) y `_health_snapshot()`. |

> El estado de los sensores lo registra **el parser**, no los callbacks de
> `app.py`. `register_callback()` pisa: hay un solo callback por evento, y en
> USB el de NIBP se lo queda `serial_manager` para controlar la medición, así
> que `handle_nibp` ni siquiera se registra. El parser ve todos los paquetes en
> los dos transportes.
