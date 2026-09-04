"""Parser: de bytes del cable a `data` y `sensor_status`.

`add_data()` es la única puerta de entrada y es una función pura de bytes a
estado: no toca el puerto ni la red. Por eso todo lo que sigue corre sin el
Berry enchufado.
"""

import pytest

from tests.tramas import (
    ECG_ELECTRODO_SUELTO, con_checksum_roto, ecg_onda, ecg_params, ecg_pico,
    estado_nibp, nibp_params, resp_onda, spo2_onda, spo2_params, temp_params,
    trama,
)


# --- Recorte de tramas ------------------------------------------------------

def test_parsea_una_trama_entera(parser):
    parser.add_data(spo2_params(spo2=97, pulso=72))
    assert parser.data["vitalSigns"]["spo2Pulse"] == "97/72"


def test_parsea_una_trama_partida_entre_dos_lecturas(parser):
    """`ser.read(256)` corta donde quiere: la mitad de una trama en una lectura
    y la otra mitad en la siguiente es el caso normal, no el raro."""
    t = spo2_params(spo2=95, pulso=70)
    parser.add_data(t[:4])
    assert parser.data["vitalSigns"]["spo2Pulse"] == parser.NO_VALUE
    parser.add_data(t[4:])
    assert parser.data["vitalSigns"]["spo2Pulse"] == "95/70"


def test_parsea_varias_tramas_de_una_sola_lectura(parser):
    parser.add_data(spo2_params(spo2=95, pulso=70) + temp_params(temp=36.7)
                    + ecg_params(hr=88, rr=14))
    vitales = parser.data["vitalSigns"]
    assert vitales["spo2Pulse"] == "95/70"
    assert vitales["temperature"] == "36.7"
    assert vitales["heartRate"] == "88"


def test_se_engancha_con_basura_por_delante(parser):
    """Es lo que pasa al conectar a un equipo que ya venía transmitiendo: se
    cae en mitad de una trama y hay que encontrar el 55AA siguiente."""
    parser.add_data(b"\x00\x11\x22\x55\x33" + spo2_params(spo2=93, pulso=68))
    assert parser.data["vitalSigns"]["spo2Pulse"] == "93/68"


def test_una_trama_con_checksum_roto_se_descarta(parser):
    parser.add_data(con_checksum_roto(spo2_params(spo2=99, pulso=80)))
    assert parser.data["vitalSigns"]["spo2Pulse"] == parser.NO_VALUE


def test_una_trama_rota_no_se_lleva_a_la_siguiente(parser):
    """Descartar la mala está bien; perder la buena que viene atrás, no."""
    parser.add_data(con_checksum_roto(spo2_params(spo2=99, pulso=80))
                    + spo2_params(spo2=94, pulso=71))
    assert parser.data["vitalSigns"]["spo2Pulse"] == "94/71"


def test_un_tipo_de_paquete_desconocido_no_rompe_el_stream(parser):
    parser.add_data(trama(0x7A, 1, 2, 3) + spo2_params(spo2=96, pulso=73))
    assert parser.data["vitalSigns"]["spo2Pulse"] == "96/73"


def test_el_buffer_no_crece_sin_limite_con_basura(parser):
    """Sin un 55AA a la vista el buffer se poda; si no, un cable con ruido lo
    haría crecer hasta comerse la memoria."""
    for _ in range(50):
        parser.add_data(b"\x00" * 200)
    assert len(parser.raw_buffer) <= 2


# --- Signos vitales ---------------------------------------------------------

def test_un_spo2_en_error_no_blanquea_un_pulso_bueno(parser):
    """127 es el código de error del SpO2 (manual pág. 12) y 255 el del pulso.
    Se validan por separado: antes un SpO2 malo borraba también el pulso."""
    parser.add_data(spo2_params(spo2=127, pulso=75))
    assert parser.data["vitalSigns"]["spo2Pulse"] == "-/75"


def test_un_pulso_en_error_no_blanquea_un_spo2_bueno(parser):
    parser.add_data(spo2_params(spo2=98, pulso=255))
    assert parser.data["vitalSigns"]["spo2Pulse"] == "98/-"


def test_con_los_dos_en_error_queda_el_centinela_de_sin_dato(parser):
    """El valor exacto importa: `app._is_valid_data` lo compara para no postear
    una sesión vacía."""
    parser.add_data(spo2_params(spo2=127, pulso=255))
    assert parser.data["vitalSigns"]["spo2Pulse"] == parser.NO_VALUE


def test_la_temperatura_llega_partida_en_entero_y_decima(parser):
    parser.add_data(trama(0x05, 0x00, 36, 8))
    assert parser.data["vitalSigns"]["temperature"] == "36.8"


def test_la_presion_no_se_pisa_mientras_el_equipo_infla(parser):
    """Mientras infla manda 0/0. Si eso pisara el valor, la medición anterior
    se borraría a mitad de camino; por eso el parser no escribe en cero."""
    parser.add_data(nibp_params(sys=120, dia=80))
    parser.add_data(nibp_params(status=estado_nibp(0b0001), sys=0, mean=0, dia=0))
    assert parser.data["vitalSigns"]["nibp"] == "120/80"


