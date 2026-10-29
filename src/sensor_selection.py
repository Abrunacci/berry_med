"""Qué sensores viajan en el payload de /metrics durante una sesión.

`start-monitoring` arranca la sesión con todos. Los eventos `start-sensor` y
`stop-sensor` permiten prender y apagar uno por uno, y una sesión también puede
arrancar con `start-sensor` directamente, con sólo ese sensor.

Es todo por software, a propósito: el PM6750 sigue transmitiendo todo como
hasta ahora, y lo que no está seleccionado se blanquea antes de postear. Apagar
los streams en el equipo dispararía el watchdog de silencio (relay) y dejaría al
/health sin ver una sonda desconectada, porque el estado de cada sonda sólo
viaja dentro de sus propios paquetes.

El payload mantiene siempre la misma forma: un sensor apagado no desaparece,
manda los mismos valores que manda cuando no hay medición. Así el backend no
tiene que distinguir "apagado" de "sin dato".
"""

import json

# Nombre del sensor en el evento -> qué partes del payload le pertenecen.
# `temperature` cubre las dos fuentes: la sonda del PM6750 y el termómetro USB,
# que terminan en el mismo `vitalSigns.temperature`.
SENSORS = ("ecg", "resp", "spo2", "temperature", "nibp")

_NO_VITAL = "- -"
_NO_PAIR = "- - /- -"   # mismo centinela que BMDataParser.NO_VALUE

_VITALS = {
    "ecg": {"heartRate": _NO_VITAL},
    "resp": {"respRate": _NO_VITAL},
    "spo2": {"spo2Pulse": _NO_PAIR},
    "temperature": {"temperature": _NO_VITAL},
    "nibp": {"nibp": _NO_PAIR},
}

ALL_SENSORS = frozenset(SENSORS)


def parse_sensor_event(event_data) -> tuple:
    """Sensores pedidos en un evento `start-sensor` / `stop-sensor`.

    Devuelve `(validos, desconocidos)`. pysher entrega el `data` del evento como
    texto, así que se acepta JSON o el nombre suelto:
      - `{"sensor": "spo2"}`
      - `{"sensors": ["spo2", "temperature"]}`
      - `"spo2"`
    Mayúsculas y espacios no importan.
    """
    data = event_data
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8", errors="replace")
    if isinstance(data, str):
        texto = data.strip()
        try:
            data = json.loads(texto) if texto else None
        except ValueError:
            data = texto

    pedidos = []
    if isinstance(data, dict):
        uno = data.get("sensor")
        varios = data.get("sensors")
        if uno is not None:
            pedidos.append(uno)
        if isinstance(varios, (list, tuple)):
            pedidos.extend(varios)
        elif varios is not None:
            pedidos.append(varios)
    elif isinstance(data, (list, tuple)):
        pedidos.extend(data)
    elif isinstance(data, str):
        pedidos.append(data)

    validos, desconocidos = set(), []
    for pedido in pedidos:
        nombre = str(pedido).strip().lower()
        if nombre in ALL_SENSORS:
            validos.add(nombre)
        else:
            desconocidos.append(pedido)
    return frozenset(validos), desconocidos


def filter_payload(data: dict, active) -> dict:
    """Blanquea en `data` lo que no está en `active`. Modifica y devuelve `data`.

    Se aplica después de pisar la temperatura con el termómetro USB, si no esa
    lectura se colaría con la temperatura apagada.
    """
    if not isinstance(data, dict):
        return data
    active = frozenset(active or ())
    if active >= ALL_SENSORS:
        return data

    vitals = data.get("vitalSigns")
    for sensor in ALL_SENSORS - active:
        if isinstance(vitals, dict):
            for clave, vacio in _VITALS[sensor].items():
                if clave in vitals:
                    vitals[clave] = vacio

    if "ecg" not in active:
        if isinstance(data.get("ecg"), dict):
            data["ecg"] = {lead: [] for lead in data["ecg"]}
        if "ecgPeaks" in data:
            data["ecgPeaks"] = []
        if isinstance(data.get("ecgInfo"), dict):
            data["ecgInfo"] = {clave: None for clave in data["ecgInfo"]}
    if "resp" not in active and "resp" in data:
        data["resp"] = []
    if "spo2" not in active and "spo2" in data:
        data["spo2"] = []
    return data
