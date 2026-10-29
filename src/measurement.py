"""Qué se está midiendo: todo el tótem o un solo instrumento.

Hasta ahora había una sola medición posible, `start-monitoring`, que publica
todo lo que manda el equipo. El backend necesita además poder pedir un solo
instrumento —la presión, el oxímetro— con su propio evento de Pusher, y que el
POST de métricas lleve sólo eso.

Lo que se publica sigue siendo **el mismo objeto, con las mismas claves**: los
campos de los otros instrumentos viajan en su valor de "sin dato". Así un
backend que hoy lee el POST no tiene que cambiar nada para leer uno de éstos.

El corte es sólo por software, a propósito. El Berry sigue transmitiendo todos
sus streams y acá se filtra lo que se publica. Apagar streams en el equipo
dispararía el watchdog de 20 s de `serial_manager` —que le corta la
alimentación con el relay— y dejaría al /health sin el estado de las sondas.

Y hay una sola medición a la vez: un pedido nuevo, del instrumento que sea,
corta la que estaba en curso y arranca la suya. `SesionDeMedicion` es ese único
camino de arranque y de cierre.
"""

import time
from typing import Callable, Optional

from src.data_parser import BMDataParser

# `start-monitoring`: todo el tótem, como siempre. No es un instrumento.
MONITOREO = "monitoring"

# Instrumento -> campos de `data` y de `data.vitalSigns` que le pertenecen.
#
# La respiración va con el ECG: el Berry la mide por impedancia con los mismos
# electrodos, y la frecuencia respiratoria llega en el mismo paquete (0x02) que
# la cardíaca. La temperatura es la del termómetro USB; la sonda del Berry ya no
# se usa (ver `src/health.py`).
INSTRUMENTOS = {
    "ecg": {"data": ("ecg", "ecgPeaks", "ecgInfo", "resp"),
            "vitalSigns": ("heartRate", "respRate")},
    "spo2": {"data": ("spo2",), "vitalSigns": ("spo2Pulse",)},
    "nibp": {"data": (), "vitalSigns": ("nibp",)},
    "temperature": {"data": (), "vitalSigns": ("temperature",)},
}

# Evento de Pusher -> instrumento. Nombres fijos, como `start-blood-pressure`:
# no se configuran.
COMANDOS = {f"start-{instrumento}": instrumento for instrumento in INSTRUMENTOS}

# "Sin dato" de cada signo vital: los mismos centinelas que usa el parser, que
# son los que reconoce `_is_valid_data` de la app para no postear vacío.
VITALES_SIN_DATO = {
    "heartRate": "- -",
    "nibp": BMDataParser.NO_VALUE,
    "spo2Pulse": BMDataParser.NO_VALUE,
    "temperature": "- -",
    "respRate": "- -",
}


def _sin_dato(campo: str):
    """Valor vacío de un campo de `data`, con el mismo tipo que el lleno."""
    if campo == "ecg":
        return {lead: [] for lead in BMDataParser.ECG_LEADS}
    if campo == "ecgInfo":
        return dict(BMDataParser.ECG_INFO_DEFAULT)
    return []


def filtrar_payload(data: dict, medicion: Optional[str]) -> dict:
    """Deja en `data` sólo lo del instrumento pedido; lo demás, en "sin dato".

    Con `MONITOREO` (o sin medición) devuelve `data` tal cual. No modifica el
    dict que recibe.
    """
    if medicion not in INSTRUMENTOS:
        return data

    propio = INSTRUMENTOS[medicion]
    salida = dict(data)
    for instrumento, campos in INSTRUMENTOS.items():
        if instrumento == medicion:
            continue
        for campo in campos["data"]:
            if campo in salida:
                salida[campo] = _sin_dato(campo)

    vitales = dict(data.get("vitalSigns") or {})
    for clave in vitales:
        if clave not in propio["vitalSigns"]:
            vitales[clave] = VITALES_SIN_DATO.get(clave, "- -")
    salida["vitalSigns"] = vitales
    return salida


def _no_op():
    return None


class SesionDeMedicion:
    """La medición en curso: cuál es, desde cuándo, y cómo se corta.

    Es el único camino de arranque y de cierre. Lo que toca el equipo lo pone la
    app, porque depende del transporte:

    - `limpiar`: borra todo lo medido (valores, ondas, última lectura del
      termómetro). Por USB, además, le manda STOP de NIBP al equipo.
    - `cortar_presion`: para un manguito que pudiera estar inflando. Se llama
      sólo si había una medición en curso.
    - `arrancar_presion`: pide una medición de presión al equipo.

    Los tres son best-effort: que falle uno no puede impedir el resto.
    """

    def __init__(self, limpiar: Callable[[], None],
                 cortar_presion: Callable[[], None] = _no_op,
                 arrancar_presion: Callable[[], None] = _no_op,
                 max_seconds: float = 0, log=print):
        self._limpiar = limpiar
        self._cortar_presion = cortar_presion
        self._arrancar_presion = arrancar_presion
        self.max_seconds = max_seconds
        self._log = log
        self.medicion: Optional[str] = None
        self.desde: Optional[float] = None  # monotonic() del arranque

    @property
    def activa(self) -> bool:
        return self.medicion is not None

    def segundos(self) -> Optional[float]:
        if self.desde is None:
            return None
        return round(time.monotonic() - self.desde, 1)

    def _intentar(self, paso: str, accion: Callable[[], None]) -> None:
        try:
            accion()
        except Exception as e:
            self._log(f"[MEDICION][ERROR] {paso}: {type(e).__name__}: {e}")

    def arrancar(self, medicion: str) -> None:
        """Arranca `medicion`, cortando la que hubiera en curso.

        Se limpia ANTES de habilitar el envío, para que el primer POST de la
        medición nueva no pueda llevarse nada de la anterior. Va acá y no sólo
        en el stop porque el stop puede no llegar nunca.
        """
        anterior = self.medicion
        if anterior is not None:
            self._log(f"[MEDICION] Se corta '{anterior}' "
                      f"({self.segundos()}s) para arrancar '{medicion}'")
            self._intentar("cortar la presión", self._cortar_presion)
        self._intentar("limpiar el estado", self._limpiar)
        self.desde = time.monotonic()
        self.medicion = medicion
        self._log(f"[MEDICION] Arranca '{medicion}'")
        if medicion == "nibp":
            self._intentar("arrancar la presión", self._arrancar_presion)

    def parar(self, motivo: str) -> None:
        """Corta el envío y deja todo limpio."""
        anterior = self.medicion
        self.medicion = None
        self.desde = None
        self._intentar("limpiar el estado", self._limpiar)
        self._log(f"[MEDICION] Termina '{anterior}' ({motivo})")

    def vencida(self) -> bool:
        """True si la medición superó el tope sin que llegara el stop."""
        if not self.max_seconds or self.desde is None:
            return False
        return (time.monotonic() - self.desde) >= self.max_seconds
