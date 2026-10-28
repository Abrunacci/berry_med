import json
import threading
import time
from typing import Any, Callable, Dict, Optional

import serial


class ThermometerReader:
    def __init__(
        self,
        port: str,
        baud: int = 115200,
        reconnect_seconds: float = 2.0,
        status_callback: Optional[Callable[[str], None]] = None,
    ):
        self.port = port
        self.baud = baud
        self.reconnect_seconds = reconnect_seconds
        self.status_callback = status_callback
        self._latest: Optional[Dict[str, Any]] = None
        self._latest_lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_parse_error_log = 0.0
        # Puerto abierto ahora mismo, o None. Lo lee `link_status()` para el
        # /health: es lo único que dice si el termómetro está enganchado, porque
        # el equipo sólo manda datos cuando alguien se mide.
        self._ser: Optional[serial.Serial] = None
        # monotonic() de la última lectura válida. No lo toca `reset_latest()`:
        # descartar el valor al empezar una sesión no dice nada del enlace.
        self._last_reading_t: Optional[float] = None
        # Por qué falló el último intento de abrir el puerto. Con un COM mal
        # asignado, es el dato que permite verlo desde el backend.
        self._last_error: Optional[str] = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._log(f"[THERM] Reader started on {self.port} @ {self.baud}")

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)
            self._thread = None
        self._log("[THERM] Reader stopped")

    def get_latest(self) -> Optional[Dict[str, Any]]:
        with self._latest_lock:
            if self._latest is None:
                return None
            return dict(self._latest)

    def reset_latest(self) -> None:
        """Descarta la última lectura, sin cortar la conexión al termómetro.

        La usa la app al arrancar una medición: el valor guardado no vence solo,
        así que sin esto la temperatura de la sesión anterior se sigue copiando
        al payload hasta que llegue una lectura nueva por el puerto.
        """
        with self._latest_lock:
            self._latest = None

    def link_status(self) -> Dict[str, Any]:
        """Estado del enlace con el termómetro, para el /health.

        Mismo criterio que `PM6750USBReader.link_status()`: `connected` se
        deduce del puerto abierto y el hilo lector vivo, no de un flag. A
        diferencia del Berry, la antigüedad de la última lectura no sirve para
        decidirlo: el termómetro sólo transmite cuando alguien se mide, así que
        con el tótem ocioso es normal que no llegue nada durante horas. Se
        publica igual, porque un puerto abierto que nunca dio una lectura puede
        ser otro dispositivo en ese COM.
        """
        ser = self._ser
        abierto = bool(ser is not None and ser.is_open)
        vivo = bool(self._thread and self._thread.is_alive())
        t = self._last_reading_t
        return {
            "enabled": True,
            "port": self.port,
            "connected": abierto and vivo,
            "portOpen": abierto,
            "readerAlive": vivo,
            "lastReadingSecondsAgo": (
                round(time.monotonic() - t, 1) if t is not None else None
            ),
            "error": None if abierto else self._last_error,
        }

    def _run_loop(self) -> None:
        while self._running:
            try:
                with serial.Serial(self.port, self.baud, timeout=0.5) as ser:
                    self._ser = ser
                    self._last_error = None
                    self._log(f"[THERM] Connected to {self.port}")
                    while self._running:
                        line = ser.readline()
                        if not line:
                            continue
                        self._parse_and_store(line)
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._log(f"[THERM][WARN] Connection issue: {exc}")
                time.sleep(self.reconnect_seconds)
            finally:
                self._ser = None

    def _parse_and_store(self, line: bytes) -> None:
        try:
            payload = json.loads(line.decode("utf-8", errors="replace").strip())
            value = payload.get("valor_temperatura_ir")
            unit = payload.get("unit")
            if value is None or unit is None:
                return
            parsed = {
                "value": float(value),
                "unit": str(unit).upper(),
            }
            with self._latest_lock:
                self._latest = parsed
            self._last_reading_t = time.monotonic()
        except Exception as exc:
            now = time.time()
            if now - self._last_parse_error_log >= 5:
                self._last_parse_error_log = now
                self._log(f"[THERM][WARN] Invalid JSON line: {exc}")

    def _log(self, message: str) -> None:
        if self.status_callback:
            self.status_callback(message)
        else:
            print(message)
