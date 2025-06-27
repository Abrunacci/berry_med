import serial, threading, time, asyncio

HEADER = b"\x55\xAA"

def _cs(n, payload):                 # checksum
    return (~(n + sum(payload)) & 0xFF) & 0xFF

def _cmd(a1, a2):
    payload = bytes([a1, a2])
    n = len(payload) + 2
    return HEADER + bytes([n]) + payload + bytes([_cs(n, payload)])

class PM6750USBReader:
    """
    Lector PM-6750 vía USB.

    – async connect()   -> bool
    – start_monitoring() / stop_monitoring()
    – async start_nibp()               (medición única, evita reinflado)

    Mantiene la misma interfaz que el Bluetooth para que la app
    no tenga que distinguir el tipo de conexión.
    """
    def __init__(self, parser, port="COM4", baud=115200):
        self.parser = parser
        self.port   = port
        self.baud   = baud
        self.ser    = None
        self._run   = False
        self._t     = None              # hilo de lectura
        self.nibp_running = False       # ← flag

        # Resetea el flag cuando el módulo devuelve resultado
        self.parser.register_callback(
            "on_nibp_params_received", self._nibp_done
        )

    # ---------- API pública -----------------------------------------------
    async def connect(self) -> bool:
        """Abre el puerto y arranca la lectura (async para la app)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._connect_sync)

    def start_monitoring(self):
        if not (self.ser and self.ser.is_open):
            print("[USB] Puerto no abierto"); return
        # Habilitar streams
        for a1, a2 in (
            (0x01,1), (0x02,1), (0x03,1), (0x04,1),   # ECG / NIBP / SpO₂ / TEMP
            (0xFB,1), (0xFE,1), (0xFF,1),             # ECG / SpO₂ / RESP waves
        ):
            self.ser.write(_cmd(a1, a2))

        self._run = True
        self._t   = threading.Thread(target=self._loop, daemon=True)
        self._t.start()
        print("[USB] Lectura iniciada")

    def stop_monitoring(self):
        self._run = False
        if self._t:
            self._t.join(timeout=1)
        if self.ser and self.ser.is_open:
            self.ser.close()
        self.nibp_running = False
        print("[USB] Lectura detenida")

    async def start_nibp(self):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._start_nibp_sync)

    # ---------- Internos ---------------------------------------------------
    def _connect_sync(self) -> bool:
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=0.3)
            print(f"[USB] Puerto {self.port} abierto")
            self.start_monitoring()
            return True
        except Exception as e:
            print(f"[USB] No se pudo abrir {self.port}: {e}")
            return False

    def _start_nibp_sync(self):
        if self.ser and self.ser.is_open and not self.nibp_running:
            print("[USB] → start NIBP")
            self.ser.write(_cmd(0x02, 0x01))
            self.nibp_running = True     # ← bloqueo hasta recibir resultado

    def _nibp_done(self, status, cuff, sys, mean, dia):
        """
        Callback interno: libera nibp_running cuando la medición
        terminó, fue detenida, fuera de rango o falló.
        Bits 5..2 de 'status' indican el resultado.
        """
        result = (status >> 2) & 0x0F
        if result in (0x0, 0x2, 0x4, 0x5):   # finished / stopped / error / timeout
            self.nibp_running = False

    def _loop(self):
        buf = bytearray()
        while self._run and self.ser and self.ser.is_open:
            data = self.ser.read(256)
            if not data:
                continue
            buf.extend(data)
            while len(buf) >= 3:
                if buf[:2] != HEADER:
                    buf.pop(0); continue
                n = buf[2]
                if len(buf) < n + 3:
                    break
                frame = bytes(buf[: n + 3])
                buf   = buf[n + 3:]
                self.parser.add_data(frame)
