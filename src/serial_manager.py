import serial, threading, asyncio
from typing import Optional, Callable

HEADER = b"\x55\xAA"


def _checksum(n: int, data: bytes) -> int:
    return (~(n + sum(data)) & 0xFF) & 0xFF


def _cmd(a1: int, a2: int) -> bytes:
    """Construye un paquete 55 AA ‹len› ‹a1 a2› ‹checksum›."""
    payload = bytes([a1, a2])
    n = len(payload) + 2
    return HEADER + bytes([n]) + payload + bytes([_checksum(n, payload)])


class PM6750USBReader:
    """
    Lector USB con **la misma interfaz** que BMPatientMonitor:

        await connect()          → True/False
        await start_nibp()       → inicia medición de presión

    Todos los frames recibidos se pasan sin procesar al BMDataParser,
    así los callbacks existentes siguen funcionando (ECG, SpO₂, TEMP,
    RESP, NIBP, etc.).
    """

    def __init__(
        self,
        parser,
        port: str = "COM3",
        baud: int = 115_200,
        status_callback: Optional[Callable[[str], None]] = None,
    ):
        self.parser = parser
        self.port = port
        self.baud = baud
        self.status_callback = status_callback or (lambda *_: None)

        self._ser: Optional[serial.Serial] = None
        self._run = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    # ----------------------------------------------------------------- PUBLIC
    async def connect(self) -> bool:
        """Abre el puerto y arranca el hilo de lectura (API async)."""
        try:
            self.status_callback(f"Opening {self.port} @ {self.baud}…")
            self._ser = serial.Serial(self.port, self.baud, timeout=0.3)
        except (serial.SerialException, OSError) as exc:
            self.status_callback(f"Failed to open {self.port}: {exc}")
            return False

        self._enable_all_channels()           # envia comandos 55 AA …

        self._run = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        self.status_callback("USB device connected and reader running")
        return True

    async def start_nibp(self):
        """Comando 55 AA 04 02 01 F8 → comenzar medición NIBP."""
        with self._lock:
            if self._ser and self._ser.is_open:
                self._ser.write(_cmd(0x02, 0x01))

    def disconnect(self):
        """Llamable desde fuera si se requiere apagar el hilo manualmente."""
        self._run = False

    # -------------------------------------------------------------- INTERNAL
    def _enable_all_channels(self):
        if not (self._ser and self._ser.is_open):
            return
        # Parámetros
        self._ser.write(_cmd(0x01, 0x01))   # ECG param
        self._ser.write(_cmd(0x02, 0x01))   # NIBP param
        self._ser.write(_cmd(0x03, 0x01))   # SpO2 param
        self._ser.write(_cmd(0x04, 0x01))   # TEMP param
        # Waveforms
        self._ser.write(_cmd(0xFB, 0x01))   # ECG waveform
        self._ser.write(_cmd(0xFE, 0x01))   # SpO2 waveform
        self._ser.write(_cmd(0xFF, 0x01))   # RESP waveform

    def _read_loop(self):
        buf = bytearray()
        try:
            while self._run and self._ser and self._ser.is_open:
                data = self._ser.read(256)
                if not data:
                    continue
                buf.extend(data)
                while len(buf) >= 3:
                    if buf[:2] != HEADER:
                        buf.pop(0)
                        continue
                    n = buf[2]
                    if len(buf) < n + 3:
                        break
                    frame = bytes(buf[: n + 3])
                    buf = buf[n + 3 :]
                    self.parser.add_data(frame)  # ← delega al BMDataParser
        except Exception as exc:
            self.status_callback(f"USB reader stopped: {exc}")
        finally:
            self._cleanup()

    def _cleanup(self):
        self._run = False
        if self._ser and self._ser.is_open:
            try:
                self._ser.close()
            except Exception:
                pass
        self.status_callback("USB device disconnected")
