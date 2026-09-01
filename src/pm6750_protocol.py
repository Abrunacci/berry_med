"""Protocolo del monitor multiparamétrico PM6750 (BerryMed).

Única fuente de verdad para armar los comandos PC→módulo. La usan las dos vías
de conexión (`serial_manager` por USB/RS232 y `bluetooth_manager` por BLE) para
que no se desincronicen: antes cada una hacía la suya y la de Bluetooth no
mandaba ningún comando de configuración.

Referencia: manual técnico del PM6750, págs. 4-10 (`docs/manual_berry.pdf`).
Ver también `docs/protocolo_berry.md`.
"""

HEADER = b"\x55\xAA"

# --- Comandos PC→PM6750 (manual págs. 4-5) ----------------------------------
CMD_ECG         = 0x01   # 0 = sin salida, 1 = salida de datos de ECG
CMD_NIBP        = 0x02   # 0 = parar medición, 1 = arrancar medición
CMD_SPO2        = 0x03
CMD_TEMP        = 0x04
CMD_ECG_GAIN    = 0x07
CMD_ECG_MODE    = 0x08
CMD_NIBP_MODE   = 0x09
CMD_NIBP_TARGET = 0x0A
CMD_RESP_GAIN   = 0x0F
CMD_SW_VERSION  = 0xFC
CMD_HW_VERSION  = 0xFD
CMD_SPO2_WAVE   = 0xFE
CMD_RESP_WAVE   = 0xFF

# --- Traducción config → byte del protocolo ---------------------------------
ECG_GAIN_VALUES  = {"x0.25": 0x01, "x0.5": 0x02, "x1": 0x03, "x2": 0x04}
ECG_MODE_VALUES  = {"operacion": 0x01, "monitor": 0x02, "diagnostico": 0x03}
NIBP_MODE_VALUES = {"adulto": 0x01, "nino": 0x02, "neonato": 0x03}

# Rangos válidos de presión objetivo por modo, en mmHg (manual pág. 7).
NIBP_TARGET_RANGE = {"adulto": (40, 300), "nino": (40, 210), "neonato": (40, 140)}


def checksum(n: int, payload: bytes) -> int:
    """SUM = ~(N + A1 + A2 + ... + An), truncado a 8 bits (manual pág. 4)."""
    return ~(n + sum(payload)) & 0xFF


def build_command(a1: int, a2: int) -> bytes:
    """Arma una trama de comando `55 AA N A1 A2 SUM`."""
    payload = bytes([a1, a2])
    n = len(payload) + 2
    return HEADER + bytes([n]) + payload + bytes([checksum(n, payload)])


def _pick(table, value, default_key, label, log):
    """Busca `value` en `table`; si no está, avisa y cae al default."""
    key = str(value).lower() if value is not None else default_key
    if key in table:
        return key, table[key]
    log(f"[PM6750][WARN] {label} inválido: {value!r}; "
        f"válidos: {sorted(table)}. Se usa {default_key}.")
    return default_key, table[default_key]


