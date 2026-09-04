"""El objeto que se postea a /health.

Acá vive la regla que decide si el tótem molesta a alguien o no: una sonda
desconectada del equipo es una falla —hay que ir a enchufarla— y un tótem sin
paciente puesto es el estado normal del 99 % del día. Confundirlas hace que el
health esté en rojo permanente, que es lo mismo que no tenerlo.
"""

import json

import pytest

from src.health import FRAME_FRESCO_SEG, SENSOR_FRESCO_SEG, HealthReporter
from tests.conftest import enlace
from tests.tramas import (
    ECG_ELECTRODO_SUELTO, ecg_params, estado_nibp, nibp_params, spo2_params,
    temp_params,
)


@pytest.fixture
def health(parser, reloj):
    return HealthReporter("totem-01", parser)


def snapshot(health, pusher, device=None, sesion=None):
    return health.snapshot(
        device=device if device is not None else enlace(),
        pusher=pusher,
        session=sesion or {"active": False, "secondsElapsed": None},
    )


# --- Estado general ---------------------------------------------------------

def test_todo_sano_reporta_ok(health, pusher_ok):
    assert snapshot(health, pusher_ok)["status"] == "ok"


def test_un_totem_ocioso_sin_paciente_sigue_estando_ok(health, parser, pusher_ok):
    """El caso que motivó `5c58820`. Esperando al primer paciente, el SpO2
    manda 'no finger' y el ECG lead-off de forma permanente. Si eso bajara el
    estado, el health estaría en degraded todo el día y nadie lo miraría."""
    parser.add_data(spo2_params(status=0x02) + ecg_params(status=ECG_ELECTRODO_SUELTO))
    foto = snapshot(health, pusher_ok)
    assert foto["status"] == "ok"
    assert foto["disconnectedSensors"] == []
    assert foto["sensorsWithoutPatient"] == ["ecg", "spo2"]


def test_una_sonda_desconectada_del_equipo_degrada(health, parser, pusher_ok):
    """0x01 'sensor off': la sonda no está en el equipo. Alguien tiene que ir."""
    parser.add_data(spo2_params(status=0x01))
    foto = snapshot(health, pusher_ok)
    assert foto["status"] == "degraded"
    assert foto["disconnectedSensors"] == ["spo2"]


def test_las_sondas_de_temperatura_sueltas_tambien_degradan(health, parser, pusher_ok):
    parser.add_data(temp_params(status=0x03))
    foto = snapshot(health, pusher_ok)
    assert foto["status"] == "degraded"
    assert foto["disconnectedSensors"] == ["temperature"]


def test_un_manguito_flojo_no_degrada(health, parser, pusher_ok):
    """Es el resultado de una medición que salió mal, no un estado permanente
    del equipo, y sólo aparece si alguien pidió una medición."""
    parser.add_data(nibp_params(status=estado_nibp(0b0100)))
    foto = snapshot(health, pusher_ok)
    assert foto["status"] == "ok"
    assert foto["sensors"]["nibp"]["kind"] == "info"


def test_varias_sondas_sueltas_salen_ordenadas(health, parser, pusher_ok):
    parser.add_data(spo2_params(status=0x01) + temp_params(status=0x01))
    assert snapshot(health, pusher_ok)["disconnectedSensors"] == ["spo2", "temperature"]


# --- El enlace con el Berry -------------------------------------------------

def test_sin_el_berry_enganchado_el_estado_es_down(health, pusher_ok):
    foto = snapshot(health, pusher_ok, device=enlace(conectado=False, edad=None))
    assert foto["status"] == "down"
    assert foto["device"]["dataFresh"] is False


def test_un_puerto_abierto_sin_datos_tambien_es_down(health, pusher_ok):
    """El caso que un flag `connected` no detecta: el puerto sigue abierto, el
    equipo está mudo. Es el cuelgue que ya vimos, y tiene que verse."""
    foto = snapshot(health, pusher_ok,
                    device=enlace(conectado=True, edad=FRAME_FRESCO_SEG + 1))
    assert foto["status"] == "down"
    assert foto["device"]["dataFresh"] is False


def test_un_enlace_que_nunca_recibio_nada_no_se_da_por_bueno(health, pusher_ok):
    """`lastFrameSecondsAgo: None` es 'todavía no llegó una sola trama'. Sin la
    comprobación explícita, comparar None contra el umbral reventaría o —peor—
    pasaría por bueno."""
    foto = snapshot(health, pusher_ok, device=enlace(conectado=True, edad=None))
    assert foto["status"] == "down"


