"""El estado del termómetro USB que se publica en el /health.

No se abre un puerto real: se reemplaza `serial.Serial` por uno de mentira,
que es lo único que el lector usa del módulo.
"""

import threading

import pytest

from src import thermometer_reader
from src.thermometer_reader import ThermometerReader


class PuertoFalso:
    """Un `serial.Serial` que devuelve líneas hasta que se lo cierra."""

    def __init__(self, lineas):
        self.lineas = list(lineas)
        self.is_open = True
        self.listo = threading.Event()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.is_open = False

    def readline(self):
        if self.lineas:
            return self.lineas.pop(0)
        self.listo.set()
        return b""


def lector():
    return ThermometerReader("COM9", reconnect_seconds=0.01, status_callback=lambda _: None)


def test_un_puerto_que_no_abre_queda_desconectado_con_el_motivo(monkeypatch):
    """El caso del COM mal asignado: el lector reintenta para siempre, y el
    motivo es lo que permite verlo desde el backend."""
    intentos = threading.Event()

    def falla(*_a, **_k):
        intentos.set()
        raise OSError("could not open port 'COM9'")

    monkeypatch.setattr(thermometer_reader.serial, "Serial", falla)
    r = lector()
    r.start()
    try:
        assert intentos.wait(2)
        estado = r.link_status()
        assert estado["connected"] is False
        assert estado["portOpen"] is False
        assert "COM9" in estado["error"]
    finally:
        r.stop()


def test_un_puerto_abierto_esta_conectado_aunque_no_haya_lecturas(monkeypatch):
    """El termómetro sólo transmite cuando alguien se mide."""
    puerto = PuertoFalso([])
    monkeypatch.setattr(thermometer_reader.serial, "Serial", lambda *a, **k: puerto)
    r = lector()
    r.start()
    try:
        assert puerto.listo.wait(2)
        estado = r.link_status()
        assert estado["connected"] is True
        assert estado["lastReadingSecondsAgo"] is None
        assert estado["error"] is None
    finally:
        r.stop()


def test_una_lectura_valida_queda_registrada(monkeypatch):
    puerto = PuertoFalso([b'{"valor_temperatura_ir": 36.5, "unit": "c"}\n'])
    monkeypatch.setattr(thermometer_reader.serial, "Serial", lambda *a, **k: puerto)
    r = lector()
    r.start()
    try:
        assert puerto.listo.wait(2)
        assert r.link_status()["lastReadingSecondsAgo"] is not None
        assert r.get_latest() == {"value": 36.5, "unit": "C"}
    finally:
        r.stop()


def test_descartar_el_valor_no_borra_la_ultima_lectura(monkeypatch):
    """`reset_latest()` es de la sesión, no del enlace."""
    puerto = PuertoFalso([b'{"valor_temperatura_ir": 36.5, "unit": "C"}\n'])
    monkeypatch.setattr(thermometer_reader.serial, "Serial", lambda *a, **k: puerto)
    r = lector()
    r.start()
    try:
        assert puerto.listo.wait(2)
        r.reset_latest()
        assert r.get_latest() is None
        assert r.link_status()["lastReadingSecondsAgo"] is not None
    finally:
        r.stop()


def test_sin_arrancar_no_esta_conectado():
    estado = lector().link_status()
    assert estado["connected"] is False
    assert estado["readerAlive"] is False