def test_reset_nibp_borra_solo_la_presion(parser):
    """La app lo llama al pedir una medición nueva: el SpO2 y la temperatura
    siguen llegando en vivo y no tienen nada que ver con el manguito."""
    parser.add_data(nibp_params(sys=120, dia=80) + spo2_params(spo2=97, pulso=72))
    parser.reset_nibp()
    assert parser.data["vitalSigns"]["nibp"] == parser.NO_VALUE
    assert parser.data["vitalSigns"]["spo2Pulse"] == "97/72"


# --- ECG --------------------------------------------------------------------

def test_el_paquete_de_onda_trae_siete_derivaciones_no_siete_muestras(parser):
    """El hallazgo de docs/ecg.md: los 7 bytes son I, II, III, aVR, aVL, aVF y
    V del MISMO instante. Leerlos como 7 muestras temporales de una sola
    derivación daría una onda inventada."""
    parser.add_data(ecg_onda(10, 20, 30, 40, 50, 60, 70))
    ecg = parser.data["ecg"]
    assert [ecg[l][-1] for l in parser.ECG_LEADS] == [10, 20, 30, 40, 50, 60, 70]


def test_cada_paquete_agrega_una_muestra_a_cada_derivacion(parser):
    for _ in range(5):
        parser.add_data(ecg_onda(1, 2, 3, 4, 5, 6, 7))
    assert all(len(parser.data["ecg"][l]) == 5 for l in parser.ECG_LEADS)


def test_la_ventana_se_vacia_al_tomar_el_payload(parser):
    """Y no antes: mientras nadie la consuma, sigue acumulando."""
    for _ in range(5):
        parser.add_data(ecg_onda(1, 2, 3, 4, 5, 6, 7))
    assert parser._ecg_window_len() == 5
    payload = parser.tomar_payload()
    assert len(payload["ecg"]["I"]) == 5
    assert parser._ecg_window_len() == 0


def test_lo_que_llega_despues_va_al_payload_siguiente(parser):
    """El corazón del arreglo: entre dos POST no se pierde nada.

    Antes las ondas se vaciaban por reloj y el POST sólo copiaba, así que lo
    acumulado entre el último borrado y el POST anterior se perdía: medido con
    reloj real, llegaba el 47 % de las muestras.
    """
    for _ in range(10):
        parser.add_data(ecg_onda(1, 1, 1, 1, 1, 1, 1))
    primero = parser.tomar_payload()
    for _ in range(7):
        parser.add_data(ecg_onda(2, 2, 2, 2, 2, 2, 2))
    segundo = parser.tomar_payload()

    assert len(primero["ecg"]["I"]) == 10
    assert len(segundo["ecg"]["I"]) == 7
    assert set(primero["ecg"]["I"]) == {1}
    assert set(segundo["ecg"]["I"]) == {2}


def test_no_se_pierde_ninguna_muestra_entre_tomas(parser):
    """Cuenta de punta a punta: todo lo que entra, sale exactamente una vez."""
    entradas = 0
    salidas = 0
    for ronda in range(20):
        for _ in range(13):
            parser.add_data(ecg_onda(ronda, 0, 0, 0, 0, 0, 0))
            entradas += 1
        salidas += len(parser.tomar_payload()["ecg"]["I"])
    salidas += len(parser.tomar_payload()["ecg"]["I"])
    assert salidas == entradas == 260


def test_los_picos_se_indexan_contra_la_onda_del_mismo_post(parser):
    """`sample` es la posición del latido DENTRO de la ventana actual: si se
    contara desde el arranque, la marca caería fuera de la onda que viaja."""
    for _ in range(10):
        parser.add_data(ecg_onda(1, 2, 3, 4, 5, 6, 7))
    parser.add_data(ecg_pico())
    pico = parser.data["ecgPeaks"][-1]
    assert pico["sample"] == 10
    assert pico["ms"] == pytest.approx(10 * 1000 / parser.ECG_SAMPLE_RATE_HZ, abs=0.1)


def test_los_picos_se_van_junto_con_la_onda(parser):
    """Comparten ventana: si sobrevivieran a la toma, sus índices apuntarían a
    muestras que ya viajaron en el payload anterior."""
    parser.add_data(ecg_onda() + ecg_pico())
    payload = parser.tomar_payload()
    assert payload["ecgPeaks"]
    assert parser.data["ecgPeaks"] == []

    parser.add_data(ecg_onda() + ecg_onda() + ecg_pico())
    assert parser.tomar_payload()["ecgPeaks"][0]["sample"] == 2


def test_tomar_el_payload_no_borra_los_signos_vitales(parser):
    """Las ondas son un flujo y se consumen; los vitales son estado y se
    siguen publicando aunque el equipo no mande uno nuevo."""
    parser.add_data(spo2_params(spo2=97, pulso=72) + ecg_onda())
    parser.tomar_payload()
    segundo = parser.tomar_payload()
    assert segundo["vitalSigns"]["spo2Pulse"] == "97/72"
    assert segundo["ecg"]["I"] == []


