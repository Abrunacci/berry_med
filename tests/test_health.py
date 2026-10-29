"""El objeto que se postea a /health.

Acá vive la regla que decide si el tótem molesta a alguien o no: una sonda
desconectada del equipo es una falla —hay que ir a enchufarla— y un tótem sin
paciente puesto es el estado normal del 99 % del día. Confundirlas hace que el
health esté en rojo permanente, que es lo mismo que no tenerlo.
"""

import json

import pytest

from src.health import FRAME_FRESCO_SEG, SENSOR_FRESCO_SEG, HealthReporter
from tests.conftest import enlace, termometro
from tests.tramas import (
    ECG_ELECTRODO_SUELTO, ecg_params, estado_nibp, nibp_params, spo2_params,
    temp_params,
)


@pytest.fixture
def health(parser, reloj):
    return HealthReporter("totem-01", parser)


SANO = object()


def snapshot(health, pusher, device=None, sesion=None, thermometer=SANO):
    """Por defecto, un tótem sano: Berry enganchado y termómetro USB conectado."""
    return health.snapshot(
        device=device if device is not None else enlace(),
        pusher=pusher,
        session=sesion or {"active": False, "secondsElapsed": None},
        thermometer=termometro() if thermometer is SANO else thermometer,
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


def test_un_termometro_usb_desconectado_degrada(health, pusher_ok):
    foto = snapshot(health, pusher_ok, thermometer=termometro(conectado=False))
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
    parser.add_data(spo2_params(status=0x01))
    foto = snapshot(health, pusher_ok, thermometer=termometro(conectado=False))
    assert foto["disconnectedSensors"] == ["spo2", "temperature"]


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
        "disconnectedSensors", "expectedSensors", "sensorsWithoutPatient", "thermometer",
        "session", "certificates", "build",
    }


def test_el_uptime_cuenta_desde_que_arranco_el_reporter(health, reloj, pusher_ok):
    reloj.avanzar(125)
    assert snapshot(health, pusher_ok)["uptimeSeconds"] == 125.0


def test_la_sesion_activa_viaja_tal_cual(health, pusher_ok):
    sesion = {"active": True, "secondsElapsed": 61.5, "measurement": "spo2"}
    foto = snapshot(health, pusher_ok, sesion=sesion)
    assert foto["session"] == sesion


def test_sin_sensores_todavia_el_objeto_igual_es_valido(health, pusher_ok):
    """Los primeros segundos tras arrancar: todavía no llegó ningún paquete del
    Berry. La temperatura igual está: no depende del equipo."""
    foto = snapshot(health, pusher_ok)
    assert set(foto["sensors"]) == {"temperature"}
    assert foto["disconnectedSensors"] == []


def test_cada_sensor_publica_su_clasificacion(health, parser, pusher_ok):
    """El backend no tiene que conocer las tablas del protocolo: lee `kind`."""
    parser.add_data(spo2_params(status=0x01) + ecg_params(status=ECG_ELECTRODO_SUELTO))
    sensores = snapshot(health, pusher_ok)["sensors"]
    assert sensores["spo2"]["kind"] == "falla"
    assert sensores["temperature"]["kind"] == "ok"
    assert sensores["ecg"]["kind"] == "sin_paciente"


def test_la_marca_de_tiempo_interna_no_se_publica(health, parser, pusher_ok):
    """`_t` es monotonic del proceso: al backend no le dice nada y lo que sí le
    sirve —la antigüedad— ya va en `secondsAgo`."""
    parser.add_data(spo2_params())
    assert "_t" not in snapshot(health, pusher_ok)["sensors"]["spo2"]


# --- Sensores que este tótem no vigila ---------------------------------------

def foto_con(parser, pusher, esperados, estado_termometro=SANO):
    reporter = HealthReporter("totem-01", parser, sensores_esperados=esperados)
    return reporter.snapshot(
        device=enlace(), pusher=pusher,
        session={"active": False, "secondsElapsed": None},
        thermometer=termometro() if estado_termometro is SANO else estado_termometro)


def test_un_sensor_que_no_se_vigila_no_degrada(parser, reloj, pusher_ok):
    parser.add_data(spo2_params(status=0x01))
    foto = foto_con(parser, pusher_ok, {"temperature"})
    assert foto["status"] == "ok"
    assert foto["disconnectedSensors"] == []


def test_el_sensor_que_no_se_vigila_igual_se_informa(parser, reloj, pusher_ok):
    """No alertar no es lo mismo que ocultar: el estado sigue publicado, con
    `expected: false` al lado para que el backend sepa por qué no cuenta."""
    parser.add_data(spo2_params(status=0x01))
    sensor = foto_con(parser, pusher_ok, {"temperature"})["sensors"]["spo2"]
    assert sensor["status"] == "sensor_desconectado"
    assert sensor["kind"] == "falla"
    assert sensor["expected"] is False


