"""Constructor de tramas del PM6750, para tests sin equipo.

Arma los mismos bytes que manda el Berry. Existe para poder escribir el caso
que hace falta —un SpO2 con la sonda desconectada, un checksum roto, una trama
partida al medio— sin depender de haber logrado provocarlo con el equipo en la
mano.

No reemplaza a las capturas reales (`tests/capturas/`): éstas dicen qué hace el
código ante un byte dado, aquéllas dicen qué bytes manda el equipo de verdad.
Hacen falta las dos, y por eso el checksum se calcula acá con la fórmula del
manual en vez de importarla de `src`: si un día `pm6750_protocol.checksum()`
cambia, estos tests tienen que fallar, no seguirlo.
"""

HEADER = b"\x55\xAA"


def checksum(n: int, payload: bytes) -> int:
    """SUM = ~(N + A1 + ... + An) truncado a 8 bits (manual pág. 4)."""
    return ~(n + sum(payload)) & 0xFF


def trama(tipo: int, *datos: int) -> bytes:
    """`55 AA N <tipo> <datos...> <cs>`, con N y checksum bien calculados."""
    payload = bytes([tipo, *datos])
    n = len(payload) + 2
    return HEADER + bytes([n]) + payload + bytes([checksum(n, payload)])


def con_checksum_roto(t: bytes) -> bytes:
    """La misma trama con el último byte alterado."""
    return t[:-1] + bytes([(t[-1] + 1) & 0xFF])


# --- Paquetes equipo→PC (tipos según BMDataParser.callbacks) ----------------

def ecg_onda(*derivaciones: int) -> bytes:
    """0x01 — 7 derivaciones (I, II, III, aVR, aVL, aVF, V) del mismo instante."""
    muestras = list(derivaciones) or [0] * 7
    muestras += [0] * (7 - len(muestras))
    return trama(0x01, *muestras[:7])


def ecg_params(status: int = 0x00, hr: int = 75, rr: int = 16,
               st: int = 0, hrv: int = 0x01) -> bytes:
    """0x02 — estado del ECG, frecuencia cardíaca y respiratoria, ST y HRV."""
    return trama(0x02, status, hr, rr, st & 0xFF, hrv)


def nibp_params(status: int = 0x00, cuff: int = 0, sys: int = 120,
                mean: int = 93, dia: int = 80) -> bytes:
    """0x03 — presión. `status` lleva el estado del test en BIT5~2."""
    return trama(0x03, status, cuff, sys, mean, dia)


def spo2_params(status: int = 0x00, spo2: int = 98, pulso: int = 75) -> bytes:
    """0x04 — saturación y pulso."""
    return trama(0x04, status, spo2, pulso)


def temp_params(status: int = 0x00, temp: float = 36.5) -> bytes:
    """0x05 — temperatura. El equipo la manda partida: entero y décima."""
    entero = int(temp)
    decima = round((temp - entero) * 10)
    return trama(0x05, status, entero, decima)


def ecg_pico() -> bytes:
    """0x30 — marca de QRS (un paquete por latido detectado)."""
    return trama(0x30, 0x00)


def spo2_onda(valor: int = 40) -> bytes:
    """0xFE — punto de la onda pletismográfica."""
    return trama(0xFE, valor)


def resp_onda(valor: int = 30) -> bytes:
    """0xFF — punto de la onda de respiración."""
    return trama(0xFF, valor)


# --- Azúcar para armar el byte de estado ------------------------------------

def estado_nibp(test: int) -> int:
    """Byte de estado del NIBP con el código de test en BIT5~2."""
    return (test & 0b1111) << 2


ECG_OK              = 0b00
ECG_SENAL_DEBIL     = 0b01
ECG_ELECTRODO_SUELTO = 0b10
