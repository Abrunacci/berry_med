"""Lectura de la config: sólo lo que tiene lógica propia.

No se testea `get_config()` entero —lee un archivo de AppData y depende del
entorno— sino las funciones que transforman valores, que es donde puede haber
un error silencioso.
"""

from config import SECRETOS, parse_sensores_esperados, redactar


def sin_log(_):
    pass


# --- Sondas esperadas -------------------------------------------------------

def test_sin_la_clave_se_vigilan_el_spo2_y_la_temperatura():
    """Un tótem ya instalado no tiene la clave en su credentials.json. La
    temperatura es la del termómetro USB."""
    assert parse_sensores_esperados(None) == {"spo2", "temperature"}


def test_usb_thermometer_se_toma_como_temperature_y_avisa():
    """Es el nombre de la 1.0.10: un tótem configurado así tiene que seguir
    vigilando el termómetro sin tocar la config."""
    avisos = []
    assert parse_sensores_esperados("spo2,usb_thermometer", log=avisos.append) \
        == {"spo2", "temperature"}
    assert avisos and "usb_thermometer" in avisos[0]


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


def test_el_configurador_no_bloquea_la_config_por_defecto():
    """`spo2,temperature` con el termómetro apagado es lo que tiene un tótem
    recién instalado: no puede impedir guardar. El health lo informa."""
    from src.config_schema import validate
    errores = validate({"HEALTH_EXPECTED_SENSORS": "spo2,temperature",
                        "THERMOMETER_ENABLED": "false"})
    assert "HEALTH_EXPECTED_SENSORS" not in errores


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


# --- Eventos de Pusher por instrumento --------------------------------------

def test_sin_las_claves_los_eventos_por_instrumento_son_los_de_siempre():
    """Un tótem ya instalado no tiene las claves: escucha los nombres por
    defecto sin tocar la config."""
    from config import eventos_por_instrumento
    assert eventos_por_instrumento({}) == {
        "ecg": "start-ecg", "spo2": "start-spo2",
        "nibp": "start-nibp", "temperature": "start-temperature"}


def test_cada_evento_por_instrumento_se_puede_renombrar():
    from config import eventos_por_instrumento
    nombres = eventos_por_instrumento({"START_NIBP_EVENT_NAME": " tomar-presion ",
                                       "START_ECG_EVENT_NAME": ""})
    assert nombres["nibp"] == "tomar-presion"
    assert nombres["ecg"] == "start-ecg"


def test_el_configurador_ofrece_los_mismos_eventos_que_lee_la_app():
    """Las claves y los defaults están en `config.py` y en el esquema del
    configurador: si se separan, el asistente guardaría algo que la app no lee."""
    from config import EVENTOS_POR_INSTRUMENTO
    from src.config_schema import BY_KEY
    for clave, defecto in EVENTOS_POR_INSTRUMENTO.values():
        assert BY_KEY[clave].default == defecto
        assert BY_KEY[clave].section == "Pusher"


def test_el_configurador_no_deja_repetir_un_nombre_de_evento():
    from src.config_schema import validate
    errores = validate({"START_SPO2_EVENT_NAME": "start-monitoring"})
    assert "START_SPO2_EVENT_NAME" in errores


def test_el_configurador_no_deja_usar_el_nombre_de_la_presion_fija():
    from src.config_schema import validate
    errores = validate({"START_NIBP_EVENT_NAME": "start-blood-pressure"})
    assert "START_NIBP_EVENT_NAME" in errores


def test_los_nombres_por_defecto_no_chocan():
    from src.config_schema import EVENT_KEYS, validate
    errores = validate({})
    assert not any(clave in errores for clave in EVENT_KEYS)