def build_startup_commands(config=None, log=print):
    """Secuencia `(a1, a2)` a enviar al conectar.

    El módulo no transmite nada hasta recibir los comandos de habilitación.
    Primero se configura (ganancia, modo) y recién después se abren los streams,
    para que las muestras salgan ya con la configuración pedida.

    Los defaults coinciden con lo que el equipo venía usando de fábrica (medido:
    ganancia x1, modo monitor), así que no cambian el comportamiento: lo vuelven
    explícito y reproducible en vez de depender del estado interno del módulo.

    Nota histórica: la versión anterior mandaba `(0xFB, 1)` comentado como
    "ECG waves". **0xFB no existe en el protocolo** — el manual sólo define
    0xFC, 0xFD, 0xFE y 0xFF en ese rango. Era un no-op; la onda de ECG se
    habilita con 0x01, que ya se mandaba, y por eso el ECG andaba igual.
    """
    cfg = config or {}
    cmds = []

    # --- configuración (antes de abrir los streams) ---
    _, gain = _pick(ECG_GAIN_VALUES, cfg.get("ecg_gain"), "x1", "ECG_GAIN", log)
    cmds.append((CMD_ECG_GAIN, gain))

    _, mode = _pick(ECG_MODE_VALUES, cfg.get("ecg_mode"), "monitor", "ECG_MODE", log)
    cmds.append((CMD_ECG_MODE, mode))

    _, resp_gain = _pick(ECG_GAIN_VALUES, cfg.get("resp_gain"), "x1", "RESP_GAIN", log)
    cmds.append((CMD_RESP_GAIN, resp_gain))

    # --- habilitación de streams ---
    # 0x01 habilita los datos de ECG: la onda (0x01), los parámetros (0x02) y
    # las marcas de latido (0x30).
    cmds += [
        (CMD_ECG, 1),
        (CMD_SPO2, 1),
        (CMD_TEMP, 1),
        (CMD_SPO2_WAVE, 1),
        (CMD_RESP_WAVE, 1),
    ]
    # NADA de NIBP acá a propósito: al conectar no sabemos si el paciente tiene
    # puesto el manguito. Toda la configuración de presión va en
    # build_nibp_commands(), que se manda recién cuando se pide la medición.
    return cmds


