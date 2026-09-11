"""Estado del tótem para el endpoint `/health` del backend.

Responde tres preguntas, en este orden de importancia:

1. ¿El tótem está vivo?             -> que llegue el POST ya lo contesta
2. ¿El Berry está enganchado?       -> `device`
3. ¿Llegan las órdenes del backend? -> `pusher`
4. ¿Las sondas están conectadas?    -> `sensors` / `disconnectedSensors`

La regla de diseño es que **nada acá pregunta un flag: todo se mide**. El
antecedente es concreto: la app tenía un `connected` que quedaba en True para
siempre si el hilo lector se moría, así que un health basado en flags habría
informado "todo bien" sobre un cable muerto. Por eso `device` sale de
`link_status()` (puerto abierto + hilo vivo + antigüedad de la última trama) y
`pusher` de la conexión real de pysher.

Los sensores son otra cosa: no hay forma de consultarlos. El equipo informa su
estado en el byte [4] de cada paquete y nada más, así que el estado que se
publica es **el último que el equipo mandó**, con su antigüedad al lado. Si el
Berry está desenganchado, los sensores quedan viejos y `secondsAgo` lo dice; no
se inventa un "desconocido" que taparía el dato real.

Y la distinción que decide si un sensor es una alerta o un dato: **"la sonda no
está conectada al equipo" no es lo mismo que "no hay un paciente puesto"**. El
manual las separa explícitamente para el SpO2 —`0x01 sensor off` contra `0x02 no
finger`— y sólo la primera es una falla: alguien tiene que ir a enchufar algo.
La segunda es el estado normal de un tótem esperando al primer paciente, y
alertar por ella sería ruido permanente, todo el día.

El ECG cae entero del lado de "no hay paciente", aunque su nombre sugiera otra
cosa: el lead-off se detecta por impedancia entre electrodos, así que sin nadie
conectado da positivo siempre y **no distingue** un cable desenchufado del
equipo de un tótem ocioso. Por eso no puede usarse como falla.

El `manguito_flojo` del NIBP tampoco: es el resultado de una medición que salió
mal, no un estado permanente del equipo, y sólo aparece si alguien pidió una
medición.

La clasificación vive en `pm6750_protocol.SENSOR_DESCONECTADO` /
`SENSOR_SIN_PACIENTE` y se publica por sensor en el campo `kind`, para que el
backend no tenga que conocer las tablas del protocolo.

Y hay una segunda condición para que una falla cuente: que ese sensor se use en
ESTE tótem. No todos tienen la misma sonda —el de temperatura del Berry no se
conecta cuando la temperatura se mide con el termómetro USB IR aparte— y el
equipo informa su ausencia igual, de forma permanente. Sin la lista de sensores
esperados, un tótem así reporta `degraded` para siempre: el mismo ruido
permanente que ya nos había pasado con el "no finger" del SpO2, y por la misma
razón. La lista sale de `HEALTH_EXPECTED_SENSORS`.
"""

import time

from src.pm6750_protocol import SENSOR_DESCONECTADO, SENSOR_SIN_PACIENTE

# Antigüedad a partir de la cual una trama deja de considerarse fresca. El
# equipo transmite continuo (ECG a 250 Hz), así que con el enlace sano la
# antigüedad es de milisegundos; 5s ya es un enlace caído.
FRAME_FRESCO_SEG = 5.0

# Antigüedad a partir de la cual el estado de un sensor deja de ser informativo.
# Más generoso que el del enlace: la temperatura y la presión no llegan continuo.
SENSOR_FRESCO_SEG = 30.0

# Sensores cuya desconexión el equipo sabe informar. Es el máximo que se puede
# vigilar: del ECG y del NIBP no hay señal de "cable desenchufado" en el
# protocolo, así que no tiene sentido esperarlos. Ver docs/health.md.
SENSORES_VIGILABLES = ("spo2", "temperature")


