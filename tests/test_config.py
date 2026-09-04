"""Lectura de la config: sólo lo que tiene lógica propia.

No se testea `get_config()` entero —lee un archivo de AppData y depende del
entorno— sino las funciones que transforman valores, que es donde puede haber
un error silencioso.
"""

from config import SECRETOS, parse_sensores_esperados, redactar


def sin_log(_):
    pass


# --- Sondas esperadas -------------------------------------------------------

def test_sin_la_clave_se_vigilan_todas():
    """Un tótem ya instalado no tiene la clave en su credentials.json, y su
    /health no puede cambiar de comportamiento por eso."""
    assert parse_sensores_esperados(None) == {"spo2", "temperature"}


def test_una_sola_sonda():
    assert parse_sensores_esperados("spo2") == {"spo2"}


def test_vacio_apaga_las_alertas_de_sensor():
    assert parse_sensores_esperados("") == set()


def test_tolera_espacios_y_mayusculas():
    """Lo escribe una persona en el configurador."""
    assert parse_sensores_esperados(" SpO2 , Temperature ") == {"spo2", "temperature"}


def test_una_sonda_que_no_se_puede_vigilar_avisa_y_se_ignora():
    """El ECG y el NIBP no tienen señal de 'cable desenchufado' en el
    protocolo: aceptarlos daría una vigilancia que nunca dispara."""
    avisos = []
    assert parse_sensores_esperados("spo2,ecg", log=avisos.append) == {"spo2"}
    assert avisos and "ecg" in avisos[0]


def test_una_lista_toda_invalida_no_rompe():
    assert parse_sensores_esperados("ecg,nibp", log=sin_log) == set()


# --- Redacción de secretos --------------------------------------------------

def test_la_contrasena_no_queda_escrita_en_el_log():
    """`app.py` imprime la config al arrancar y la salida se duplica a un
    archivo que sobrevive al proceso."""
    limpio = redactar({"api_password": "secreto", "totem_id": "totem12"})
    assert limpio["api_password"] == "***"
    assert limpio["totem_id"] == "totem12"


def test_se_tapan_todos_los_secretos():
    original = {k: "valor" for k in SECRETOS}
    assert set(redactar(original).values()) == {"***"}


def test_un_secreto_vacio_no_se_tapa():
    """Tapar un valor vacío escondería que la clave falta, que es justo lo que
    se quiere ver en el log al diagnosticar."""
    assert redactar({"api_password": ""})["api_password"] == ""


def test_redactar_no_toca_el_original():
    original = {"api_password": "secreto"}
    redactar(original)
    assert original["api_password"] == "secreto"