def test_el_sensor_que_si_se_vigila_sigue_degradando(parser, reloj, pusher_ok):
    parser.add_data(spo2_params(status=0x01))
    foto = foto_con(parser, pusher_ok, {"spo2"}, termometro(conectado=False))
    assert foto["status"] == "degraded"
    assert foto["disconnectedSensors"] == ["spo2"]


def test_el_objeto_dice_que_sondas_se_estan_vigilando(parser, reloj, pusher_ok):
    """Para que el backend pueda distinguir 'no hay alerta' de 'no se está
    mirando'."""
    assert foto_con(parser, pusher_ok, {"spo2"})["expectedSensors"] == ["spo2"]


def test_sin_lista_se_vigilan_el_spo2_y_la_temperatura(health, pusher_ok):
    assert snapshot(health, pusher_ok)["expectedSensors"] == ["spo2", "temperature"]


def test_se_pueden_apagar_todas_las_alertas_de_sensor(parser, reloj, pusher_ok):
    parser.add_data(spo2_params(status=0x01))
    foto = foto_con(parser, pusher_ok, set(), termometro(conectado=False))
    assert foto["status"] == "ok"
    assert foto["expectedSensors"] == []


# --- Temperatura: el termómetro USB, no la sonda del Berry -----------------

def test_un_totem_con_termometro_usb_y_la_config_por_defecto_da_ok(parser, reloj, pusher_ok):
    """El caso que reportó el cliente el 28/10: el tótem mide con el termómetro
    USB, no tiene la sonda del Berry, y el equipo la informa suelta en cada
    paquete. Con la config por defecto (`spo2,temperature`), sin tocar nada al
    actualizar, tiene que dar ok."""
    parser.add_data(temp_params(status=0x01) + spo2_params(status=0x02))
    foto = HealthReporter("totem-01", parser).snapshot(
        device=enlace(), pusher=pusher_ok,
        session={"active": False, "secondsElapsed": None},
        thermometer=termometro())
    assert foto["status"] == "ok"
    assert foto["disconnectedSensors"] == []
    assert foto["sensors"]["temperature"]["status"] == "ok"


def test_la_sonda_del_berry_no_se_publica(health, parser, pusher_ok):
    """Ya no se usa: lo que se publica en `sensors.temperature` es el
    termómetro USB, aunque el equipo informe su sonda."""
    parser.add_data(temp_params(status=0x03))
    sensor = snapshot(health, pusher_ok)["sensors"]["temperature"]
    assert sensor["source"] == "usb_thermometer"
    assert sensor["status"] == "ok"


def test_la_temperatura_dice_de_donde_sale(health, pusher_ok):
    assert snapshot(health, pusher_ok)["sensors"]["temperature"] == {
        "status": "ok", "source": "usb_thermometer", "port": "COM5",
        "secondsAgo": 0.0, "stale": False, "kind": "ok", "expected": True}


def test_un_termometro_usb_que_no_abre_degrada(health, pusher_ok):
    """El caso que motivó vigilarlo: el termómetro mal asignado de puerto no
    llegaba al backend y el health no decía nada."""
    foto = snapshot(health, pusher_ok, thermometer=termometro(conectado=False))
    assert foto["status"] == "degraded"
    assert foto["disconnectedSensors"] == ["temperature"]
    sensor = foto["sensors"]["temperature"]
    assert sensor["status"] == "termometro_desconectado"
    assert sensor["kind"] == "falla"


def test_un_termometro_usb_conectado_sin_lecturas_esta_ok(health, pusher_ok):
    """El termómetro sólo transmite cuando alguien se mide: un tótem ocioso
    no tiene lecturas, y eso no es una falla."""
    foto = snapshot(health, pusher_ok, thermometer=termometro(edad=None))
    assert foto["status"] == "ok"


def test_un_termometro_que_no_se_vigila_se_informa_pero_no_degrada(parser, reloj, pusher_ok):
    foto = foto_con(parser, pusher_ok, {"spo2"}, termometro(conectado=False))
    assert foto["status"] == "ok"
    assert foto["sensors"]["temperature"]["expected"] is False
    assert foto["thermometer"]["connected"] is False
    assert foto["thermometer"]["expected"] is False


def test_sin_termometro_habilitado_y_vigilado_degrada(health, pusher_ok):
    """Todos los tótems que miden temperatura llevan el termómetro USB: si la
    config lo vigila y no está habilitado, alguien se olvidó de algo."""
    foto = snapshot(health, pusher_ok, thermometer=None)
    assert foto["status"] == "degraded"
    assert foto["disconnectedSensors"] == ["temperature"]
    sensor = foto["sensors"]["temperature"]
    assert sensor["status"] == "termometro_no_habilitado"
    assert sensor["kind"] == "falla"
    assert sensor["expected"] is True
    assert foto["thermometer"]["enabled"] is False


