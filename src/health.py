"""Estado del tótem para el endpoint `/health` del backend.

Responde tres preguntas, en este orden de importancia:

1. ¿El tótem está vivo?            -> que llegue el POST ya lo contesta
2. ¿El Berry está enganchado?      -> `device`
3. ¿Llegan las órdenes del backend? -> `pusher`
4. ¿Los sensores están puestos?    -> `sensors`

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
"""

import time

from src.pm6750_protocol import SENSOR_DESCONECTADO

# Antigüedad a partir de la cual una trama deja de considerarse fresca. El
# equipo transmite continuo (ECG a 250 Hz), así que con el enlace sano la
# antigüedad es de milisegundos; 5s ya es un enlace caído.
FRAME_FRESCO_SEG = 5.0

# Antigüedad a partir de la cual el estado de un sensor deja de ser informativo.
# Más generoso que el del enlace: la temperatura y la presión no llegan continuo.
SENSOR_FRESCO_SEG = 30.0


class HealthReporter:
    """Junta el estado de las partes y arma el objeto que se postea.

    El estado de los sensores lo escribe el parser (`sensor_status`) desde el
    hilo lector, y `snapshot()` lo lee desde el event loop. No hace falta lock:
    el parser reemplaza cada entrada con una sola asignación de un dict nuevo,
    así que un lector nunca ve una entrada a medio escribir.
    """

    def __init__(self, totem_id: str, parser):
        self.totem_id = totem_id
        self.parser = parser
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
        return salida

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

    def snapshot(self, device: dict, pusher, session: dict) -> dict:
        sensores = self._sensores_publicos()
        pusher_st = self._pusher_status(pusher)

        edad = device.get("lastFrameSecondsAgo")
        device_ok = bool(device.get("connected")) and edad is not None and edad <= FRAME_FRESCO_SEG

        # Un sensor suelto no es una falla del tótem: es información clínica. Se
        # reporta aparte para que el backend pueda decidir qué hacer, sin que
        # baje el estado general a "down" y dispare una alarma de operación.
        sueltos = sorted(
            n for n, d in sensores.items()
            if d.get("status") in SENSOR_DESCONECTADO and not d["stale"]
        )

        if not device_ok or not pusher_st["connected"]:
            estado = "down"
        elif sueltos:
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
            "disconnectedSensors": sueltos,
            "session": session,
        }
