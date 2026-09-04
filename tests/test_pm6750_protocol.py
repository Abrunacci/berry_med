"""Protocolo: comandos que salen y estados que entran.

Es la capa que no necesita ni equipo ni capturas —son tablas y aritmética— y
sin embargo es donde vive la decisión más delicada de la rama: cuál estado de
sensor es una falla y cuál es el tótem esperando un paciente.
"""

import pytest

from src.pm6750_protocol import (
    CMD_ECG, CMD_ECG_GAIN, CMD_ECG_MODE, CMD_NIBP, CMD_NIBP_MODE,
    CMD_NIBP_TARGET, CMD_RESP_GAIN, CMD_RESP_WAVE, CMD_SPO2, CMD_SPO2_WAVE,
    CMD_TEMP, SENSOR_DESCONECTADO, SENSOR_SIN_PACIENTE, build_command,
    build_nibp_commands, build_startup_commands, checksum, decode_ecg_status,
    decode_nibp_status, decode_spo2_status, decode_temp_status,
)


# --- Trama de comando -------------------------------------------------------

def test_comando_tiene_el_formato_del_manual():
    # Habilitar ECG: 55 AA N=04 A1=01 A2=01 SUM
    assert build_command(CMD_ECG, 1).hex() == "55aa040101f9"


def test_checksum_es_el_complemento_de_la_suma():
    # ~(N + A1 + A2) truncado a 8 bits. Con N=4, A1=1, A2=1 -> ~6 = 0xF9.
    assert checksum(4, bytes([0x01, 0x01])) == 0xF9


def test_checksum_no_se_desborda_del_byte():
    assert 0 <= checksum(0xFF, bytes([0xFF, 0xFF])) <= 0xFF


# --- Secuencia de arranque --------------------------------------------------

def test_arranque_configura_antes_de_abrir_los_streams():
    """Si los enables salieran primero, las primeras muestras vendrían con la
    configuración vieja del módulo."""
    cmds = build_startup_commands({"ecg_gain": "x2", "ecg_mode": "diagnostico"})
    claves = [a1 for a1, _ in cmds]
    assert claves.index(CMD_ECG_GAIN) < claves.index(CMD_ECG)
    assert claves.index(CMD_ECG_MODE) < claves.index(CMD_ECG)


def test_arranque_no_incluye_nibp():
    """0x02 infla el manguito de verdad: no puede salir al conectar, cuando no
    se sabe si el paciente lo tiene puesto."""
    assert CMD_NIBP not in [a1 for a1, _ in build_startup_commands()]


def test_arranque_abre_los_cinco_streams():
    claves = [a1 for a1, _ in build_startup_commands()]
    for cmd in (CMD_ECG, CMD_SPO2, CMD_TEMP, CMD_SPO2_WAVE, CMD_RESP_WAVE):
        assert cmd in claves


def test_arranque_traduce_la_config_a_los_bytes_del_manual():
    cmds = dict(build_startup_commands(
        {"ecg_gain": "x2", "ecg_mode": "diagnostico", "resp_gain": "x0.5"}
    ))
    assert cmds[CMD_ECG_GAIN] == 0x04
    assert cmds[CMD_ECG_MODE] == 0x03
    assert cmds[CMD_RESP_GAIN] == 0x02


def test_arranque_sin_config_usa_los_defaults_de_fabrica():
    """x1 / monitor: lo que el equipo ya venía usando, medido."""
    cmds = dict(build_startup_commands())
    assert cmds[CMD_ECG_GAIN] == 0x03      # x1
    assert cmds[CMD_ECG_MODE] == 0x02      # monitor


def test_una_config_invalida_avisa_y_no_rompe():
    avisos = []
    cmds = dict(build_startup_commands({"ecg_gain": "x999"}, log=avisos.append))
    assert cmds[CMD_ECG_GAIN] == 0x03      # cayó al default
    assert any("ECG_GAIN" in a for a in avisos)


# --- Medición de presión ----------------------------------------------------

def test_nibp_manda_el_arranque_al_final():
    """La configuración tiene que llegar antes que el 0x02, o el equipo mide
    con su valor anterior."""
    cmds = build_nibp_commands({"nibp_mode": "adulto",
                                "nibp_target_pressure_mmhg": 160})
    assert cmds[-1] == (CMD_NIBP, 0x01)
    assert cmds[0][0] == CMD_NIBP_MODE


def test_la_presion_objetivo_viaja_dividida_por_dos():
    """Manual pág. 7: 'the pressure = Set value / 2'."""
    cmds = dict(build_nibp_commands({"nibp_target_pressure_mmhg": 160}))
    assert cmds[CMD_NIBP_TARGET] == 80


