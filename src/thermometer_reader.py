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

    def _run_loop(self) -> None:
        while self._running:
            try:
                with serial.Serial(self.port, self.baud, timeout=0.5) as ser:
                    self._log(f"[THERM] Connected to {self.port}")
                    while self._running:
                        line = ser.readline()
                        if not line:
                            continue
                        self._parse_and_store(line)
            except Exception as exc:
                self._log(f"[THERM][WARN] Connection issue: {exc}")
                time.sleep(self.reconnect_seconds)

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
