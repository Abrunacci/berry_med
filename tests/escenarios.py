"""Qué se captura del equipo real, y cómo se sabe que salió bien.

Una sola lista, usada por los dos lados:

- `tools/capturar_escenarios.py` la recorre para guiar la sesión con el Berry
  en la mano, y usa `verificar()` para decir en el momento si la maniobra
  quedó bien grabada. Enterarse ahí es la diferencia entre repetir 30 segundos
  y volver a enchufar todo otro día.
- `tests/test_capturas.py` corre el mismo `verificar()` sobre el `.bin`
  guardado.

Que el predicado sea el mismo es a propósito: si el capturador lo dio por
bueno, el test no puede después decir otra cosa.
"""

from dataclasses import dataclass
from typing import Callable, List, Optional


@dataclass(frozen=True)
class Escenario:
    id: str
    titulo: str

    # De qué estado se parte, dicho entero y sin referencias al escenario
    # anterior. Va aparte de `maniobra` porque cada escenario tiene que poder
    # correrse solo (`--solo <id>`) y porque, encadenado, es el paso que se
    # olvida: la primera versión decía "desenchufá la sonda" y nunca "volvé a
    # enchufarla", así que dos capturas salieron con la sonda de SpO2 todavía
    # afuera del escenario anterior. Eso fue culpa de estas instrucciones, no
    # de quien las siguió.
    punto_de_partida: str

    maniobra: str          # el único cambio a hacer sobre ese estado
    segundos: int          # tope de grabación
    verificar: Callable    # (parser) -> lista de problemas; vacía = salió bien
    porque: str            # qué prueba esta captura que ninguna otra prueba

    # Comandos que el capturador le manda al equipo al empezar a grabar, con la
    # firma `(ser, cfg)`. Existe porque hay estados que no se provocan con las
    # manos: la medición de presión arranca con un comando, y no se puede pedir
    # desde la app porque el puerto serie es exclusivo — si la app lo tiene
    # abierto para escuchar a Pusher, el capturador no puede abrirlo.
    preparar: Optional[Callable] = None

    # Se ejecuta SIEMPRE al soltar el escenario, termine bien, falle o lo corten
    # con Ctrl+C. Para el NIBP es el stop que desinfla el manguito: hay un brazo
    # del otro lado.
    al_terminar: Optional[Callable] = None

    # Corte anticipado: `(parser) -> bool` sobre lo que va llegando. Sin esto la
    # medición de presión obligaría a esperar el tope entero después de haber
    # terminado.
    hasta: Optional[Callable] = None

    # Sensores que la maniobra toca. Todo lo demás tendría que estar sano, y si
    # no lo está es que quedó de la maniobra anterior. Pasó de verdad: dos
    # capturas se grabaron con la sonda de SpO2 todavía desenchufada del
    # escenario que venía antes, y como cada escenario sólo verificaba lo suyo,
    # nadie se enteró hasta mirar las cinco juntas.
    manipula: tuple = ()


# Estado del que parten casi todos los escenarios. Se escribe una vez y se
# repite, en vez de decir "como estaba antes": el que lo lee no tiene por qué
# acordarse de qué dejó el escenario anterior.
TODO_ENCHUFADO = ("Sonda de SpO2, cable de ECG y sonda de temperatura "
                  "enchufados al equipo")
CON_PACIENTE = (TODO_ENCHUFADO + ", con el dedo en la sonda y los electrodos "
                "pegados sobre alguien.")
SIN_PACIENTE = (TODO_ENCHUFADO + ", sin nadie puesto (sin dedo en la sonda y "
                "sin electrodos pegados).")


def _estado(parser, sensor):
    entrada = parser.sensor_status.get(sensor)
    return entrada["status"] if entrada else None


def _exige(parser, sensor, esperado) -> List[str]:
    """El sensor tiene que haber informado uno de los estados de `esperado`."""
    actual = _estado(parser, sensor)
    if actual is None:
        return [f"el equipo no mandó ni un paquete de {sensor}"]
    if actual not in esperado:
        return [f"{sensor} informó '{actual}' y se esperaba uno de {sorted(esperado)}"]
    return []


def advertencias(parser, esc) -> List[str]:
    """Todo lo que no invalida la captura pero conviene ver antes de guardarla."""
    return arrastre(parser, esc) + saturacion_ecg(parser, esc)


