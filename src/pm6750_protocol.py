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