def test_una_trama_reciente_mantiene_el_enlace_fresco(health, pusher_ok):
    foto = snapshot(health, pusher_ok,
                    device=enlace(edad=FRAME_FRESCO_SEG - 0.5))
    assert foto["device"]["dataFresh"] is True
    assert foto["status"] == "ok"


def test_el_enlace_caido_pesa_mas_que_una_sonda_suelta(health, parser, pusher_ok):
    """Con el Berry desenganchado, que además falte una sonda es un detalle:
    el estado general tiene que ser el peor de los dos."""
    parser.add_data(spo2_params(status=0x01))
    foto = snapshot(health, pusher_ok, device=enlace(conectado=False, edad=None))
    assert foto["status"] == "down"


# --- Pusher -----------------------------------------------------------------

def test_sin_pusher_no_llegan_las_ordenes_y_el_estado_es_down(health, pusher_caido):
    foto = snapshot(health, pusher_caido)
    assert foto["status"] == "down"
    assert foto["pusher"]["connected"] is False


def test_el_estado_crudo_de_pusher_se_publica(health, pusher_caido):
    """Distingue 'todavía conectando' de 'se cayó', que para diagnosticar no es
    lo mismo."""
    assert snapshot(health, pusher_caido)["pusher"]["state"] == "disconnected"


def test_un_pusher_que_revienta_al_consultarlo_no_tumba_el_health(health):
    class Roto:
        @property
        def connection(self):
            raise RuntimeError("boom")

    foto = snapshot(health, Roto())
    assert foto["status"] == "down"
    assert "boom" in foto["pusher"]["error"]


# --- Antigüedad de los estados de sensor ------------------------------------

def test_un_estado_viejo_no_dispara_una_alerta(health, parser, reloj, pusher_ok):
    """Si el enlace se cayó, lo último que informó el equipo puede ser de hace
    horas: ya no describe la realidad y no puede sostener una alerta."""
    parser.add_data(spo2_params(status=0x01))
    reloj.avanzar(SENSOR_FRESCO_SEG + 1)
    foto = snapshot(health, pusher_ok)
    assert foto["sensors"]["spo2"]["stale"] is True
    assert foto["disconnectedSensors"] == []
    assert foto["status"] == "ok"


def test_el_estado_viejo_igual_se_publica_con_su_antiguedad(health, parser, reloj, pusher_ok):
    """No se inventa un 'desconocido' que taparía el último dato real."""
    parser.add_data(spo2_params(status=0x01))
    reloj.avanzar(42)
    sensor = snapshot(health, pusher_ok)["sensors"]["spo2"]
    assert sensor["status"] == "sensor_desconectado"
    assert sensor["secondsAgo"] == 42.0


def test_un_estado_recien_llegado_no_esta_stale(health, parser, reloj, pusher_ok):
    parser.add_data(spo2_params(status=0x01))
    reloj.avanzar(SENSOR_FRESCO_SEG - 1)
    assert snapshot(health, pusher_ok)["sensors"]["spo2"]["stale"] is False


# --- Forma del objeto -------------------------------------------------------

def test_el_snapshot_es_serializable_a_json(health, parser, pusher_ok):
    """Viaja como `json=` en un POST: si algo no fuera serializable, el reporte
    fallaría en producción y no acá."""
    parser.add_data(spo2_params(status=0x01) + ecg_params() + temp_params())
    json.dumps(snapshot(health, pusher_ok))


def test_el_objeto_trae_todo_lo_que_el_backend_espera(health, pusher_ok):
    foto = snapshot(health, pusher_ok)
    assert set(foto) == {
        "totemId", "status", "uptimeSeconds", "device", "pusher", "sensors",
        "disconnectedSensors", "expectedSensors", "sensorsWithoutPatient",
        "session",
    }


def test_el_uptime_cuenta_desde_que_arranco_el_reporter(health, reloj, pusher_ok):
    reloj.avanzar(125)
    assert snapshot(health, pusher_ok)["uptimeSeconds"] == 125.0


def test_la_sesion_activa_viaja_tal_cual(health, pusher_ok):
    foto = snapshot(health, pusher_ok,
                    sesion={"active": True, "secondsElapsed": 61.5})
    assert foto["session"] == {"active": True, "secondsElapsed": 61.5}


