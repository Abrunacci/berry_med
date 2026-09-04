"""Fixtures compartidas.

Las dos que importan son `reloj` y `captura`:

- `reloj` congela `time.monotonic()`. Media docena de comportamientos dependen
  del tiempo —la antigüedad de la última trama, el `stale` de un sensor, la
  ventana de un segundo de la onda— y con el reloj real habría que dormir para
  probarlos, que es lento y además da tests que fallan uno de cada cien días.

- `captura` carga un `.bin` grabado del equipo real, y saltea el test si no
  está. Así el set corre entero en cualquier máquina sin el Berry, y se vuelve
  más estricto solo en la que tiene las capturas.
"""

import json
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

CAPTURAS = Path(__file__).resolve().parent / "capturas"


class Reloj:
    """`time.monotonic()` controlado a mano."""

    def __init__(self, t0: float = 1000.0):
        self.t = t0

    def avanzar(self, segundos: float) -> None:
        self.t += segundos

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def reloj(monkeypatch):
    r = Reloj()
    monkeypatch.setattr(time, "monotonic", r)
    return r


@pytest.fixture
def parser():
    from src.data_parser import BMDataParser
    return BMDataParser()


@pytest.fixture
def captura():
    """Devuelve los bytes de `tests/capturas/<nombre>.bin`, o saltea el test.

    El mensaje del skip dice cómo generarla, para que el que se lo encuentre no
    tenga que ir a buscar la herramienta.
    """
    def cargar(nombre: str) -> bytes:
        archivo = CAPTURAS / f"{nombre}.bin"
        if not archivo.exists():
            pytest.skip(
                f"falta la captura '{nombre}'. Se graba con el Berry conectado:\n"
                f"    python tools/capturar_escenarios.py --puerto COM3 --solo {nombre}"
            )
        return archivo.read_bytes()
    return cargar


@pytest.fixture
def manifiesto():
    """Metadata de las capturas (`manifest.json`), o `{}` si no hay ninguna."""
    archivo = CAPTURAS / "manifest.json"
    if not archivo.exists():
        return {}
    return json.loads(archivo.read_text(encoding="utf-8"))


class PusherFalso:
    """Lo mínimo que `HealthReporter._pusher_status()` le pide a pysher."""

    class _Conn:
        def __init__(self, state, vivo):
            self.state = state
            self.socket_id = "123.456"
            self._vivo = vivo

        def is_alive(self):
            return self._vivo

    def __init__(self, state="connected", vivo=True):
        self.connection = self._Conn(state, vivo)


@pytest.fixture
def pusher_ok():
    return PusherFalso()


@pytest.fixture
def pusher_caido():
    return PusherFalso(state="disconnected", vivo=False)


def enlace(conectado=True, edad=0.2, transporte="usb"):
    """Un `link_status()` como el que devuelven los dos managers."""
    return {
        "transport": transporte,
        "port": "COM3" if transporte == "usb" else "BerryMed",
        "connected": conectado,
        "portOpen": conectado,
        "readerAlive": conectado,
        "lastFrameSecondsAgo": edad,
    }