def saturacion_ecg(parser, esc) -> List[str]:
    """Avisa si el amplificador de ECG está recortando la onda.

    No invalida la captura —el orden de las derivaciones se puede verificar con
    las muestras que quedan— pero sí significa que la onda que el tótem postea
    está recortada. Es un pendiente conocido de configuración: docs/ecg.md §10
    recomienda bajar `ECG_GAIN`.
    """
    if "ecg" not in esc.manipula:
        return []
    ecg = parser.data["ecg"]
    n = min((len(ecg[k]) for k in ecg), default=0)
    if n < 100:
        return []
    limpias = sum(
        1 for i in range(n) if all(2 <= ecg[k][i] <= 248 for k in ecg)
    )
    if limpias >= n // 2:
        return []
    return [f"el ECG satura: sólo {limpias} de {n} muestras "
            f"({100 * limpias / n:.0f}%) caen dentro del rango. La onda se "
            f"postea recortada; ver docs/ecg.md §10 (ECG_GAIN)"]


def arrastre(parser, esc) -> List[str]:
    """Sensores en falla que este escenario no tenía por qué haber tocado.

    Es una advertencia y no un error: puede ser legítimo (un tótem que no lleva
    la sonda de temperatura la reporta desconectada siempre). Pero si no es
    legítimo, es una maniobra anterior sin revertir y la captura no describe lo
    que su nombre dice.
    """
    from src.pm6750_protocol import SENSOR_DESCONECTADO
    avisos = []
    for sensor, datos in parser.sensor_status.items():
        if sensor in esc.manipula:
            continue
        if datos["status"] in SENSOR_DESCONECTADO:
            avisos.append(
                f"{sensor} está en '{datos['status']}' y esta maniobra no lo "
                f"toca: el equipo no arrancó desde el punto de partida"
            )
    return avisos


def _hay_tramas(parser, minimo=50) -> List[str]:
    total = sum(len(v) for v in parser.data["ecg"].values()) + len(parser.data["spo2"])
    if total < minimo:
        return [f"llegaron muy pocas muestras ({total}): ¿el equipo está transmitiendo?"]
    return []


# --- Acciones sobre el equipo ----------------------------------------------
# Escriben al puerto con las MISMAS funciones que usa `serial_manager`, para que
# lo capturado sea lo que produce el código de producción y no una variante.

def _arrancar_nibp(ser, cfg) -> str:
    """Pide una medición de presión, igual que `_start_nibp_sync()`.

    Modo y presión objetivo viajan acá, inmediatamente antes del arranque: el
    manual (pág. 7) los pide así o el equipo usa su valor por defecto.
    """
    from src.pm6750_protocol import build_command, build_nibp_commands
    cmds = build_nibp_commands(cfg)
    for a1, a2 in cmds:
        ser.write(build_command(a1, a2))
    ser.flush()
    return f"medicion pedida ({len(cmds)} comandos)"


def _parar_nibp(ser, cfg) -> str:
    """Corta la medición. `0x02 00` es el stop del manual.

    Se manda siempre al terminar el escenario, haya terminado sola o no: si la
    grabación se corta con el manguito inflado, dejarlo así no es una opción.
    """
    from src.pm6750_protocol import CMD_NIBP, build_command
    ser.write(build_command(CMD_NIBP, 0x00))
    ser.flush()
    return "stop de NIBP enviado"


def _la_medicion_termino(parser) -> bool:
    """True cuando ya hay un resultado y el equipo dice que cerró.

    Se exigen las dos cosas. Sólo el estado no alcanza: 'terminado' es también
    el estado en reposo, así que el primer paquete que llegue antes de que el
    equipo procese el arranque cortaría la grabación a los dos segundos. El
    resultado tampoco alcanza solo: el parser no escribe la presión mientras
    infla —el equipo manda 0/0— pero el desinflado todavía es parte del ciclo
    que queremos tener grabado.
    """
    entrada = parser.sensor_status.get("nibp")
    if not entrada:
        return False
    hay_resultado = parser.data["vitalSigns"]["nibp"] != parser.NO_VALUE
    return hay_resultado and entrada["status"] == "terminado"


