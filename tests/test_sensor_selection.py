"""Selección de sensores por sesión (`start-sensor` / `stop-sensor`).

Lo que importa: un sensor apagado no puede colarse en el POST, y el payload no
cambia de forma, así que el backend lo lee igual que uno sin medición.
"""

import pytest

from src.data_parser import BMDataParser
from src.sensor_selection import ALL_SENSORS, filter_payload, parse_sensor_event


def payload_completo():
    """Un payload con todos los sensores midiendo."""
    return {
        "spo2": [10, 20, 30],
        "ecg": {lead: [1, 2] for lead in BMDataParser.ECG_LEADS},
        "ecgPeaks": [{"t": 0}],
        "ecgInfo": {"leadOff": False, "weakSignal": False, "gain": "x1",
                    "mode": "monitor", "st": 0.1, "hrv": "normal"},
        "resp": [5, 6],
        "vitalSigns": {
            "heartRate": 72,
            "nibp": "120/80",
            "spo2Pulse": "98/72",
            "temperature": "36.5",
            "respRate": 16,
        },
    }


# --- parse_sensor_event -----------------------------------------------------

@pytest.mark.parametrize("evento", [
    '{"sensor": "spo2"}',
    '{"sensors": ["spo2"]}',
    "spo2",
    " SpO2 ",
    b'{"sensor": "spo2"}',
    {"sensor": "spo2"},
])
def test_acepta_las_formas_documentadas(evento):
    assert parse_sensor_event(evento) == (frozenset({"spo2"}), [])


def test_acepta_varios_sensores():
    validos, _ = parse_sensor_event('{"sensors": ["spo2", "temperature"]}')
    assert validos == {"spo2", "temperature"}


def test_separa_los_desconocidos():
    validos, desconocidos = parse_sensor_event('{"sensors": ["spo2", "glucosa"]}')
    assert validos == {"spo2"}
    assert desconocidos == ["glucosa"]


@pytest.mark.parametrize("evento", [None, "", "{}", '{"otro": 1}', "glucosa"])
def test_sin_sensores_validos_devuelve_vacio(evento):
    assert parse_sensor_event(evento)[0] == frozenset()


# --- filter_payload ---------------------------------------------------------

def test_con_todos_activos_no_toca_nada():
    assert filter_payload(payload_completo(), ALL_SENSORS) == payload_completo()


def test_solo_spo2_blanquea_el_resto():
    data = filter_payload(payload_completo(), {"spo2"})
    assert data["spo2"] == [10, 20, 30]
    assert data["vitalSigns"] == {
        "heartRate": "- -",
        "nibp": BMDataParser.NO_VALUE,
        "spo2Pulse": "98/72",
        "temperature": "- -",
        "respRate": "- -",
    }
    assert data["ecg"] == {lead: [] for lead in BMDataParser.ECG_LEADS}
    assert data["ecgPeaks"] == []
    assert all(v is None for v in data["ecgInfo"].values())
    assert data["resp"] == []


def test_mantiene_la_forma_del_payload():
    original = payload_completo()
    data = filter_payload(payload_completo(), {"temperature"})
    assert data.keys() == original.keys()
    assert data["vitalSigns"].keys() == original["vitalSigns"].keys()
    assert data["ecg"].keys() == original["ecg"].keys()
    assert data["ecgInfo"].keys() == original["ecgInfo"].keys()


def test_resp_es_independiente_del_ecg():
    data = filter_payload(payload_completo(), {"resp"})
    assert data["resp"] == [5, 6]
    assert data["vitalSigns"]["respRate"] == 16
    assert data["vitalSigns"]["heartRate"] == "- -"
    assert not any(data["ecg"].values())


def test_temperatura_apagada_tapa_tambien_la_del_termometro_usb():
    # La app pisa vitalSigns.temperature con el termómetro USB antes de filtrar.
    data = payload_completo()
    data["vitalSigns"]["temperature"] = "37.2"
    assert filter_payload(data, {"spo2"})["vitalSigns"]["temperature"] == "- -"


def test_payload_del_parser_sin_datos_pasa_igual():
    data = BMDataParser().tomar_payload()
    assert filter_payload(data, {"nibp"})["vitalSigns"]["nibp"] == BMDataParser.NO_VALUE
