"""Los mismos tests, pero contra bytes que salieron del Berry de verdad.

Los otros archivos prueban qué hace el código ante un byte dado; éstos prueban
que el equipo manda los bytes que creemos. Son las dos mitades: las tramas
sintéticas están construidas con nuestra lectura del manual, así que si esa
lectura estuviera mal, no habría ningún test que se enterara.

Todo esto saltea si no hay capturas, así que el set corre igual en cualquier
máquina. Para grabarlas, con el Berry enchufado en Windows:

    python tools\\capturar_escenarios.py --puerto COM3
"""

from unittest.mock import patch

import pytest

from src.health import HealthReporter
from tests.conftest import enlace
from tests.escenarios import ESCENARIOS, POR_ID


class _LoopQuieto:
    """Un event loop cuyo reloj no avanza.

    `_parse_package()` le pide la hora al loop para decidir cuándo empieza una
    ventana nueva de onda, y `_roll_ecg_window()` vacía `data["ecg"]` cada vez
    que cambia el segundo. Alimentar una captura de 20s de golpe tarda
    milisegundos, pero si esos milisegundos cruzan un cambio de segundo, la
    ventana se vacía y quedan muchas menos muestras: el mismo `.bin` daba 184,
    537 o 665 muestras según cuándo se corriera el test.

    Con el reloj quieto no hay roll y la captura entera se acumula (hasta
    `max_waveform_points`), que es lo que estos tests necesitan para mirar la
    onda completa.
    """

    def time(self):
        return 0.0


def alimentar(parser, crudo):
    with patch("asyncio.get_event_loop", return_value=_LoopQuieto()):
        parser.add_data(bytearray(crudo))
    return parser


def contar_tramas(crudo):
    """Recorre el stream a mano y separa tramas buenas de checksums rotos.

    Se cuenta acá y no con el parser porque el parser descarta en silencio: si
    la mitad del stream tuviera el checksum mal, los tests seguirían pasando
    con los datos de la otra mitad y nadie se enteraría.
    """
    buenas = malas = 0
    i = 0
    n = len(crudo)
    while i + 3 < n:
        if crudo[i] != 0x55 or crudo[i + 1] != 0xAA:
            i += 1
            continue
        largo = crudo[i + 2]
        fin = i + largo + 2
        if fin > n:
            break
        paquete = crudo[i:fin]
        if (~sum(paquete[2:-1]) & 0xFF) == paquete[-1]:
            buenas += 1
        else:
            malas += 1
        i = fin
    return buenas, malas


# --- Cada escenario cumple lo que prometía ---------------------------------

@pytest.mark.parametrize("esc", ESCENARIOS, ids=lambda e: e.id)
def test_la_captura_muestra_lo_que_el_escenario_esperaba(esc, parser, captura):
    """Mismo predicado que usó el capturador para dar el OK en el momento."""
    alimentar(parser, captura(esc.id))
    problemas = esc.verificar(parser)
    assert not problemas, "; ".join(problemas)


@pytest.mark.parametrize("esc", ESCENARIOS, ids=lambda e: e.id)
def test_el_stream_del_equipo_viene_integro(esc, captura):
    """Un porcentaje alto de checksums rotos significaría que el framing o la
    captura están mal, no que el equipo manda basura."""
    buenas, malas = contar_tramas(captura(esc.id))
    assert buenas > 0
    assert malas / (buenas + malas) < 0.01


# --- El /health armado desde bytes reales ----------------------------------

def test_un_totem_ocioso_de_verdad_no_dispara_ninguna_alerta(parser, captura, pusher_ok):
    """El test que justifica toda esta máquina: bytes reales de un tótem con
    todo enchufado y nadie puesto, y el health en 'ok'. Es exactamente el caso
    que la primera versión reportaba como degraded todo el día."""
    alimentar(parser, captura("ocioso"))
    foto = HealthReporter("totem-01", parser).snapshot(
        device=enlace(), pusher=pusher_ok,
        session={"active": False, "secondsElapsed": None})
    assert foto["disconnectedSensors"] == []
    assert foto["status"] == "ok"


