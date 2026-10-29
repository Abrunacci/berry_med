"""Medición por instrumento: qué se publica y cómo se corta una medición.

El contrato con el backend es que el POST de métricas **no cambia de forma**:
con `start-<instrumento>` viajan las mismas claves, y lo de los otros
instrumentos va en su valor de "sin dato". Y que hay una sola medición a la vez:
un pedido nuevo corta la que estaba en curso.
"""

import pytest

from src.measurement import (
    COMANDOS, INSTRUMENTOS, MONITOREO, VITALES_SIN_DATO, SesionDeMedicion,
    filtrar_payload,
)
from tests.tramas import (
    ecg_onda, ecg_params, ecg_pico, nibp_params, resp_onda, spo2_onda,
    spo2_params,
)


def payload_lleno(parser):
    """Un POST con todos los instrumentos midiendo."""
    parser.add_data(
        ecg_onda() + ecg_pico() + ecg_params(hr=80, rr=16) + resp_onda()
        + spo2_onda() + spo2_params(spo2=97, pulso=72)
        + nibp_params(sys=120, dia=80)
    )
    data = parser.tomar_payload()
    data["vitalSigns"]["temperature"] = "36.5"   # la pone el termómetro USB
    return data


# --- Comandos ---------------------------------------------------------------

def test_hay_un_comando_por_instrumento():
    assert COMANDOS == {
        "start-ecg": "ecg",
        "start-spo2": "spo2",
        "start-nibp": "nibp",
        "start-temperature": "temperature",
    }


def test_cada_signo_vital_es_de_un_solo_instrumento(parser):
    """Si un campo quedara sin dueño, ningún `start-*` lo publicaría nunca; si
    tuviera dos, el filtro dependería del orden."""
    vitales = [v for i in INSTRUMENTOS.values() for v in i["vitalSigns"]]
    assert sorted(vitales) == sorted(parser.data["vitalSigns"])


def test_los_centinelas_son_los_del_parser(parser):
    """`_is_valid_data` de la app reconoce estos valores para no postear vacío:
    tienen que ser exactamente los que deja el parser."""
    assert VITALES_SIN_DATO == parser.data["vitalSigns"]


# --- Lo que se publica ------------------------------------------------------

def test_monitoring_publica_todo(parser):
    data = payload_lleno(parser)
    assert filtrar_payload(data, MONITOREO) == data


def test_sin_medicion_no_se_toca_nada(parser):
    data = payload_lleno(parser)
    assert filtrar_payload(data, None) == data


@pytest.mark.parametrize("instrumento", sorted(INSTRUMENTOS))
def test_el_mensaje_tiene_siempre_las_mismas_claves(parser, instrumento):
    data = payload_lleno(parser)
    filtrado = filtrar_payload(data, instrumento)
    assert set(filtrado) == set(data)
    assert set(filtrado["vitalSigns"]) == set(data["vitalSigns"])
    assert set(filtrado["ecg"]) == set(data["ecg"])
    assert set(filtrado["ecgInfo"]) == set(data["ecgInfo"])


def test_start_spo2_publica_solo_el_oximetro(parser):
    filtrado = filtrar_payload(payload_lleno(parser), "spo2")
    assert filtrado["vitalSigns"] == {
        "heartRate": "- -", "nibp": "- - /- -", "spo2Pulse": "97/72",
        "temperature": "- -", "respRate": "- -"}
    assert filtrado["spo2"]
    assert not any(filtrado["ecg"].values())
    assert filtrado["ecgPeaks"] == []
    assert filtrado["resp"] == []
    assert set(filtrado["ecgInfo"].values()) == {None}


def test_start_ecg_publica_ecg_y_respiracion(parser):
    filtrado = filtrar_payload(payload_lleno(parser), "ecg")
    assert filtrado["vitalSigns"]["heartRate"] == "80"
    assert filtrado["vitalSigns"]["respRate"] == "16"
    assert filtrado["vitalSigns"]["spo2Pulse"] == "- - /- -"
    assert filtrado["vitalSigns"]["nibp"] == "- - /- -"
    assert all(filtrado["ecg"].values())
    assert filtrado["ecgPeaks"]
    assert filtrado["resp"]
    assert filtrado["ecgInfo"]["hrv"] is not None
    assert filtrado["spo2"] == []


def test_start_nibp_publica_solo_la_presion(parser):
    filtrado = filtrar_payload(payload_lleno(parser), "nibp")
    assert filtrado["vitalSigns"]["nibp"] == "120/80"
    assert filtrado["vitalSigns"]["heartRate"] == "- -"
    assert filtrado["vitalSigns"]["temperature"] == "- -"
    assert filtrado["spo2"] == [] and filtrado["resp"] == []
    assert not any(filtrado["ecg"].values())