def test_sin_termometro_y_sin_vigilar_temperatura_da_ok(parser, reloj, pusher_ok):
    """El camino para un tótem que no mide temperatura: `spo2` solo."""
    foto = foto_con(parser, pusher_ok, {"spo2"}, None)
    assert foto["status"] == "ok"
    assert foto["sensors"]["temperature"]["status"] == "termometro_no_habilitado"


def test_el_termometro_desconectado_cuenta_una_sola_vez(health, pusher_ok):
    """El bloque `thermometer` es el detalle de `sensors.temperature`, no una
    segunda alerta."""
    foto = snapshot(health, pusher_ok, thermometer=termometro(conectado=False))
    assert foto["disconnectedSensors"] == ["temperature"]
    assert foto["thermometer"]["expected"] is True


def test_usb_thermometer_es_un_sinonimo_de_temperature(parser, reloj, pusher_ok):
    """Un tótem configurado con `spo2,usb_thermometer` tras la 1.0.10 sigue
    vigilando lo mismo, y se informa como `temperature`."""
    foto = foto_con(parser, pusher_ok, {"spo2", "usb_thermometer"},
                    termometro(conectado=False))
    assert foto["expectedSensors"] == ["spo2", "temperature"]
    assert foto["disconnectedSensors"] == ["temperature"]


def test_sin_termometro_el_bloque_lo_dice(health, pusher_ok):
    assert snapshot(health, pusher_ok, thermometer=None)["thermometer"] == {
        "enabled": False, "port": None, "connected": False, "expected": True}


# --- Certificados del tótem -------------------------------------------------

AVISO_X2_VENCIDO = {
    "subject": "ISRG Root X2", "issuer": "ISRG Root X1", "stores": ["CA"],
    "notAfter": "2025-09-15T16:00:00Z", "daysLeft": -361, "expired": True,
    "sha1": "151682F5218C0A511C28F4060A73B9CA78CE9A53",
}


def test_sin_revision_de_certificados_se_dice_que_no_se_miro(health, pusher_ok):
    """Los primeros segundos, antes de la primera revisión: el backend tiene
    que poder distinguir 'no hay avisos' de 'no se revisó'."""
    certs = snapshot(health, pusher_ok)["certificates"]
    assert certs["checked"] is False
    assert certs["warnings"] == []
    assert certs["error"]


def test_un_certificado_vencido_se_avisa_pero_no_cambia_el_status(health, reloj, pusher_ok):
    """El caso de SUBSUELO-11. Es un aviso para ir a limpiar el tótem, no una
    falla de operación: el estado sigue hablando de si el tótem puede operar."""
    revision = {"checked": True, "inspected": 2, "warnDays": 30,
                "warnings": [AVISO_X2_VENCIDO], "error": None, "_t": reloj()}
    reloj.avanzar(90)
    foto = health.snapshot(device=enlace(), pusher=pusher_ok,
                           session={"active": False, "secondsElapsed": None},
                           certificates=revision, thermometer=termometro())
    assert foto["status"] == "ok"
    assert foto["certificates"]["warnings"] == [AVISO_X2_VENCIDO]
    assert foto["certificates"]["checkedSecondsAgo"] == 90.0
    assert "_t" not in foto["certificates"]
    json.dumps(foto)


def test_una_revision_fallida_viaja_con_su_motivo(health, reloj, pusher_ok):
    revision = {"checked": False, "inspected": None, "warnDays": 30,
                "warnings": [], "error": "OSError: sin red", "_t": reloj()}
    foto = health.snapshot(device=enlace(), pusher=pusher_ok,
                           session={"active": False, "secondsElapsed": None},
                           certificates=revision, thermometer=termometro())
    assert foto["certificates"]["checked"] is False
    assert foto["certificates"]["error"] == "OSError: sin red"
    assert foto["status"] == "ok"


def test_el_build_del_exe_viaja_en_el_health(parser, reloj, pusher_ok):
    """Qué exe corre en el tótem, visible desde el backend sin ir al tótem."""
    build = {"name": "berry-monitor", "version": "1.0.9", "commit": "abc1234"}
    reporter = HealthReporter("totem-01", parser, build=build)
    foto = reporter.snapshot(device=enlace(), pusher=pusher_ok,
                             session={"active": False, "secondsElapsed": None},
                             thermometer=termometro())
    assert foto["build"] == build
    json.dumps(foto)