def test_una_sonda_realmente_desenchufada_degrada(parser, captura, pusher_ok):
    alimentar(parser, captura("spo2_sonda_desconectada"))
    foto = HealthReporter("totem-01", parser).snapshot(
        device=enlace(), pusher=pusher_ok,
        session={"active": False, "secondsElapsed": None})
    assert "spo2" in foto["disconnectedSensors"]
    assert foto["status"] == "degraded"


def test_el_codigo_de_temperatura_es_el_que_manda_este_equipo(parser, captura, manifiesto):
    """El manual define tres códigos de sonda de temperatura suelta y no dice
    cuál usa este equipo con una sola sonda conectada. La captura lo fija: si
    un firmware nuevo cambiara de código, este test se entera."""
    alimentar(parser, captura("temp_sonda_desconectada"))
    estado = parser.sensor_status["temperature"]["status"]
    grabado = manifiesto.get("temp_sonda_desconectada", {}).get("sensores", {})
    if grabado.get("temperature"):
        assert estado == grabado["temperature"]


# --- Que los datos que se postean salgan bien de un stream real ------------

def test_con_paciente_puesto_salen_signos_vitales(parser, captura):
    alimentar(parser, captura("normal"))
    vitales = parser.data["vitalSigns"]
    assert vitales["spo2Pulse"] != parser.NO_VALUE
    assert vitales["heartRate"] != "- -"


def test_con_paciente_puesto_llega_onda_de_ecg_en_las_siete_derivaciones(parser, captura):
    alimentar(parser, captura("normal"))
    ecg = parser.data["ecg"]
    assert all(ecg[lead] for lead in parser.ECG_LEADS)


SATURADO_BAJO = 2      # el equipo pega en 1 cuando el amplificador satura
SATURADO_ALTO = 248    # y en 249 del otro lado


def muestras_limpias(ecg, leads, n):
    """Índices donde ninguna derivación está pegada al tope del rango.

    Hace falta filtrar porque el amplificador satura: los valores recortados
    dejan de ser la suma de nada y ninguna identidad entre derivaciones se
    puede verificar sobre ellos. Ver docs/ecg.md §10.
    """
    return [i for i in range(n)
            if all(SATURADO_BAJO <= ecg[k][i] <= SATURADO_ALTO for k in leads)]


def test_las_derivaciones_reales_cumplen_einthoven(parser, captura):
    """II = I + III, la prueba de que el orden de las 7 derivaciones en el
    paquete es el que dice docs/ecg.md: si estuvieran corridas, la identidad no
    daría.

    Se mide sobre la DISPERSIÓN de `II - I - III`, no sobre su valor: las
    muestras traen la línea de base en 128 (medido: la constante da -127,5), y
    ese offset se cancela al restar. Si el orden es el correcto, la diferencia
    es constante y su desvío es del orden del error de cuantización.
    """
    alimentar(parser, captura("normal"))
    ecg = parser.data["ecg"]
    leads = ("I", "II", "III")
    n = min(len(ecg[k]) for k in leads)
    limpias = muestras_limpias(ecg, leads, n)
    assert len(limpias) >= 50, (
        f"sólo {len(limpias)} de {n} muestras sin saturar: no alcanzan para "
        f"verificar el orden de las derivaciones. Ver docs/ecg.md §10 — "
        f"bajar ECG_GAIN y regrabar 'normal'."
    )
    dif = [ecg["II"][i] - ecg["I"][i] - ecg["III"][i] for i in limpias]
    media = sum(dif) / len(dif)
    desvio = (sum((d - media) ** 2 for d in dif) / len(dif)) ** 0.5
    assert desvio < 3, (
        f"II - I - III varía demasiado (desvío {desvio:.1f}): las 7 "
        f"derivaciones no están en el orden que documenta docs/ecg.md"
    )