def build_nibp_commands(config=None, log=print):
    """Secuencia `(a1, a2)` para arrancar una medición de presión, bajo demanda.

    Se manda cuando el usuario la pide (evento Pusher `start-blood-pressure`),
    nunca al conectar: no sabemos en qué momento tiene puesto el manguito, y
    `0x02` infla de verdad.

    La configuración (modo y presión objetivo) va junto con el arranque y no en
    la secuencia de conexión, porque el manual (pág. 7) pide setear la presión
    objetivo **inmediatamente antes** de cada medición: si no, el equipo usa su
    valor por defecto.
    """
    cfg = config or {}
    cmds = []

    nibp_name, nibp_mode = _pick(
        NIBP_MODE_VALUES, cfg.get("nibp_mode"), "adulto", "NIBP_MODE", log
    )
    cmds.append((CMD_NIBP_MODE, nibp_mode))

    # Presión objetivo: sólo si está configurada. El byte que viaja es mmHg/2
    # (manual pág. 7: "the pressure = Set value / 2").
    target = cfg.get("nibp_target_pressure_mmhg")
    if target not in (None, ""):
        try:
            target = int(target)
        except (TypeError, ValueError):
            log(f"[PM6750][WARN] NIBP_TARGET_PRESSURE_MMHG inválido: {target!r}; se ignora.")
        else:
            lo, hi = NIBP_TARGET_RANGE[nibp_name]
            if lo <= target <= hi:
                cmds.append((CMD_NIBP_TARGET, target // 2))
            else:
                log(f"[PM6750][WARN] NIBP_TARGET_PRESSURE_MMHG={target} fuera del "
                    f"rango {lo}-{hi} para modo {nibp_name}; se ignora.")

    # Y recién ahora, el arranque de la medición.
    cmds.append((CMD_NIBP, 0x01))
    return cmds


# --- Estado de los sensores (byte [4] de cada paquete) -----------------------
# El equipo informa en cada paquete si su sensor está puesto y midiendo. Es la
# única fuente que hay para saber que un electrodo se soltó o que no hay dedo en
# el clip: no existe ningún comando para consultarlo, sólo viaja acá.
#
# Los valores salen del manual (docs/manual_berry.pdf) y están tabulados en
# docs/protocolo_berry.md §6.

# ECG (0x02): bits, no un enumerado.
ECG_WEAK_SIGNAL_BIT = 0b01   # BIT0
ECG_LEAD_OFF_BIT    = 0b10   # BIT1

# SpO2 (0x04), temperatura (0x05): enumerados.
# Ojo con la diferencia, que el manual marca explícitamente y es la que decide
# si algo es una alerta o no: "sensor off" es la sonda desconectada del equipo,
# "no finger" es la sonda puesta y sin paciente.
SPO2_STATUS = {
    0x00: "ok",
    0x01: "sensor_desconectado",   # manual: "sensor off"
    0x02: "sin_paciente",          # manual: "no finger"
    0x03: "buscando_pulso",
    0x04: "busqueda_demasiado_larga",
}
TEMP_STATUS = {
    0x00: "ok",
    0x01: "t1_desconectado",       # manual: "T1 sensor off"
    0x02: "t2_desconectado",
    0x03: "t1_y_t2_desconectados",
}

# NIBP (0x03): el estado del test vive en BIT5~2, no en el byte entero.
NIBP_TEST_STATUS = {
    0b0000: "terminado",
    0b0001: "midiendo",
    0b0010: "detenido",
    0b0011: "presion_excedida",
    0b0100: "manguito_flojo",
    0b0101: "demoro_demasiado",
    0b0110: "error",
    0b0111: "interferencia",
    0b1000: "sin_resultado",
    0b1001: "inicializando",
    0b1010: "inicializacion_terminada",
}

# Estados que significan "el sensor no está puesto o no puede medir". Se separan
# de los de progreso (buscando_pulso, midiendo, inicializando) porque un tótem
# que está midiendo no tiene un problema: está trabajando.
# Falla de infraestructura: la sonda no está conectada al equipo. Alguien tiene
# que ir a enchufarla, y eso vale la pena saberlo aunque no haya paciente —
# mejor enterarse a las 3 AM que cuando llega el primero.
SENSOR_DESCONECTADO = {
    "sensor_desconectado",                                       # SpO2
    "t1_desconectado", "t2_desconectado", "t1_y_t2_desconectados",  # temperatura
}

# Estados que sólo dicen que no hay nadie puesto. Con el tótem ocioso son LO
# NORMAL y alertar por ellos sería ruido permanente:
#
# - `sin_paciente` (SpO2 "no finger") es el estado del 99 % del día.
# - `electrodo_suelto` es lo mismo para el ECG: el lead-off se detecta por
#   impedancia entre electrodos, así que sin nadie conectado da siempre
#   positivo. NO distingue "cable desenchufado del equipo" de "nadie puesto",
#   por eso no puede usarse como falla.
#
# Se informan igual en el objeto —sirven para saber si hay una medición en
# curso— pero no mueven el estado general.
SENSOR_SIN_PACIENTE = {
    "sin_paciente", "electrodo_suelto", "senal_debil",
    "buscando_pulso", "busqueda_demasiado_larga",
}


def decode_spo2_status(estado: int) -> str:
    return SPO2_STATUS.get(estado & 0xFF, f"desconocido_0x{estado:02X}")


def decode_temp_status(estado: int) -> str:
    return TEMP_STATUS.get(estado & 0xFF, f"desconocido_0x{estado:02X}")


def decode_nibp_status(estado: int) -> str:
    """Estado del test de presión: BIT5~2 del byte, no el byte entero."""
    test = (estado >> 2) & 0b1111
    return NIBP_TEST_STATUS.get(test, f"desconocido_0b{test:04b}")


def decode_ecg_status(estado: int) -> dict:
    """El ECG informa con bits, no con un enumerado, así que además de los dos
    booleanos se deriva un `status` con el mismo vocabulario que el resto de los
    sensores. Electrodo suelto gana sobre señal débil: si el electrodo no está
    puesto, que la señal sea débil es una consecuencia, no otro problema."""
    lead_off = bool(estado & ECG_LEAD_OFF_BIT)
    weak = bool(estado & ECG_WEAK_SIGNAL_BIT)
    return {
        "status": "electrodo_suelto" if lead_off else ("senal_debil" if weak else "ok"),
        "leadOff": lead_off,
        "weakSignal": weak,
    }