class HealthReporter:
    """Junta el estado de las partes y arma el objeto que se postea.

    El estado de los sensores lo escribe el parser (`sensor_status`) desde el
    hilo lector, y `snapshot()` lo lee desde el event loop. No hace falta lock:
    el parser reemplaza cada entrada con una sola asignación de un dict nuevo,
    así que un lector nunca ve una entrada a medio escribir.
    """

    def __init__(self, totem_id: str, parser, sensores_esperados=None):
        self.totem_id = totem_id
        self.parser = parser
        # Qué sondas tiene puestas este tótem. Sin lista, se esperan todas las
        # que el equipo sabe reportar: es el comportamiento que ya había, y
        # falla del lado de avisar de más antes que de menos.
        self.sensores_esperados = frozenset(
            SENSORES_VIGILABLES if sensores_esperados is None else sensores_esperados
        )
        self._arranque = time.monotonic()

    # --- armado del objeto --------------------------------------------------

    def _sensores_publicos(self) -> dict:
        ahora = time.monotonic()
        salida = {}
        for nombre, datos in dict(self.parser.sensor_status).items():
            edad = round(ahora - datos["_t"], 1)
            salida[nombre] = {
                k: v for k, v in datos.items() if k != "_t"
            }
            salida[nombre]["secondsAgo"] = edad
            salida[nombre]["stale"] = edad > SENSOR_FRESCO_SEG
            salida[nombre]["kind"] = self._clasificar(salida[nombre]["status"])
            # Si este tótem no tiene esa sonda, su estado se publica igual pero
            # no puede mover el estado general. El backend lo lee de acá sin
            # tener que conocer la configuración del tótem.
            salida[nombre]["expected"] = nombre in self.sensores_esperados
        return salida

    @staticmethod
    def _clasificar(status: str) -> str:
        """Para qué sirve este estado. Es lo que separa una alerta de un dato.

            falla         la sonda no está conectada al equipo -> hay que ir
            sin_paciente  no hay nadie puesto -> con el tótem ocioso es lo normal
            ok            midiendo bien
            info          resultado de una medición de presión, o desconocido

        Sin esta separación el health alertaría todo el día: un tótem esperando
        al primer paciente reporta "no finger" en el SpO2 y lead-off en el ECG
        de forma permanente, que es su estado sano.
        """
        if status in SENSOR_DESCONECTADO:
            return "falla"
        if status in SENSOR_SIN_PACIENTE:
            return "sin_paciente"
        if status == "ok":
            return "ok"
        return "info"

    @staticmethod
    def _pusher_status(pusher) -> dict:
        """Estado real de la conexión de pysher.

        `state` es el string de la máquina de estados de pysher
        ("connected", "disconnected", "connecting", ...). Se publica crudo
        además del booleano porque distingue "todavía conectando" de "se cayó",
        que para diagnosticar no es lo mismo.
        """
        try:
            conn = pusher.connection
            estado = getattr(conn, "state", None)
            return {
                "connected": estado == "connected",
                "state": estado,
                "socketId": getattr(conn, "socket_id", None),
                "threadAlive": bool(conn.is_alive()) if hasattr(conn, "is_alive") else None,
            }
        except Exception as e:
            # El health nunca puede tumbar al que lo llama: si no se puede leer
            # el estado, eso mismo es información.
            return {"connected": False, "state": None, "socketId": None,
                    "error": f"{type(e).__name__}: {e}"}

    @staticmethod
    def _certificados_publicos(revision) -> dict:
        """Última revisión de certificados del tótem (ver `src/cert_check.py`).

        Va siempre y con la misma forma, para que el backend distinga "no hay
        avisos" de "no se revisó": con `checked: false`, el motivo va en
        `error`. Como con los sensores, la marca `_t` no se publica: se
        convierte en antigüedad.
        """
        if not revision:
            return {"checked": False, "checkedSecondsAgo": None, "inspected": None,
                    "warnDays": None, "warnings": [], "error": "todavía no se revisó"}
        salida = {k: v for k, v in revision.items() if k != "_t"}
        t = revision.get("_t")
        salida["checkedSecondsAgo"] = round(time.monotonic() - t, 1) if t is not None else None
        return salida

    def snapshot(self, device: dict, pusher, session: dict, certificates: dict = None) -> dict:
        sensores = self._sensores_publicos()
        pusher_st = self._pusher_status(pusher)

        edad = device.get("lastFrameSecondsAgo")
        device_ok = bool(device.get("connected")) and edad is not None and edad <= FRAME_FRESCO_SEG

        # Sólo las sondas desconectadas del equipo bajan el estado. "No hay
        # dedo" o "electrodo suelto" NO: con el tótem ocioso ése es el estado
        # normal y alertar por eso sería ruido permanente. Ver `_clasificar()`.
        #
        # Se exige `not stale` porque un estado viejo no prueba nada: si el
        # enlace se cayó, lo último que informó el equipo puede ser de hace
        # horas y ya no describe la realidad.
        desconectados = sorted(
            n for n, d in sensores.items()
            if d["kind"] == "falla" and not d["stale"] and d["expected"]
        )
        sin_paciente = sorted(
            n for n, d in sensores.items()
            if d["kind"] == "sin_paciente" and not d["stale"]
        )

        if not device_ok or not pusher_st["connected"]:
            estado = "down"
        elif desconectados:
            estado = "degraded"
        else:
            estado = "ok"

        return {
            "totemId": self.totem_id,
            "status": estado,
            "uptimeSeconds": round(time.monotonic() - self._arranque, 1),
            "device": dict(device, dataFresh=device_ok),
            "pusher": pusher_st,
            "sensors": sensores,
            # Sondas que este tótem usa y no están conectadas: esto sí es una
            # alerta. Una sonda que el tótem no usa no entra acá aunque el
            # equipo la reporte desconectada — ver `expected` en cada sensor.
            "disconnectedSensors": desconectados,
            # Qué sondas se vigilan en este tótem, para que el backend pueda
            # distinguir "no hay alerta" de "no se está mirando".
            "expectedSensors": sorted(self.sensores_esperados),
            # Sensores conectados pero sin nadie puesto. Va aparte y a título
            # informativo: es el estado normal de un tótem esperando paciente.
            "sensorsWithoutPatient": sin_paciente,
            "session": session,
            # Certificados del tótem vencidos o por vencer. Es un aviso: no
            # toca `status`, que habla de si el tótem puede operar.
            "certificates": self._certificados_publicos(certificates),
        }