@pytest.mark.parametrize("modo,fuera", [
    ("adulto", 320), ("nino", 250), ("neonato", 200), ("neonato", 30),
])
def test_una_presion_fuera_de_rango_se_ignora(modo, fuera):
    """Ignorarla deja que el equipo use su default; mandarla podría inflar de
    más sobre un neonato."""
    avisos = []
    cmds = dict(build_nibp_commands(
        {"nibp_mode": modo, "nibp_target_pressure_mmhg": fuera}, log=avisos.append
    ))
    assert CMD_NIBP_TARGET not in cmds
    assert avisos


def test_el_rango_de_presion_depende_del_modo():
    """210 es válido para un niño y no para un neonato."""
    nino = dict(build_nibp_commands(
        {"nibp_mode": "nino", "nibp_target_pressure_mmhg": 210}, log=lambda _: None))
    neonato = dict(build_nibp_commands(
        {"nibp_mode": "neonato", "nibp_target_pressure_mmhg": 210}, log=lambda _: None))
    assert nino[CMD_NIBP_TARGET] == 105
    assert CMD_NIBP_TARGET not in neonato


def test_una_presion_no_numerica_no_rompe_la_medicion():
    cmds = build_nibp_commands({"nibp_target_pressure_mmhg": "ochenta"},
                               log=lambda _: None)
    assert cmds[-1] == (CMD_NIBP, 0x01)


# --- Estado de los sensores -------------------------------------------------

def test_spo2_distingue_sonda_desconectada_de_paciente_ausente():
    """La distinción que decide si el /health alerta o no. Manual pág. 12:
    0x01 'sensor off' (la sonda no está en el equipo) contra 0x02 'no finger'
    (la sonda está puesta, no hay nadie)."""
    assert decode_spo2_status(0x01) == "sensor_desconectado"
    assert decode_spo2_status(0x02) == "sin_paciente"
    assert decode_spo2_status(0x01) in SENSOR_DESCONECTADO
    assert decode_spo2_status(0x02) in SENSOR_SIN_PACIENTE


def test_un_estado_de_spo2_desconocido_no_se_confunde_con_una_falla():
    """Un firmware con un código nuevo no puede caer en 'sonda desconectada' y
    disparar una alerta falsa."""
    desconocido = decode_spo2_status(0x7F)
    assert desconocido == "desconocido_0x7F"
    assert desconocido not in SENSOR_DESCONECTADO


@pytest.mark.parametrize("byte,esperado", [
    (0x00, "ok"),
    (0x01, "t1_desconectado"),
    (0x02, "t2_desconectado"),
    (0x03, "t1_y_t2_desconectados"),
])
def test_temperatura_decodifica_la_tabla_del_manual(byte, esperado):
    assert decode_temp_status(byte) == esperado


def test_las_sondas_de_temperatura_sueltas_son_falla():
    for byte in (0x01, 0x02, 0x03):
        assert decode_temp_status(byte) in SENSOR_DESCONECTADO


def test_nibp_lee_el_estado_en_los_bits_5_a_2():
    """El estado del test no es el byte entero: si se leyera así, 'midiendo'
    (0b0001 << 2 = 4) daría cualquier otra cosa."""
    assert decode_nibp_status(0b0001 << 2) == "midiendo"
    assert decode_nibp_status(0b0100 << 2) == "manguito_flojo"
    assert decode_nibp_status(0b0000) == "terminado"


def test_nibp_ignora_los_bits_que_no_son_del_test():
    """BIT0, BIT1 y BIT6~7 llevan otra cosa y no tienen que cambiar el estado."""
    con_ruido = (0b0001 << 2) | 0b11 | (1 << 6)
    assert decode_nibp_status(con_ruido) == "midiendo"


def test_ecg_informa_con_bits_y_el_electrodo_suelto_gana():
    """Si el electrodo no está puesto, que la señal sea débil es la
    consecuencia, no un segundo problema."""
    assert decode_ecg_status(0b00)["status"] == "ok"
    assert decode_ecg_status(0b01)["status"] == "senal_debil"
    assert decode_ecg_status(0b10)["status"] == "electrodo_suelto"
    ambos = decode_ecg_status(0b11)
    assert ambos["status"] == "electrodo_suelto"
    assert ambos["leadOff"] and ambos["weakSignal"]


def test_el_lead_off_del_ecg_no_es_una_falla_de_infraestructura():
    """Se detecta por impedancia entre electrodos: sin nadie conectado da
    positivo siempre, así que NO distingue un cable desenchufado de un tótem
    ocioso. Si entrara en SENSOR_DESCONECTADO, el /health alertaría todo el
    día."""
    assert decode_ecg_status(0b10)["status"] in SENSOR_SIN_PACIENTE
    assert decode_ecg_status(0b10)["status"] not in SENSOR_DESCONECTADO


def test_ningun_estado_es_falla_y_ausencia_a_la_vez():
    assert not (SENSOR_DESCONECTADO & SENSOR_SIN_PACIENTE)
