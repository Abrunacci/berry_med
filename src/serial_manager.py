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
        self._nibp_timeout_task = None

        self.parser.register_callback(
            "on_nibp_params_received", self._nibp_done
        )

    def reset_state(self):
        """Limpia por completo el estado de NIBP y timeouts."""
        try:
            self.nibp_running = False
            if getattr(self, "_nibp_timeout_task", None):
                try:
                    self._nibp_timeout_task.cancel()
                except Exception:
                    pass
                self._nibp_timeout_task = None
            # limpiar buffers del puerto para no “arrastrar” frames viejos
            if self.ser and self.ser.is_open:
                try:
                    self.ser.reset_input_buffer()
                    self.ser.reset_output_buffer()
                except Exception:
                    pass
        except Exception as e:
            print(f"[USB] Error en reset_state: {e}")

    # ---------- API pública -----------------------------------------------
    async def connect(self) -> bool:
        """Abre el puerto y arranca la lectura (async para la app)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._connect_sync)

    def start_monitoring(self):
        # self.parser.reset_data()
        if not (self.ser and self.ser.is_open):
            print("[USB] Puerto no abierto"); return
        # Habilitar streams
        for a1, a2 in (
            (0x01,1), (0x03,1), (0x04,1),   # ECG / NIBP / SpO₂ / TEMP
            (0xFB,1), (0xFE,1), (0xFF,1),             # ECG / SpO₂ / RESP waves
        ):
            self.ser.write(_cmd(a1, a2))

        self._run = True
        self._t   = threading.Thread(target=self._loop, daemon=True)
        self._t.start()
        print("[USB] Lectura iniciada")

    def stop_monitoring(self):
        self._run = False
        self.nibp_running = False
        if self._t:
            self._t.join(timeout=1)
        if self.ser and self.ser.is_open:
            self.ser.close()
        self.nibp_running = False
        print("[USB] Lectura detenida")

    async def _nibp_watchdog(self):
        from time import monotonic
        try:
            while self.nibp_running:
                await asyncio.sleep(0.5)
                if monotonic() - self._last_nibp_update > self._nibp_timeout_sec:
                    print("[NIBP] Timeout sin cierre → forzando STOP y liberando estado")
                    await self.stop_nibp(force=True)
                    break
        except asyncio.CancelledError:
            pass

    async def start_nibp(self, timeout_sec: int = 90):
        """
        Arranca una medición de NIBP y arma un timeout por si no llega el cierre.
        """
        if not (self.ser and self.ser.is_open):
            print("[USB] No hay puerto abierto para NIBP")
            return

        if self.nibp_running:
            print("[USB] NIBP ya en curso")
            return

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._start_nibp_sync)

        # Programar timeout: si no se limpia con _nibp_done, liberamos flag
        if self._nibp_timeout_task:
            self._nibp_timeout_task.cancel()
        self._nibp_timeout_task = asyncio.create_task(self._nibp_timeout(timeout_sec))

    async def _nibp_timeout(self, timeout_sec: int):
        try:
            await asyncio.sleep(timeout_sec)
            if self.nibp_running:
                print("[USB] NIBP timeout; liberando estado")
                self.nibp_running = False
        except asyncio.CancelledError:
            pass

    # async def start_nibp(self):
        # if self.nibp_running:
        #     print("[BLE] NIBP ya en curso")
        # loop = asyncio.get_running_loop()
        # await loop.run_in_executor(None, self._start_nibp_sync)

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
        phase  = status & 0x03
        result = (status >> 2) & 0x0F
        # terminales (OK, cancel, error/abort, signal weak…) – ya los tenías
        if result in (0x0, 0x2, 0x4, 0x5):
            print(f"[DEBUG] NIBP DONE status=0x{status:02X} phase={phase} result={result} "
            f"cuff={cuff} sys={sys} mean={mean} dia={dia}")
            self.nibp_running = False
            if getattr(self, "_nibp_timeout_task", None):
                try: self._nibp_timeout_task.cancel()
                except Exception: pass
                self._nibp_timeout_task = None

            # si querés, asegurá STOP explícito al equipo:
            try:
                if self.ser and self.ser.is_open:
                    self.ser.write(_cmd(0x02, 0x00))  # STOP NIBP
            except Exception:
                pass

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