def test_las_derivaciones_aumentadas_cumplen_goldberger(parser, captura):
    """aVR = -(I+II)/2, aVL = (I-III)/2, aVF = (II+III)/2.

    Einthoven solo no alcanza para fijar el orden: sólo involucra a las tres
    primeras. Esto ata las otras tres, que es donde un corrimiento pasaría
    desapercibido.
    """
    alimentar(parser, captura("normal"))
    ecg = parser.data["ecg"]
    leads = ("I", "II", "III", "aVR", "aVL", "aVF")
    n = min(len(ecg[k]) for k in leads)
    limpias = muestras_limpias(ecg, leads, n)
    if len(limpias) < 50:
        pytest.skip(f"sólo {len(limpias)} muestras sin saturar")

    for nombre, formula in (
        ("aVR", lambda i: -(ecg["I"][i] + ecg["II"][i]) / 2),
        ("aVL", lambda i: (ecg["I"][i] - ecg["III"][i]) / 2),
        ("aVF", lambda i: (ecg["II"][i] + ecg["III"][i]) / 2),
    ):
        err = [ecg[nombre][i] - formula(i) for i in limpias]
        media = sum(err) / len(err)
        desvio = (sum((e - media) ** 2 for e in err) / len(err)) ** 0.5
        assert desvio < 3, (
            f"{nombre} no se deriva de I/II/III como debería "
            f"(desvío {desvio:.1f}): revisar el orden en docs/ecg.md"
        )


def test_queda_registrada_cuanto_satura_el_ecg(parser, captura):
    """No falla por saturar: falla si NO queda registro de cuánto.

    La saturación es un pendiente conocido de configuración (docs/ecg.md §10,
    ECG_GAIN) y no un bug del código, así que un test en rojo permanente sólo
    sería ruido. Lo que sí importa es que el número esté medido y a la vista
    cuando alguien vaya a mirar por qué la onda se ve recortada.
    """
    alimentar(parser, captura("normal"))
    ecg = parser.data["ecg"]
    n = min(len(ecg[k]) for k in parser.ECG_LEADS)
    limpias = len(muestras_limpias(ecg, parser.ECG_LEADS, n))
    print(f"\nECG en la captura 'normal': {limpias} de {n} muestras sin "
          f"saturar ({100 * limpias / n:.0f}%)")
    assert n > 0


def test_la_medicion_de_presion_deja_un_resultado(parser, captura):
    alimentar(parser, captura("nibp_medicion"))
    assert parser.data["vitalSigns"]["nibp"] != parser.NO_VALUE


def test_la_medicion_de_presion_pasa_por_el_estado_midiendo(parser, captura):
    """La captura tiene el ciclo entero, así que el estado 'midiendo' tiene que
    aparecer en algún momento del stream, no sólo el resultado final."""
    crudo = captura("nibp_medicion")
    from src.pm6750_protocol import decode_nibp_status
    vistos = set()
    i = 0
    while i + 3 < len(crudo):
        if crudo[i] != 0x55 or crudo[i + 1] != 0xAA:
            i += 1
            continue
        largo = crudo[i + 2]
        fin = i + largo + 2
        if fin > len(crudo):
            break
        if crudo[i + 3] == 0x03:
            vistos.add(decode_nibp_status(crudo[i + 4]))
        i = fin
    assert "midiendo" in vistos


# --- Metadata ---------------------------------------------------------------

def test_toda_captura_grabada_paso_su_verificacion(manifiesto):
    """El capturador deja grabar una captura con reparos si insistís. Esto lo
    deja anotado en vez de que pase inadvertido."""
    if not manifiesto:
        pytest.skip("todavía no hay capturas")
    con_reparos = [id_ for id_, m in manifiesto.items() if not m.get("verificacionOk")]
    assert not con_reparos, (
        f"capturas guardadas con reparos: {con_reparos}. "
        f"Volvé a grabarlas con: python tools/capturar_escenarios.py --solo <id>"
    )


def test_el_manifest_no_menciona_escenarios_que_ya_no_existen(manifiesto):
    if not manifiesto:
        pytest.skip("todavía no hay capturas")
    assert not (set(manifiesto) - set(POR_ID))