ESCENARIOS = (
    Escenario(
        id="normal",
        punto_de_partida=TODO_ENCHUFADO + ".",
        manipula=("spo2", "ecg", "temperature", "nibp"),
        titulo="Todo normal, con paciente",
        maniobra="Poné el dedo en la sonda de SpO2 y pegá los electrodos de "
                 "ECG sobre alguien. Esperá a ver el pulso estable y una onda "
                 "de ECG limpia antes de grabar.",
        segundos=20,
        verificar=lambda p: (
            _hay_tramas(p)
            + _exige(p, "spo2", {"ok", "buscando_pulso"})
            # 'senal_debil' NO alcanza acá, aunque en otros escenarios sea
            # aceptable: con los electrodos flojos la onda satura y deja de
            # cumplir las identidades entre derivaciones, que es justamente lo
            # que esta captura tiene que poder verificar.
            + _exige(p, "ecg", {"ok"})
        ),
        porque="Es la línea de base: sin esto no hay con qué comparar el resto, "
               "y es la única captura que verifica el orden de las 7 "
               "derivaciones contra el equipo real.",
    ),
    Escenario(
        id="ocioso",
        punto_de_partida=CON_PACIENTE,
        manipula=("spo2", "ecg"),
        titulo="Tótem ocioso (todo enchufado, nadie puesto)",
        maniobra="Sacá el dedo de la sonda y despegá los electrodos. NO "
                 "desenchufes ningún cable del equipo.",
        segundos=15,
        verificar=lambda p: (
            _hay_tramas(p)
            + _exige(p, "spo2", {"sin_paciente", "buscando_pulso",
                                 "busqueda_demasiado_larga"})
        ),
        porque="Es el estado del 99 % del día. La captura que prueba que el "
               "/health NO alerta acá — el bug que arregló 5c58820.",
    ),
    Escenario(
        id="spo2_sonda_desconectada",
        punto_de_partida=SIN_PACIENTE,
        manipula=("spo2",),
        titulo="Sonda de SpO2 desenchufada del equipo",
        maniobra="Desenchufá el cable de la sonda de SpO2 del equipo (del "
                 "equipo, no del dedo).",
        segundos=15,
        verificar=lambda p: _exige(p, "spo2", {"sensor_desconectado"}),
        porque="La única falla de sensor que el /health puede detectar de "
               "verdad, y la mitad de la distinción con 'ocioso'.",
    ),
    Escenario(
        id="ecg_electrodo_suelto",
        punto_de_partida=CON_PACIENTE,
        manipula=("ecg",),
        titulo="Electrodo de ECG despegado",
        maniobra="Despegá UN electrodo de ECG del paciente.",
        segundos=15,
        verificar=lambda p: _exige(p, "ecg", {"electrodo_suelto"}),
        porque="Confirma que el lead-off llega como bit y que el /health lo "
               "trata como 'sin paciente' y no como falla.",
    ),
    Escenario(
        id="temp_sonda_desconectada",
        punto_de_partida=SIN_PACIENTE,
        manipula=("temperature",),
        titulo="Sonda de temperatura desenchufada",
        maniobra="Desenchufá la sonda de temperatura del equipo.",
        segundos=15,
        verificar=lambda p: _exige(
            p, "temperature",
            {"t1_desconectado", "t2_desconectado", "t1_y_t2_desconectados"}),
        porque="El otro sensor cuya desconexión el equipo sí informa. Verifica "
               "cuál de los tres códigos manda este equipo en concreto.",
    ),
    Escenario(
        id="nibp_medicion",
        punto_de_partida=TODO_ENCHUFADO + ", y el manguito puesto en el brazo.",
        manipula=("nibp",),
        titulo="Medición de presión completa",
        maniobra="Ninguna: la medición la arranca esta herramienta al apretar "
                 "ENTER. No hace falta la app ni el evento de Pusher.",
        segundos=90,
        preparar=_arrancar_nibp,
        al_terminar=_parar_nibp,
        hasta=_la_medicion_termino,
        verificar=lambda p: (
            [] if p.sensor_status.get("nibp")
            else ["no llegó ningún paquete de NIBP: ¿arrancó la medición?"]
        ),
        porque="El único camino donde el equipo infla de verdad. Deja grabado "
               "el ciclo entero para no tener que volver a inflarle el brazo a "
               "nadie cada vez que se toca el parser.",
    ),
)

POR_ID = {e.id: e for e in ESCENARIOS}
