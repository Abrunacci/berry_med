"""Los handlers de Pusher de la app con la medición por instrumento.

`app.py` no se puede importar tal cual en un test: al cargarse lee la config
del tótem y duplica la salida al log. Se le cambian esas dos cosas antes de
importarlo, y la app se arma sin `__init__` (que conecta Pusher y el equipo),
con un lector falso que registra lo que se le pide.
"""

import asyncio
import importlib
import sys
import threading

import pytest

from src.data_parser import BMDataParser
from src.measurement import MONITOREO, SesionDeMedicion, comandos
from tests.tramas import nibp_params, spo2_onda, spo2_params


@pytest.fixture(scope="module")
def app_mod():
    import config
    import src.logging_setup as logging_setup
    mp = pytest.MonkeyPatch()
    mp.setattr(config, "get_config", lambda: {"ssl_cert_file": "certifi.pem"})
    mp.setattr(logging_setup, "setup", lambda *a, **k: None)
    mp.setenv("SSL_CERT_FILE", "certifi.pem")
    sys.modules.pop("app", None)
    try:
        yield importlib.import_module("app")
    finally:
        sys.modules.pop("app", None)
        mp.undo()


class LectorFalso:
    """Registra lo que la app le pide al equipo, en orden."""

    def __init__(self):
        self.llamadas = []

    def reset_state(self):
        self.llamadas.append("reset_state")

    async def start_nibp(self):
        self.llamadas.append("start_nibp")

    async def stop_nibp(self):
        self.llamadas.append("stop_nibp")


@pytest.fixture
def loop():
    """El loop principal de la app, corriendo en otro hilo como en producción:
    los handlers de Pusher llegan desde el hilo de pysher."""
    loop = asyncio.new_event_loop()
    hilo = threading.Thread(target=loop.run_forever, daemon=True)
    hilo.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    hilo.join(timeout=2)
    loop.close()


def armar(app_mod, loop, usb=True):
    app = app_mod.VitalsMonitor.__new__(app_mod.VitalsMonitor)
    app.data_parser = BMDataParser()
    app.monitor = LectorFalso()
    app.using_usb_monitor = usb
    app.thermometer_reader = None
    app.main_loop = loop
    app.max_session_seconds = 30 * 60
    app.sesion = SesionDeMedicion(
        limpiar=app._reset_session_state,
        cortar_presion=app._cortar_presion,
        arrancar_presion=app._arrancar_presion,
        max_seconds=app.max_session_seconds,
        log=lambda _: None,
    )
    return app


def test_start_monitoring_sigue_igual(app_mod, loop):
    app = armar(app_mod, loop)
    app.handle_start_event({})
    assert app.is_sending_data
    assert app.sesion.medicion == MONITOREO
    assert app.monitor.llamadas == ["reset_state"]


def test_stop_monitoring_corta_una_medicion_por_instrumento(app_mod, loop):
    app = armar(app_mod, loop)
    app.handle_instrument_event("spo2", {})
    app.handle_stop_event({})
    assert not app.is_sending_data
    assert app.sesion.medicion is None


def test_cada_comando_arranca_su_instrumento(app_mod, loop):
    for instrumento in ("ecg", "spo2", "temperature"):
        app = armar(app_mod, loop)
        app.handle_instrument_event(instrumento, {})
        assert app.sesion.medicion == instrumento
        assert "start_nibp" not in app.monitor.llamadas


def test_start_nibp_limpia_e_infla(app_mod, loop):
    app = armar(app_mod, loop)
    app.data_parser.add_data(nibp_params(sys=130, dia=85))
    app.handle_instrument_event("nibp", {})
    assert app.sesion.medicion == "nibp"
    assert app.monitor.llamadas == ["reset_state", "start_nibp"]
    assert app.data_parser.data["vitalSigns"]["nibp"] == BMDataParser.NO_VALUE


def test_un_pedido_durante_otra_medicion_la_corta_y_limpia(app_mod, loop):
    """El oxímetro sigue midiendo y llega `start-nibp`: lo medido por el
    oxímetro no puede viajar en la medición de presión."""
    app = armar(app_mod, loop)
    app.handle_instrument_event("spo2", {})
    app.data_parser.add_data(spo2_params(spo2=97, pulso=70) + spo2_onda())
    app.handle_instrument_event("nibp", {})
    assert app.sesion.medicion == "nibp"
    assert app.data_parser.data["vitalSigns"]["spo2Pulse"] == BMDataParser.NO_VALUE
    assert app.data_parser.data["spo2"] == []


def test_por_usb_el_corte_para_el_manguito_desde_reset_state(app_mod, loop):
    """`reset_state()` ya le manda STOP de NIBP al equipo: no se manda dos
    veces."""
    app = armar(app_mod, loop, usb=True)
    app.handle_instrument_event("nibp", {})
    app.handle_instrument_event("nibp", {})
    assert app.monitor.llamadas == [
        "reset_state", "start_nibp", "reset_state", "start_nibp"]


def test_por_bluetooth_el_corte_desinfla_antes_de_arrancar(app_mod, loop):
    app = armar(app_mod, loop, usb=False)
    app.handle_instrument_event("nibp", {})
    app.handle_instrument_event("spo2", {})
    assert app.monitor.llamadas == ["start_nibp", "stop_nibp"]


def test_por_bluetooth_sin_medicion_en_curso_no_se_manda_stop(app_mod, loop):
    """`start-monitoring` sin nada corriendo hace lo mismo que hoy."""
    app = armar(app_mod, loop, usb=False)
    app.handle_start_event({})
    assert app.monitor.llamadas == []


def test_start_blood_pressure_sigue_igual(app_mod, loop):
    """Infla sin cambiar qué se publica."""
    app = armar(app_mod, loop)
    app.handle_start_event({})
    app.handle_blood_pressure_event({})
    assert app.sesion.medicion == MONITOREO
    assert app.monitor.llamadas == ["reset_state", "start_nibp"]


def test_el_health_dice_que_se_esta_midiendo(app_mod, loop):
    app = armar(app_mod, loop)
    app.handle_instrument_event("temperature", {})

    capturado = {}

    class Health:
        def snapshot(self, **kw):
            capturado.update(kw)

    class Monitor:
        def link_status(self):
            return {}

    app.health = Health()
    app.monitor = Monitor()
    app.pusher_subscriber = None
    app._certificados = None
    app._health_snapshot()
    assert capturado["session"]["active"] is True
    assert capturado["session"]["measurement"] == "temperature"


def test_se_suscribe_con_los_nombres_de_la_config(app_mod, loop):
    """Igual que START_EVENT_NAME: el tótem escucha el nombre configurado."""
    app = armar(app_mod, loop)
    app.public_channel = "totem.01"
    app.start_event_name = "start-monitoring"
    app.stop_event_name = "stop-monitoring"
    app.instrument_events = comandos({"nibp": "tomar-presion"})
    registrados = {}

    class Canal:
        def bind(self, evento, callback):
            registrados[evento] = callback

    class Pusher:
        def subscribe(self, canal):
            return Canal()

    app.pusher_subscriber = Pusher()
    app.connect_handler({})
    assert "tomar-presion" in registrados and "start-nibp" not in registrados
    assert {"start-ecg", "start-spo2", "start-temperature"} <= set(registrados)
    registrados["tomar-presion"]({})
    assert app.sesion.medicion == "nibp"