def test_sin_sensores_todavia_el_objeto_igual_es_valido(health, pusher_ok):
    """Los primeros segundos tras arrancar: todavía no llegó ningún paquete."""
    foto = snapshot(health, pusher_ok)
    assert foto["sensors"] == {}
    assert foto["disconnectedSensors"] == []


def test_cada_sensor_publica_su_clasificacion(health, parser, pusher_ok):
    """El backend no tiene que conocer las tablas del protocolo: lee `kind`."""
    parser.add_data(spo2_params(status=0x01) + temp_params(status=0x00)
                    + ecg_params(status=ECG_ELECTRODO_SUELTO))
    sensores = snapshot(health, pusher_ok)["sensors"]
    assert sensores["spo2"]["kind"] == "falla"
    assert sensores["temperature"]["kind"] == "ok"
    assert sensores["ecg"]["kind"] == "sin_paciente"


def test_la_marca_de_tiempo_interna_no_se_publica(health, parser, pusher_ok):
    """`_t` es monotonic del proceso: al backend no le dice nada y lo que sí le
    sirve —la antigüedad— ya va en `secondsAgo`."""
    parser.add_data(spo2_params())
    assert "_t" not in snapshot(health, pusher_ok)["sensors"]["spo2"]


# --- Sondas que este tótem no tiene ----------------------------------------

def test_una_sonda_que_el_totem_no_usa_no_degrada(parser, reloj, pusher_ok):
    """El caso que apareció con las capturas reales: un tótem que mide la
    temperatura con el termómetro USB no lleva la sonda del Berry, y el equipo
    la informa desconectada en cada paquete, para siempre. Si eso degradara, el
    /health estaría en rojo el 100 % del tiempo."""
    parser.add_data(temp_params(status=0x01))
    reporter = HealthReporter("totem-01", parser, sensores_esperados={"spo2"})
    foto = reporter.snapshot(device=enlace(), pusher=pusher_ok,
                             session={"active": False, "secondsElapsed": None})
    assert foto["status"] == "ok"
    assert foto["disconnectedSensors"] == []


def test_la_sonda_que_no_se_vigila_igual_se_informa(parser, reloj, pusher_ok):
    """No alertar no es lo mismo que ocultar: el estado sigue publicado, con
    `expected: false` al lado para que el backend sepa por qué no cuenta."""
    parser.add_data(temp_params(status=0x01))
    reporter = HealthReporter("totem-01", parser, sensores_esperados={"spo2"})
    foto = reporter.snapshot(device=enlace(), pusher=pusher_ok,
                             session={"active": False, "secondsElapsed": None})
    sensor = foto["sensors"]["temperature"]
    assert sensor["status"] == "t1_desconectado"
    assert sensor["kind"] == "falla"
    assert sensor["expected"] is False


def test_la_sonda_que_si_se_vigila_sigue_degradando(parser, reloj, pusher_ok):
    parser.add_data(spo2_params(status=0x01) + temp_params(status=0x01))
    reporter = HealthReporter("totem-01", parser, sensores_esperados={"spo2"})
    foto = reporter.snapshot(device=enlace(), pusher=pusher_ok,
                             session={"active": False, "secondsElapsed": None})
    assert foto["status"] == "degraded"
    assert foto["disconnectedSensors"] == ["spo2"]


def test_el_objeto_dice_que_sondas_se_estan_vigilando(parser, reloj, pusher_ok):
    """Para que el backend pueda distinguir 'no hay alerta' de 'no se está
    mirando'."""
    reporter = HealthReporter("totem-01", parser, sensores_esperados={"spo2"})
    foto = reporter.snapshot(device=enlace(), pusher=pusher_ok,
                             session={"active": False, "secondsElapsed": None})
    assert foto["expectedSensors"] == ["spo2"]


def test_sin_lista_se_vigilan_todas_las_que_el_equipo_informa(health, pusher_ok):
    """El default avisa de más antes que de menos: para un tótem recién armado
    es mejor una alerta que sobra que una sonda floja que nadie ve."""
    assert set(snapshot(health, pusher_ok)["expectedSensors"]) == {"spo2", "temperature"}


def test_se_pueden_apagar_todas_las_alertas_de_sensor(parser, reloj, pusher_ok):
    parser.add_data(spo2_params(status=0x01) + temp_params(status=0x01))
    reporter = HealthReporter("totem-01", parser, sensores_esperados=set())
    foto = reporter.snapshot(device=enlace(), pusher=pusher_ok,
                             session={"active": False, "secondsElapsed": None})
    assert foto["status"] == "ok"
    assert foto["expectedSensors"] == []
