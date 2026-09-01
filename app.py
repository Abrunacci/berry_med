import asyncio
import base64
import copy
import os
import sys
import time

import aiohttp
import certifi
import pysher

from config import build_metrics_url, get_config, redactar
from src.bluetooth_manager import BMPatientMonitor
from src.data_parser import BMDataParser
from src.logging_setup import setup as setup_logging
from src.health import HealthReporter
from src.thermometer_reader import ThermometerReader

# Lo primero de todo: la línea de abajo revienta con TypeError si get_config()
# devuelve None, y sin esto un arranque fallido no deja ningún rastro — que es
# justo lo que pasa cuando la app la levanta Windows sola y nadie está mirando.
_LOG_PATH = setup_logging("berry-monitor")
if _LOG_PATH:
    print(f"[LOG] Guardando salida en: {_LOG_PATH}")

os.environ["SSL_CERT_FILE"] = get_config()["ssl_cert_file"]


class VitalsMonitor:
    def __init__(self):
        self.data_parser = BMDataParser()
        cfg = get_config()
        print(redactar(cfg))
        self.using_usb_monitor = cfg.get("device_connection", "bt") == "usb"

        if self.using_usb_monitor:
            from src.serial_manager import PM6750USBReader

            self.monitor = PM6750USBReader(
                parser=self.data_parser,
                port=cfg.get("device_port", "COM3"),
                device_config=cfg,
            )
        else:
            self.monitor = BMPatientMonitor(
                self.data_parser, self.status_callback, device_config=cfg
            )
        
        # self.monitor = BMPatientMonitor(self.data_parser, self.status_callback)
        self.main_loop = None  # Almacenar el loop principal

        # Register callbacks
        self.data_parser.register_callback(
            "on_ecg_waveform_received", self.handle_ecg_wave
        )
        self.data_parser.register_callback(
            "on_spo2_waveform_received", self.handle_spo2_wave
        )
        self.data_parser.register_callback(
            "on_resp_waveform_received", self.handle_resp_wave
        )
        self.data_parser.register_callback("on_ecg_params_received", self.handle_ecg)
        self.data_parser.register_callback(
            "on_ecg_peak_received", self.handle_ecg_peak
        )
        self.data_parser.register_callback("on_spo2_params_received", self.handle_spo2)
        self.data_parser.register_callback(
            "on_temp_params_received", self.handle_temperature
        )
        if not self.using_usb_monitor:
            self.data_parser.register_callback("on_nibp_params_received", self.handle_nibp)

        # Initialize credentials
        self.credentials = get_config()
        if not self.credentials:
            print("Error: No se encontraron credenciales")
            sys.exit(1)

        # Initialize Pusher subscriber for public channel
        print("Initializing Pusher subscriber...")
        self.pusher_subscriber = pysher.Pusher(
            key=self.credentials["key"],
            cluster=self.credentials["cluster"],
        )

        # Initialize HTTP session for data sending
        self.api_url = self._build_api_url()
        print(f"[DEBUG] Endpoint de métricas: {self.api_url}")
        self.auth_token = base64.b64encode(
            f"{self.credentials['api_username']}:{self.credentials['api_password']}".encode()
        ).decode()

        # Initialize channel names from config
        self.public_channel = self.credentials["public_channel"]
        self.start_event_name = self.credentials["start_event_name"]
        self.stop_event_name = self.credentials["stop_event_name"]

        self.health_url = self._build_api_url(self.credentials.get("health_endpoint"))
        self.health_interval = float(
            self.credentials.get("health_interval_seconds", 60)
        )
        self.health = HealthReporter(
            str(self.credentials.get("totem_id") or ""), self.data_parser
        )
        print(f"[DEBUG] Endpoint de health: {self.health_url} "
              f"(cada {self.health_interval:.0f}s)"
              if self.health_interval else "[DEBUG] Health desactivado")

        self.is_sending_data = False
        # Tope de duración de una sesión sin stop. 0 = sin corte automático.
        self.max_session_seconds = (
            float(self.credentials.get("max_session_minutes", 30)) * 60
        )
        self._sending_since = None  # monotonic() del start; None = sin sesión

        # Bind connection handlers for the client
        self.pusher_subscriber.connection.bind(
            "pusher:connection_established", self.connect_handler
        )
        # Sin esto la caída de Pusher pasa desapercibida: el único bind que
        # había era el de conexión establecida, así que la app no se enteraba
        # de que dejó de recibir órdenes. El /health lee `connection.state`,
        # pero además conviene que quede en el log cuándo se cortó.
        self.pusher_subscriber.connection.bind(
            "pusher:connection_failed", self._pusher_caido
        )
        self.pusher_subscriber.connection.bind(
            "pusher:error", self._pusher_caido
        )
        self.pusher_subscriber.connect()

        self.command_queue = asyncio.Queue()
        self.thermometer_reader = None
        self.thermometer_enabled = bool(
            self.credentials.get("thermometer_enabled", False)
        )

        if self.thermometer_enabled:
            self.thermometer_reader = ThermometerReader(
                port=self.credentials.get("thermometer_port", "COM5"),
                baud=int(self.credentials.get("thermometer_baud", 115200)),
                reconnect_seconds=self.credentials.get("thermometer_reconnect_seconds", 2.0),
                status_callback=self.status_callback
            )
            self.thermometer_reader.start()

    def _build_api_url(self, endpoint=None) -> str:
        """Arma `{API_URL}/{TOTEM_ID}/{endpoint}`.

        Con `endpoint=None` devuelve el de métricas (`API_ENDPOINT`). Se le pasa
        otro para el /health, que cuelga de la misma base y el mismo id: es el
        mismo tótem informando otra cosa.

        `API_URL` es sólo la base del servicio: el id del tótem vive en
        `TOTEM_ID` y el último tramo en `API_ENDPOINT`, así una misma base
        sirve para todas las instalaciones y el id no queda repetido en dos
        claves que pueden desfasarse.

        Usa el mismo `build_metrics_url()` que la vista previa del asistente,
        así lo que se ve al configurar es lo que se postea.
        """
        totem_id = str(self.credentials.get("totem_id") or "").strip()

        if not totem_id:
            # Sin TOTEM_ID no hay nada que armar. Se cae al valor crudo de
            # API_URL, que es el comportamiento viejo, para no dejar mudo a un
            # tótem que todavía no migró la config.
            legacy = str(self.credentials.get("api_url") or "").strip()
            print(
                "[WARN] Falta TOTEM_ID: se usa API_URL tal cual está. "
                "Configuralo con el asistente para armar la URL correctamente."
            )
            return legacy or str(self.credentials.get("api_base_url") or "")

        return build_metrics_url(
            self.credentials.get("api_base_url"),
            totem_id,
            endpoint if endpoint is not None else self.credentials.get("api_endpoint"),
        )

    def status_callback(self, message: str):
        """Callback for device status updates"""
        print(f"[BERRY STATUS] {message}")

    # Handler methods
    def handle_ecg_wave(self, value: int):
        
        pass

    def handle_spo2_wave(self, value: int):
        
        pass

    def handle_resp_wave(self, value: int):
        
        pass

    def handle_ecg(self, states: int, heart_rate: int, resp_rate: int):
        # El estado de los sensores lo registra el parser (ver
        # BMDataParser.sensor_status): acá no, porque en USB este callback ni
        # siquiera se registra — lo toma serial_manager.
        pass

    def handle_ecg_peak(self):
        """Latido (QRS) detectado por el equipo.

        El parser ya lo guarda en data["ecgPeaks"] para que viaje en el POST;
        este callback existe para poder reaccionar en vivo (p. ej. un beep).
        """
        pass

    def handle_spo2(self, states: int, spo2: int, pulse_rate: int):
        pass

    def handle_temperature(self, states: int, temp: float):
        pass

    def handle_nibp(self, states: int, cuff: int, sys: int, mean: int, dia: int):
        pass

    def _pusher_caido(self, data=None):
        """Pusher se cayó o falló. Sólo se loguea: pysher reintenta solo.

        Lo que importa es que quede registrado el momento — el /health informa
        el estado actual, pero para entender un incidente hace falta saber
        cuándo se cortó.
        """
        print(f"[PUSHER] Conexión caída o con error: {data}")

    async def send_health(self):
        """Postea el estado del tótem al backend, cada `health_interval`.

        Es una tarea aparte y no un agregado al POST de métricas, por dos
        razones. `send_data()` sólo corre con sesión activa, y el health importa
        sobre todo cuando NO hay sesión: un tótem con el Berry desenchufado de
        madrugada tiene que poder avisarlo. Y arranca antes de que el equipo
        conecte, porque "el Berry no engancha" es justamente uno de los estados
        que hay que poder reportar.

        Nunca propaga excepciones: un backend caído o un error armando el
        snapshot no puede tumbar el monitoreo.
        """
        if not self.health_interval:
            print("[HEALTH] Desactivado (HEALTH_INTERVAL_SECONDS = 0)")
            return

        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    cuerpo = self._health_snapshot()
                    async with session.post(
                        self.health_url,
                        json=cuerpo,
                        headers={"Authorization": f"Basic {self.auth_token}",
                                 "Content-Type": "application/json"},
                    ) as resp:
                        if resp.status not in (200, 201, 204):
                            print(f"[HEALTH] El backend respondió {resp.status}")
                        else:
                            print(f"[HEALTH] {cuerpo['status']} "
                                  f"(sensores sueltos: "
                                  f"{cuerpo['disconnectedSensors'] or 'ninguno'})")
                except Exception as e:
                    print(f"[HEALTH] No se pudo reportar: {type(e).__name__}: {e}")
                await asyncio.sleep(self.health_interval)

    def _health_snapshot(self) -> dict:
        """Foto del estado actual. Ver `src/health.py` para el objeto."""
        try:
            device = self.monitor.link_status()
        except Exception as e:
            # Un lector sin link_status() (o a medio construir) no puede dejar
            # sin health al tótem: el resto del objeto sigue siendo útil.
            device = {"connected": False, "lastFrameSecondsAgo": None,
                      "error": f"{type(e).__name__}: {e}"}

        transcurrido = (
            round(time.monotonic() - self._sending_since, 1)
            if self._sending_since is not None else None
        )
        return self.health.snapshot(
            device=device,
            pusher=self.pusher_subscriber,
            session={"active": self.is_sending_data,
                     "secondsElapsed": transcurrido},
        )

    def connect_handler(self, data):
        """Handler for successful connection"""
        print(f"[DEBUG] Connected to Pusher, subscribing to: {self.public_channel}")
        try:
            channel = self.pusher_subscriber.subscribe(self.public_channel)
            if channel:
                channel.bind(self.start_event_name, self.handle_start_event)
                channel.bind(self.stop_event_name, self.handle_stop_event)
                channel.bind('start-blood-pressure', self.handle_blood_pressure_event)
                print(f"[DEBUG] Successfully subscribed to {self.public_channel}")
            else:
                print("[ERROR] Failed to subscribe to channel")
        except Exception as e:
            print(f"[ERROR] Subscription error: {e}")

    def _reset_session_state(self):
        """Borra todo lo medido en la sesión anterior.

        `data["vitalSigns"]` vive toda la vida del proceso y nada lo expira por
        tiempo, así que sin este reset el último valor medido sigue viajando en
        cada POST. Se nota sobre todo con el NIBP: el parser no lo pisa cuando
        el equipo manda sistólica/diastólica en cero (que es lo que manda
        mientras no hay medición en curso), así que el valor anterior queda
        pegado indefinidamente.

        Es best-effort a propósito: corre desde el hilo de callbacks de Pusher,
        y que falle un paso no puede impedir que corran los otros ni tumbar el
        handler.
        """
        if self.using_usb_monitor:
            try:
                self.monitor.reset_state()
            except Exception as e:
                print(f"[ERROR] No se pudo resetear el estado del lector USB: {e}")

        # Ojo: `self.data_parser`, no `self.monitor.parser`. El lector USB
        # expone el parser como `.parser` pero el de Bluetooth lo guarda en
        # `.data_parser`, así que `self.monitor.parser` reventaba con
        # AttributeError en las instalaciones por BT — que son el default.
        try:
            self.data_parser.reset_data()
        except Exception as e:
            print(f"[ERROR] No se pudo resetear el parser: {e}")

        # El termómetro externo es otro objeto y `reset_data()` no lo toca:
        # su última lectura tampoco vence, y `_apply_thermometer_temperature`
        # la vuelve a pisar sobre el payload en cada POST.
        if self.thermometer_reader:
            try:
                self.thermometer_reader.reset_latest()
            except Exception as e:
                print(f"[ERROR] No se pudo resetear el termómetro: {e}")

    def _stop_session(self, reason: str):
        """Corta el envío y deja todo limpio. Único camino de cierre."""
        self.is_sending_data = False
        self._sending_since = None
        self._reset_session_state()
        print(f"[DEBUG] Stopped data transmission ({reason})")

    def _session_expired(self) -> bool:
        """True si la sesión superó el tope sin que llegara el stop."""
        if not self.max_session_seconds or self._sending_since is None:
            return False
        return (time.monotonic() - self._sending_since) >= self.max_session_seconds

    def handle_start_event(self, event_data):
        """Handle the start monitoring event"""
        try:
            # Limpiar ANTES de habilitar el envío, para que el primer POST de
            # la medición nueva no pueda llevarse nada de la anterior. Va acá y
            # no sólo en el stop porque el stop puede no llegar nunca: pestaña
            # cerrada, Pusher caído, la app del otro lado reiniciando.
            self._reset_session_state()
            self._sending_since = time.monotonic()
            self.is_sending_data = True
            print("[DEBUG] Starting data transmission")
        except Exception as e:
            print(f"[ERROR] Error processing start event: {e}")

    def handle_stop_event(self, event_data):
        """Handle the stop monitoring event"""
        try:
            self._stop_session("evento stop")
        except Exception as e:
            print(f"[ERROR] Error processing stop event: {e}")

    def handle_blood_pressure_event(self, event_data):
        """Handle the start blood pressure measurement event"""
        try:
            print("[DEBUG] Starting blood pressure measurement")
            # Limpiar la presión anterior ANTES de arrancar. Mientras el
            # manguito infla, el equipo manda el 0x03 con sistólica y
            # diastólica en cero, y el parser no pisa el valor con ceros: sin
            # esto, la medición anterior se sigue publicando como si fuera la
            # de ahora durante todo el inflado. Se limpia sólo el NIBP; el
            # resto de los signos siguen llegando en vivo.
            self.data_parser.reset_nibp()
            if self.main_loop:
                future = asyncio.run_coroutine_threadsafe(
                    self.monitor.start_nibp(),
                    self.main_loop
                )
                future.result()  # Espera el resultado
            else:
                print("[ERROR] Main event loop not initialized")
        except Exception as e:
            print(f"[ERROR] Error starting blood pressure measurement: {e}")

    async def send_data(self):
        """Send data via HTTP POST"""
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    if not self.is_sending_data:
                        await asyncio.sleep(1)
                        continue

                    # Red de seguridad: si el stop nunca llega, la sesión se
                    # cierra sola. Sin esto el totem POSTea 1 vez por segundo
                    # para siempre — el reset del start deja los datos
                    # correctos, pero no apaga la sesión anterior.
                    if self._session_expired():
                        self._stop_session(
                            f"sin stop tras {self.max_session_seconds / 60:.0f} min"
                        )
                        continue

                    data = copy.deepcopy(self.data_parser.get_current_data())
                    self._apply_thermometer_temperature(data)
                    if not self._is_valid_data(data):
                        await asyncio.sleep(1)
                        continue

                    # Prepare the payload
                    payload = {"timestamp": int(time.time() * 1000), "data": data}

                    # Send data with retry logic
                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            print(f"[DEBUG] Sending data: {self._summarize(data)}")
                            async with session.post(
                                self.api_url,
                                json=payload,
                                headers={
                                    "Authorization": f"Basic {self.auth_token}",
                                    "Content-Type": "application/json",
                                },
                            ) as response:
                                if response.status == 200:
                                    
                                    print("[DATA] Vital signs sent successfully")
                                    break
                                else:
                                    print(
                                        f"[ERROR] API returned status {response.status}"
                                    )

                        except Exception as e:
                            if attempt == max_retries - 1:
                                print(
                                    f"[ERROR] Failed to send data after {max_retries} attempts: {e}"
                                )
                            else:
                                print(
                                    f"[WARN] Retry attempt {attempt + 1} after error: {e}"
                                )
                                await asyncio.sleep(0.5)

                except Exception as e:
                    print(f"[ERROR] Error in send_data loop: {e}")

                await asyncio.sleep(1)

    @staticmethod
    def _summarize(data):
        """Resumen del payload para el log.

        Se envían 7 derivaciones de ECG a ~251 Hz (~1.750 valores/seg), así que
        volcar el payload entero al stdout cada segundo lo vuelve ilegible. Se
        loguean los signos vitales y el largo de cada onda; el payload que se
        manda a la API no cambia.
        """
        waves = {key: len(data.get(key, [])) for key in ("spo2", "resp")}
        leads = {
            lead: len(values)
            for lead, values in (data.get("ecg") or {}).items()
        }
        return {
            "vitalSigns": data.get("vitalSigns", {}),
            "waveLens": waves,
            "ecgLens": leads,
            "ecgPeaks": len(data.get("ecgPeaks", [])),
            "ecgInfo": data.get("ecgInfo", {}),
        }

    def _apply_thermometer_temperature(self, data):
        if not self.thermometer_reader:
            return

        latest = self.thermometer_reader.get_latest()
        if not latest:
            return

        value = self._parse_thermometer_value(latest)
        if value is None:
            return

        celsius = self._to_celsius(value, latest.get("unit", "C"))
        data.setdefault("vitalSigns", {})["temperature"] = self._format_temperature_c(
            celsius
        )

    @staticmethod
    def _parse_thermometer_value(latest):
        try:
            value = latest.get("value")
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_celsius(value: float, unit: str) -> float:
        if str(unit).upper() == "F":
            return (value - 32) * 5 / 9
        return value

    @staticmethod
    def _format_temperature_c(value: float) -> str:
        if value == 0:
            return "-"
        return str(round(value, 1))

    def _is_valid_data(self, data):
        """Check if data contains any valid measurements"""
        if not data or not isinstance(data, dict):
            return False

        default_values = {"-", "- -", "- - /- -"}

        vital_signs = data.get("vitalSigns", {})
        if not vital_signs:
            return False

        if str(vital_signs.get("temperature", "-")) not in default_values:
            return True
        all_vitals_empty = all(
            str(value) in default_values for value in vital_signs.values()
        )

        # Check waveforms
        spo2_empty = all(v == 0 for v in data.get("spo2", []))
        # "ecg" es un dict de 7 derivaciones: está vacío si ninguna trae datos.
        # (Un len() sobre el dict daría 7 siempre y nunca sería "vacío".)
        ecg_empty = not any(data.get("ecg", {}).values())
        resp_empty = len(data.get("resp", [])) == 0

        # Data is valid only if we have some vital signs OR some non-zero waveform data
        return not (all_vitals_empty and spo2_empty and ecg_empty and resp_empty)

    async def run(self):
        self.main_loop = asyncio.get_running_loop()  # Guardar referencia al loop principal
        intento = 0
        arranque = time.monotonic()

        # El health arranca ANTES del bucle de conexión, a propósito: "el Berry
        # no engancha" es uno de los estados que hay que poder reportar, y si
        # el reporte esperara a que el equipo conecte, ese caso justamente nunca
        # se informaría.
        asyncio.create_task(self.send_health())

        while True:
            try:
                intento += 1
                # El número de intento y el tiempo desde que arrancó el proceso
                # son los dos datos que hacen falta para leer un arranque de
                # Windows: dicen si conectó a la primera o cuántos segundos
                # tardó en aparecer el puerto.
                print(f"\n[BERRY] Intento #{intento} de conectar "
                      f"(t+{time.monotonic() - arranque:.0f}s desde el arranque)")
                connected = await self.monitor.connect()
                if not connected:
                    print("[BERRY] Connection failed, retrying in 5 seconds...")
                    await asyncio.sleep(5)
                    continue

                print(f"[BERRY] Conectado en el intento #{intento}, "
                      f"t+{time.monotonic() - arranque:.0f}s")

                asyncio.create_task(self.send_data())
                asyncio.create_task(self.process_commands())
                if self.thermometer_reader:
                    self.thermometer_reader.start()
                print("[BERRY] Connection successful, starting data monitoring...")
                while True:
                    await asyncio.sleep(0.1)
            except Exception as e:
                print(f"[ERROR] Berry connection error: {str(e)}")
                await asyncio.sleep(5)

    async def process_commands(self):
        while True:
            command = await self.command_queue.get()
            if command == "start_nibp":
                await self.monitor.start_nibp()
            await asyncio.sleep(0.1)


async def main():
    monitor = VitalsMonitor()
    try:
        await monitor.run()
    except Exception as e:
        print(f"Error during initialization: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
