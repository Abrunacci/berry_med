import asyncio
import time                # ← añadido
from typing import Callable, Dict, Optional, Tuple


from src.pm6750_protocol import (
    decode_ecg_status,
    decode_nibp_status,
    decode_spo2_status,
    decode_temp_status,
)


def _noop(*args, **kwargs) -> None:
    """Callback vacío, para cuando nadie registró uno."""
    return None


class BMDataParser:
    PACKAGE_MIN_LENGTH = 4
    PACKAGE_HEADER = [0x55, 0xAA]

    # Centinela de "sin dato". Estaba repetido en cada dict de defaults; se
    # define acá porque `reset_nibp()` necesita el mismo valor exacto que
    # reconoce `_is_valid_data` en la app.
    NO_VALUE = "- - /- -"

    # Orden de las 7 derivaciones dentro del paquete 0x01 (ECG waves).
    # Confirmado con el manual técnico del PM6750 (docs/manual_berry.pdf, pág. 10:
    # "ECG waves 0x01 | A2=I | A3=II | A4=III | A5=aVR | A6=aVL | A7=aVF | A8=V")
    # y verificado sobre una captura real: se cumplen Einthoven/Goldberger
    # (II = I+III, aVR = -(I+II)/2, aVL = (I-III)/2, aVF = (II+III)/2) dentro
    # de ±2 LSB. Así que package[4]=I, package[5]=II, ..., package[10]=V.
    ECG_LEADS = ("I", "II", "III", "aVR", "aVL", "aVF", "V")

    # Frecuencia de muestreo de cada derivación. El manual dice 250 bps; medido
    # sobre captura real: 250,7. Se usa el nominal para convertir índice→ms.
    ECG_SAMPLE_RATE_HZ = 250.0

    # Códigos de arritmia del byte HRV (paquete 0x02, manual pág. 11).
    HRV_CODES = {
        0x00: "analizando",
        0x01: "normal",
        0x02: "paro",
        0x03: "fibrilacion_ventricular",
        0x04: "R_sobre_T",
        0x05: "extrasistoles_ventriculares_multiples",
        0x06: "extrasistoles_ventriculares_dobles",
        0x07: "extrasistole_ventricular",
        0x08: "bigeminismo",
        0x09: "trigeminismo",
        0x0A: "taquicardia",
        0x0B: "bradicardia",
        0x0C: "latido_perdido",
    }

    # Bits 3~2 y 5~4 del byte de estado del ECG (paquete 0x02, manual pág. 10).
    ECG_GAIN_MODES = {0b00: "x0.25", 0b01: "x0.5", 0b10: "x1", 0b11: "x2"}
    ECG_FILTER_MODES = {0b00: "operacion", 0b01: "monitor", 0b10: "diagnostico"}

    ECG_INFO_DEFAULT = {
        "leadOff": None,      # True = electrodo suelto
        "weakSignal": None,   # True = señal débil
        "gain": None,         # "x0.25" | "x0.5" | "x1" | "x2"
        "mode": None,         # "operacion" | "monitor" | "diagnostico"
        "st": None,           # desnivel del ST en mV
        "hrv": None,          # código de arritmia (ver HRV_CODES)
    }

    def __init__(self):
        self.raw_buffer = []
        self.callbacks: Dict[int, Tuple[str, Optional[Callable]]] = {
            0x01: ("on_ecg_waveform_received", None),
            0x02: ("on_ecg_params_received", None),
            0x03: ("on_nibp_params_received", None),
            0x04: ("on_spo2_params_received", None),
            0x05: ("on_temp_params_received", None),
            0x30: ("on_ecg_peak_received", None),
            0x31: ("on_spo2_peak_received", None),
            0xFE: ("on_spo2_waveform_received", None),
            0xFF: ("on_resp_waveform_received", None),
        }

        # Último estado informado por cada sensor del equipo, para el /health.
        # Aparte de `self.data` a propósito: eso es el payload de métricas.
        # No se limpia en `reset_data()` — el estado de un electrodo no depende
        # de que empiece o termine una sesión.
        self.sensor_status = {}

        # Data storage with default values
        self.data = {
            "spo2": [],   # SpO2 waveform
            # Las 7 derivaciones de ECG, cada una a ~251 muestras/seg.
            "ecg": {lead: [] for lead in self.ECG_LEADS},
            # Latidos (QRS) detectados dentro de la ventana actual.
            "ecgPeaks": [],
            # Estado/diagnóstico del ECG que viene en el paquete 0x02.
            "ecgInfo": dict(self.ECG_INFO_DEFAULT),
            "resp": [],   # Respiratory waveform
            "vitalSigns": {
                "heartRate": "- -",
                "nibp": self.NO_VALUE,
                "spo2Pulse": self.NO_VALUE,
                "temperature": "- -",
                "respRate": "- -",
            },
        }
        # Tope por ventana de 1 s. El ECG llega a ~1756 muestras/s (7 por
        # paquete × ~251 paquetes/s), así que 25 recortaba >98% de la señal.
        # Tope de una ventana sin que nadie la consuma: con el ECG a 250 Hz son
        # 10 segundos. Sólo se alcanza sin sesión activa, que es cuando nadie
        # postea; al arrancar una, `reset_data()` limpia todo.
        self.max_waveform_points = 2500
        # Tope de picos por ventana de 1 s. A 250 lpm serían ~4; 100 es un
        # techo defensivo para que un stream corrupto no infle el payload.
        self.max_peaks_per_window = 100
        self.last_update_time = 0  # (compat) — ver _wave_last
        # Timestamp de último reset por onda, independiente entre sí. Antes se
        # compartía last_update_time y ECG/RESP se reseteaban de más.
        self._wave_last = {"ecg": 0, "spo2": 0, "resp": 0}

    # ---------- API ---------------------------------------------------------
    # def register_callback(self, name: str, callback: Callable) -> None:
    #     for key, (callback_name, _) in self.callbacks.items():
    #         if callback_name == name:
    #             self.callbacks[key] = (callback_name, callback)
    #             break
    def _record_sensor(self, sensor: str, status) -> None:
        """Guarda el último estado que informó el equipo para un sensor.

        Va acá y no en los callbacks de la app porque `register_callback()`
        pisa: sólo puede haber un callback por evento, y en USB el de NIBP se
        lo queda `serial_manager` para su control de la medición. El parser, en
        cambio, ve todos los paquetes en los dos transportes.

        Deliberadamente NO se guarda en `self.data`: eso viaja en el POST de
        métricas y cambiaría ese contrato. El /health lo lee de acá.
        """
        entrada = {"status": status} if isinstance(status, str) else dict(status)
        entrada["_t"] = time.monotonic()
        self.sensor_status[sensor] = entrada

    def register_callback(self, name: str, callback: Callable) -> None:
        found = False
        for key, (callback_name, _) in self.callbacks.items():
            if callback_name == name:
                self.callbacks[key] = (callback_name, callback)
                found = True

                break
        if not found:
            print("[DBG][WARN] callback name not found:", name)


    def add_data(self, data: bytearray) -> None:
        self.raw_buffer.extend(data)

        while len(self.raw_buffer) >= self.PACKAGE_MIN_LENGTH:
            try:
                start_idx = self._find_package_start()
                if start_idx == -1:
                    self.raw_buffer = self.raw_buffer[-1:]
                    continue

                package_length = self.raw_buffer[start_idx + 2]
                end_idx = start_idx + package_length + 2

                if end_idx > len(self.raw_buffer):
                    break

                package = self.raw_buffer[start_idx:end_idx]
                self.raw_buffer = self.raw_buffer[end_idx:]

                if self._check_sum(package):
                    self._parse_package(package)

            except Exception as e:
                print(f"Error processing package: {e}")
                self.raw_buffer = self.raw_buffer[-1:]

    def get_current_data(self):
        """El estado actual, SIN consumirlo. Para inspeccionar, no para postear.

        Lo que se postea sale de `tomar_payload()`, que además vacía las ondas.
        Usar ésta para armar un POST es el bug que se arregló: las muestras se
        quedan en la ventana y se pisan con las que llegan después.
        """
        return self.data

    # ---------- helpers -----------------------------------------------------
    def _find_package_start(self) -> int:
        for i in range(len(self.raw_buffer) - 1):
            if (
                self.raw_buffer[i]     == self.PACKAGE_HEADER[0] and
                self.raw_buffer[i + 1] == self.PACKAGE_HEADER[1]
            ):
                return i
        return -1

    @staticmethod
    def _check_sum(package: list) -> bool:
        checksum = ~sum(package[2:-1]) & 0xFF
        return checksum == package[-1]

    def _format_value(self, value) -> str:
        return "-" if value == 0 else str(value)

    def _decode_ecg_info(self, package: list) -> dict:
        """Decodifica el estado del ECG, el ST y el código HRV del paquete 0x02.

        Manual del PM6750, págs. 10–11. El paquete es
        `55 AA 08 02 <status> <HR> <RR> <ST> <HRV> <cs>`, pero algunas versiones
        de firmware mandan menos bytes, así que los campos del final se leen
        sólo si están presentes.
        """
        info = dict(self.ECG_INFO_DEFAULT)

        status = package[4]
        info["weakSignal"] = bool(status & 0x01)          # BIT0
        info["leadOff"]    = bool(status & 0x02)          # BIT1
        info["gain"] = self.ECG_GAIN_MODES.get((status >> 2) & 0x03)   # BIT3~2
        info["mode"] = self.ECG_FILTER_MODES.get((status >> 4) & 0x03)  # BIT5~4

        # payload = todo lo que hay entre el tipo y el checksum
        payload_len = len(package) - 5

        if payload_len >= 4:
            # ST: signed char, -100..+100 → -1,00 mV .. +1,00 mV
            raw_st = package[7]
            st = raw_st - 256 if raw_st > 127 else raw_st
            info["st"] = round(st / 100.0, 2)

        if payload_len >= 5:
            info["hrv"] = self.HRV_CODES.get(package[8], f"desconocido_0x{package[8]:02X}")

        return info

    def _ecg_window_len(self) -> int:
        """Muestras acumuladas en la ventana actual (todas las derivaciones van
        parejas: se agrega una muestra a cada una por paquete)."""
        return len(self.data["ecg"][self.ECG_LEADS[0]])

    def tomar_payload(self) -> dict:
        """Devuelve lo que hay para postear y arranca una ventana nueva.

        Las ondas se **entregan y se vacían**: lo que sale de acá no vuelve a
        salir, y lo que entra después va a la ventana siguiente. Los signos
        vitales y `ecgInfo` no, que son estado y se siguen publicando igual
        aunque no cambien.

        Antes las ondas se vaciaban por reloj —una ventana nueva en cada cambio
        de segundo— y el que posteaba sólo hacía una copia, sin consumir. Como
        el POST sale cada `sleep(1)` MÁS lo que tarde la request, nunca quedaba
        alineado con esa frontera: cada POST se llevaba el pedazo acumulado
        desde el último borrado y el resto se perdía. Medido con reloj real:
        llegaban ~120 de las 250 muestras por derivación y la cobertura era del
        **47 %**, con el número ciclando entre 1 y 250 a medida que la fase
        derivaba. El requisito es el contrario — si el equipo manda una muestra,
        se manda (ver `tools/ecg_audit.py`).

        Sobre hilos: el lector llena las listas desde su hilo y esto las cambia
        desde el event loop. Cada lista se reemplaza con una sola asignación,
        así que una muestra que entre en el medio cae en la lista vieja —la que
        se está entregando— y viaja igual. Los picos se reemplazan antes que la
        onda a propósito: al revés, un pico que entrara justo en el medio
        quedaría indexado contra una ventana que ya se fue.
        """
        picos = self.data["ecgPeaks"]
        self.data["ecgPeaks"] = []
        ecg = self.data["ecg"]
        self.data["ecg"] = {lead: [] for lead in self.ECG_LEADS}
        spo2 = self.data["spo2"]
        self.data["spo2"] = []
        resp = self.data["resp"]
        self.data["resp"] = []

        return {
            "spo2": spo2,
            "ecg": ecg,
            "ecgPeaks": picos,
            "ecgInfo": dict(self.data["ecgInfo"]),
            "resp": resp,
            "vitalSigns": dict(self.data["vitalSigns"]),
        }

    # ---------- main package handler ---------------------------------------
    def _parse_package(self, package: list) -> None:
        package_type = package[3]
        if package_type not in self.callbacks:
            return

        callback_name, callback = self.callbacks[package_type]
        if callback is None:
            # Antes acá había un `return`, y era un bug: si nadie registraba el
            # callback (que es sólo una notificación en vivo), el paquete se
            # descartaba entero y sus datos NUNCA llegaban al payload. Es lo que
            # pasaba con el pico de latido 0x30. Guardar en self.data no debe
            # depender de que haya un consumidor: se usa un no-op y se sigue.
            callback = _noop

        try:
            # tiempo seguro para cualquier hilo
            try:
                current_time = int(asyncio.get_event_loop().time())
            except RuntimeError:
                current_time = int(time.time())

            if callback_name == "on_spo2_waveform_received":
                if len(self.data["spo2"]) < self.max_waveform_points:
                    self.data["spo2"].append(package[4])
                callback(package[4])

            elif callback_name == "on_ecg_waveform_received":
                # OJO: los 7 bytes del paquete NO son 7 muestras temporales,
                # son 7 DERIVACIONES (I, II, III, aVR, aVL, aVF, V) del mismo
                # instante, cada una muestreada a 250 Hz. Ver docs/ecg.md.
                # Datos útiles: desde el byte 4 hasta antes del checksum.
                # Normalmente son 7; si llegaran menos, se guardan los que haya.
                samples = package[4:-1]
                if self._ecg_window_len() < self.max_waveform_points:
                    for lead, value in zip(self.ECG_LEADS, samples):
                        self.data["ecg"][lead].append(value)
                callback(package[4])

            elif callback_name == "on_resp_waveform_received":
                if len(self.data["resp"]) < self.max_waveform_points:
                    self.data["resp"].append(package[4])
                callback(package[4])

            elif callback_name == "on_ecg_params_received":
                heart_rate = self._format_value(package[5])
                resp_rate  = self._format_value(package[6])
                self.data["vitalSigns"]["heartRate"] = heart_rate
                self.data["vitalSigns"]["respRate"]  = resp_rate
                # ST (package[7]) y código HRV (package[8]) antes se ignoraban.
                # Van en data["ecgInfo"] y no en vitalSigns porque _is_valid_data
                # compara todos los vitalSigns como strings contra "- -"/"-".
                self.data["ecgInfo"] = self._decode_ecg_info(package)
                self._record_sensor("ecg", decode_ecg_status(package[4]))
                callback(package[4], package[5], package[6])

            elif callback_name == "on_spo2_params_received":
                spo2  = package[5]
                pulse = package[6]
                # El pulso vive acá, en vitalSigns.spo2Pulse ("SpO2/pulso").
                # Rangos del manual (pág. 12): SpO2 0-100, siendo 127 error;
                # pulso 0-250, siendo 255 error. Se valida cada uno por
                # separado: antes, un SpO2 con error (>100) blanqueaba también
                # el pulso, aunque el pulso fuera bueno.
                spo2_val  = self._format_value(spo2) if spo2 <= 100 else "-"
                pulse_val = self._format_value(pulse) if pulse <= 250 else "-"
                if spo2_val == "-" and pulse_val == "-":
                    # Sentinela de "sin dato"; _is_valid_data lo reconoce.
                    self.data["vitalSigns"]["spo2Pulse"] = "- - /- -"
                else:
                    self.data["vitalSigns"]["spo2Pulse"] = f"{spo2_val}/{pulse_val}"
                self._record_sensor("spo2", decode_spo2_status(package[4]))
                callback(package[4], spo2, pulse)

            elif callback_name == "on_temp_params_received":
                temp = (package[5] * 10 + package[6]) / 10.0
                temp_str = self._format_value(temp)
                self.data["vitalSigns"]["temperature"] = temp_str
                self._record_sensor("temperature", decode_temp_status(package[4]))
                callback(package[4], temp)

            elif callback_name == "on_nibp_params_received":
                sys = package[6]
                dia = package[8]
                sys_val = self._format_value(sys)
                dia_val = self._format_value(dia)
                if sys != 0 or dia != 0:
                    self.data["vitalSigns"]["nibp"] = f"{sys_val}/{dia_val}"
                self._record_sensor("nibp", decode_nibp_status(package[4]))
                if package[4] == 0:
                    callback(package[4], package[5] * 2, sys, package[7], dia)
                else: # retro compatibilidad con otras versiones
                    callback(package[4], package[5] * 2, sys, package[7], dia)

            elif callback_name == "on_ecg_peak_received":
                # Marca de QRS: el equipo manda un 0x30 por cada latido que
                # detecta (verificado: intervalo mediano 718 ms ≈ 84 lpm contra
                # los 86 lpm que reportaba el 0x02 en la misma captura).
                # Se guarda la posición del latido DENTRO de la ventana actual
                # de ECG, para poder marcarlo sobre la onda que va en el mismo
                # POST. `ms` es ese índice llevado a tiempo con ECG_SAMPLE_RATE_HZ.
                if len(self.data["ecgPeaks"]) < self.max_peaks_per_window:
                    sample = self._ecg_window_len()
                    self.data["ecgPeaks"].append({
                        "sample": sample,
                        "ms": round(sample * 1000.0 / self.ECG_SAMPLE_RATE_HZ, 1),
                    })
                callback()

            elif callback_name == "on_spo2_peak_received":
                callback()

        except Exception as e:
            print(f"Error in callback {callback_name}: {e}")

    def reset_data(self):
        # limpiar buffer crudo y timestamp
        self.raw_buffer.clear()
        self.last_update_time = 0
        self._wave_last = {"ecg": 0, "spo2": 0, "resp": 0}

        # restaurar valores por defecto
        self.data = {
            "spo2": [],
            "ecg": {lead: [] for lead in self.ECG_LEADS},
            "ecgPeaks": [],
            "ecgInfo": dict(self.ECG_INFO_DEFAULT),
            "resp": [],
            "vitalSigns": {
                "heartRate": "- -",
                "nibp": self.NO_VALUE,
                "spo2Pulse": self.NO_VALUE,
                "temperature": "- -",
                "respRate": "- -",
            },
        }

    def reset_nibp(self):
        """Borra sólo la presión, sin tocar el resto de la sesión.

        La usa la app al pedir una medición nueva. No sirve `reset_data()`:
        eso borraría también SpO2, temperatura, pulso y las ondas, que siguen
        llegando en vivo y no tienen nada que ver con el manguito.

        Hace falta porque el parser no pisa el valor cuando el equipo manda
        sistólica/diastólica en cero — que es lo que manda mientras infla — así
        que sin esto la medición anterior se sigue publicando como si fuera la
        de ahora durante todo el inflado.
        """
        self.data["vitalSigns"]["nibp"] = self.NO_VALUE