def test_start_temperature_publica_solo_la_temperatura(parser):
    filtrado = filtrar_payload(payload_lleno(parser), "temperature")
    assert filtrado["vitalSigns"] == {
        "heartRate": "- -", "nibp": "- - /- -", "spo2Pulse": "- - /- -",
        "temperature": "36.5", "respRate": "- -"}
    assert filtrado["spo2"] == [] and filtrado["ecgPeaks"] == []


def test_el_filtro_no_modifica_el_original(parser):
    data = payload_lleno(parser)
    filtrar_payload(data, "nibp")
    assert data["vitalSigns"]["spo2Pulse"] == "97/72"
    assert data["spo2"]


# --- La sesión: una medición a la vez ---------------------------------------

class Equipo:
    """Registra lo que la sesión le pide a la app, en orden."""

    def __init__(self):
        self.llamadas = []

    def sesion(self, **kw):
        return SesionDeMedicion(
            limpiar=lambda: self.llamadas.append("limpiar"),
            cortar_presion=lambda: self.llamadas.append("cortar_presion"),
            arrancar_presion=lambda: self.llamadas.append("arrancar_presion"),
            log=lambda _: None, **kw)


def test_arrancar_sin_nada_en_curso_limpia_y_no_corta(reloj):
    equipo = Equipo()
    sesion = equipo.sesion()
    sesion.arrancar("spo2")
    assert sesion.activa and sesion.medicion == "spo2"
    assert equipo.llamadas == ["limpiar"]


def test_un_pedido_nuevo_corta_el_anterior(reloj):
    """El oxímetro sigue midiendo y llega `start-nibp`: se corta el oxímetro,
    se limpia y recién después arranca la presión."""
    equipo = Equipo()
    sesion = equipo.sesion()
    sesion.arrancar("spo2")
    equipo.llamadas.clear()
    sesion.arrancar("nibp")
    assert sesion.medicion == "nibp"
    assert equipo.llamadas == ["cortar_presion", "limpiar", "arrancar_presion"]


def test_el_mismo_instrumento_reinicia_la_medicion(reloj):
    equipo = Equipo()
    sesion = equipo.sesion()
    sesion.arrancar("nibp")
    reloj.avanzar(40)
    equipo.llamadas.clear()
    sesion.arrancar("nibp")
    assert equipo.llamadas == ["cortar_presion", "limpiar", "arrancar_presion"]
    assert sesion.segundos() == 0.0


def test_start_monitoring_corta_una_medicion_por_instrumento(reloj):
    equipo = Equipo()
    sesion = equipo.sesion()
    sesion.arrancar("temperature")
    equipo.llamadas.clear()
    sesion.arrancar(MONITOREO)
    assert sesion.medicion == MONITOREO
    assert equipo.llamadas == ["cortar_presion", "limpiar"]


def test_solo_start_nibp_infla_el_manguito(reloj):
    for instrumento in ("ecg", "spo2", "temperature", MONITOREO):
        equipo = Equipo()
        equipo.sesion().arrancar(instrumento)
        assert "arrancar_presion" not in equipo.llamadas


def test_parar_corta_cualquier_medicion(reloj):
    equipo = Equipo()
    sesion = equipo.sesion()
    sesion.arrancar("ecg")
    equipo.llamadas.clear()
    sesion.parar("evento stop")
    assert not sesion.activa
    assert sesion.medicion is None and sesion.segundos() is None
    assert equipo.llamadas == ["limpiar"]


def test_el_tope_corta_una_medicion_por_instrumento(reloj):
    sesion = Equipo().sesion(max_seconds=30 * 60)
    sesion.arrancar("spo2")
    reloj.avanzar(30 * 60 - 1)
    assert not sesion.vencida()
    reloj.avanzar(1)
    assert sesion.vencida()


def test_sin_tope_no_vence_nunca(reloj):
    sesion = Equipo().sesion(max_seconds=0)
    sesion.arrancar("spo2")
    reloj.avanzar(10 ** 6)
    assert not sesion.vencida()


def test_un_paso_que_falla_no_impide_arrancar(reloj):
    """Best-effort: un equipo que no responde al STOP no puede dejar al tótem
    sin poder arrancar la medición nueva."""
    def roto():
        raise OSError("puerto cerrado")
    avisos = []
    sesion = SesionDeMedicion(limpiar=roto, cortar_presion=roto,
                              arrancar_presion=roto, log=avisos.append)
    sesion.arrancar("spo2")
    sesion.arrancar("nibp")
    assert sesion.medicion == "nibp"
    assert any("puerto cerrado" in a for a in avisos)