def test_el_payload_es_independiente_del_parser(parser):
    """Se postea desde otra tarea: si compartiera las estructuras, el lector
    seguiría escribiendo sobre el objeto que se está serializando."""
    parser.add_data(spo2_params(spo2=97, pulso=72) + ecg_onda(5, 5, 5, 5, 5, 5, 5))
    payload = parser.tomar_payload()
    payload["vitalSigns"]["spo2Pulse"] = "PISADO"
    payload["ecg"]["I"].append(999)
    parser.add_data(ecg_onda(6, 6, 6, 6, 6, 6, 6))
    assert parser.data["vitalSigns"]["spo2Pulse"] == "97/72"
    assert parser.data["ecg"]["I"] == [6]


def test_una_ventana_que_nadie_consume_no_crece_sin_limite(parser):
    """Sin sesión activa nadie postea. El tope evita que la memoria crezca
    mientras tanto."""
    parser.max_waveform_points = 10
    for _ in range(50):
        parser.add_data(ecg_onda())
    assert parser._ecg_window_len() == 10


def test_el_pico_se_guarda_aunque_nadie_haya_registrado_el_callback(parser):
    """El bug que arregló `_noop`: sin callback el paquete se descartaba entero
    y el latido nunca llegaba al payload."""
    assert parser.callbacks[0x30][1] is None
    parser.add_data(ecg_pico())
    assert len(parser.data["ecgPeaks"]) == 1


def test_ecg_info_decodifica_ganancia_modo_y_st(parser):
    # ganancia x1 (0b10 en BIT3~2), modo monitor (0b01 en BIT5~4)
    status = (0b01 << 4) | (0b10 << 2)
    parser.add_data(ecg_params(status=status, st=50, hrv=0x0B))
    info = parser.data["ecgInfo"]
    assert info["gain"] == "x1"
    assert info["mode"] == "monitor"
    assert info["st"] == 0.5
    assert info["hrv"] == "bradicardia"


def test_el_st_negativo_se_lee_como_signed(parser):
    """Es un signed char: leerlo sin signo daría +2,06 mV donde hay -0,50."""
    parser.add_data(ecg_params(st=-50))
    assert parser.data["ecgInfo"]["st"] == -0.5


# --- Estado de sensores (lo que consume el /health) -------------------------

def test_registra_el_estado_de_cada_sensor(parser):
    parser.add_data(
        spo2_params(status=0x01) + temp_params(status=0x02)
        + ecg_params(status=ECG_ELECTRODO_SUELTO)
        + nibp_params(status=estado_nibp(0b0100))
    )
    estados = {k: v["status"] for k, v in parser.sensor_status.items()}
    assert estados == {
        "spo2": "sensor_desconectado",
        "temperature": "t2_desconectado",
        "ecg": "electrodo_suelto",
        "nibp": "manguito_flojo",
    }


def test_el_estado_del_ecg_conserva_los_bits_crudos(parser):
    parser.add_data(ecg_params(status=ECG_ELECTRODO_SUELTO))
    assert parser.sensor_status["ecg"]["leadOff"] is True
    assert parser.sensor_status["ecg"]["weakSignal"] is False


def test_cada_paquete_pisa_el_estado_anterior_con_su_marca_de_tiempo(parser, reloj):
    parser.add_data(spo2_params(status=0x01))
    t0 = parser.sensor_status["spo2"]["_t"]
    reloj.avanzar(10)
    parser.add_data(spo2_params(status=0x00))
    assert parser.sensor_status["spo2"]["status"] == "ok"
    assert parser.sensor_status["spo2"]["_t"] == t0 + 10


def test_el_estado_de_los_sensores_no_viaja_en_el_payload_de_metricas(parser):
    """Va aparte de `self.data` a propósito: eso es el contrato del POST de
    métricas y agregarle campos lo cambiaría."""
    parser.add_data(spo2_params(status=0x01))
    assert "sensor_status" not in parser.data
    assert parser.sensor_status


def test_arrancar_una_sesion_no_borra_el_estado_de_los_sensores(parser):
    """Que un electrodo esté suelto no depende de que empiece una sesión, y el
    /health tiene que poder informarlo con el tótem ocioso."""
    parser.add_data(spo2_params(status=0x01, spo2=127, pulso=255))
    parser.reset_data()
    assert parser.sensor_status["spo2"]["status"] == "sensor_desconectado"
    assert parser.data["vitalSigns"]["spo2Pulse"] == parser.NO_VALUE


# --- Ondas -----------------------------------------------------------------

def test_las_ondas_de_spo2_y_respiracion_se_acumulan(parser):
    parser.add_data(spo2_onda(40) + spo2_onda(42) + resp_onda(30))
    assert parser.data["spo2"][-2:] == [40, 42]
    assert parser.data["resp"][-1] == 30
