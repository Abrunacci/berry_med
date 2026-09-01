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
        # Intentos de conexión seguidos en los que el equipo no mandó datos.
        # Es el nivel de la escalada de `_preparar()`; se pone en cero al
        # conectar. No lo tocan los fallos de apertura del puerto: que COM6 no
        # exista todavía es otra cosa (pasa 40s después de arrancar Windows) y
        # no tiene sentido escalar por eso.
        self._fallos = 0

        self.parser.register_callback(
            "on_nibp_params_received", self._nibp_done
        )

    def reset_state(self):
        """Limpia por completo el estado de NIBP y timeouts.

        Incluye mandarle STOP al equipo: bajar `nibp_running` sólo limpia lo
        que creemos nosotros. Si la sesión anterior quedó a mitad de una
        medición (nadie cerró, se cortó la conexión, el operador arrancó una
        nueva), el manguito puede seguir inflándose por su cuenta y nosotros
        creeríamos que no hay nada corriendo.
        """
        try:
            self.nibp_running = False
            if getattr(self, "_nibp_timeout_task", None):
                try:
                    self._nibp_timeout_task.cancel()
                except Exception:
                    pass
                self._nibp_timeout_task = None

            if self.ser and self.ser.is_open:
                # 1) descartar lo que hubiera encolado para escribir
                try:
                    self.ser.reset_output_buffer()
                except Exception:
                    pass
                # 2) STOP de NIBP al equipo, y esperar a que salga de verdad
                #    (va antes de limpiar el buffer de entrada para que el
                #    reconocimiento del equipo caiga en una ventana limpia)
                try:
                    self.ser.write(_cmd(0x02, 0x00))   # STOP NIBP
                    self.ser.flush()
                except Exception as e:
                    print(f"[USB] No se pudo mandar STOP de NIBP: {e}")
                # 3) descartar frames viejos para no “arrastrar” la sesión previa
                try:
                    self.ser.reset_input_buffer()
                except Exception:
                    pass
        except Exception as e:
            print(f"[USB] Error en reset_state: {e}")

    # ---------- API pública -----------------------------------------------
    async def connect(self) -> bool:
        """Abre el puerto y arranca la lectura (async para la app)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._connect_sync)

    # Streams que se habilitan al conectar. NIBP (0x02) queda afuera a
    # propósito: infla el manguito de verdad y al conectar no sabemos si el
    # paciente lo tiene puesto.
    STREAM_COMMANDS = (0x01, 0x03, 0x04, 0xFB, 0xFE, 0xFF)

    def _send_enables(self):
        """Habilita los streams de datos.

        Se escribe todo junto y se loguea en hex: el equipo no hace eco de los
        comandos, así que si queda mudo la única evidencia de que los enables
        salieron de esta punta del cable es esta línea.
        """
        datos = b"".join(_cmd(a1, 1) for a1 in self.STREAM_COMMANDS)
        self.ser.write(datos)
        self.ser.flush()
        print(f"[USB] -> enables ({len(datos)}B): {datos.hex()}")

    def _start_reader(self):
        """Arranca el hilo que lee del puerto."""
        self._run = True
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()
        print("[USB] Lectura iniciada")

    def start_monitoring(self):
        # self.parser.reset_data()
        if not (self.ser and self.ser.is_open):
            print("[USB] Puerto no abierto"); return
        self._send_enables()
        self._start_reader()

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
    def _resync_device(self):
        """Deja el equipo en un estado conocido, sin apagarlo.

        El puerto abre aunque el equipo haya quedado a mitad de una trama o de
        una medición de presión: el CDC-ACM enumera igual. Se tira lo que haya
        en los buffers, se corta cualquier NIBP en curso y se apagan los streams
        para que el equipo salga de mitad de transmisión antes de volver a
        habilitarlos.

        Sirve para un equipo que responde pero quedó desincronizado. Contra el
        equipo trabado tras reiniciar la PC no hace nada — eso sólo sale
        cortándole la alimentación, ver `_preparar()`.

        Ojo con el apagado de streams: si el equipo acepta el disable pero
        ignora el enable que viene después, esto lo deja mudo. Por eso no corre
        en el primer intento.
        """
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()

        datos = _cmd(0x02, 0x00)                   # STOP NIBP
        datos += b"".join(_cmd(a1, 0) for a1 in self.STREAM_COMMANDS)
        self.ser.write(datos)
        self.ser.flush()
        print(f"[USB] -> resync stop+disables ({len(datos)}B): {datos.hex()}")

        # Darle tiempo a procesar los comandos y descartar lo que haya salido
        # como respuesta, para que la espera de datos arranque en limpio.
        time.sleep(0.3)
        self.ser.reset_input_buffer()

    def _estado_lineas(self, cuando: str):
        """Loguea la config real del puerto. Las líneas van sólo de referencia.

        Ojo con leerles sentido a cts/dsr: el COM6 del Berry no es un bridge
        sino un CDC-ACM nativo del STM32 (VID_0483 PID_5740, usbser.inf), y esas
        líneas salen de una notificación SERIAL_STATE que su firmware nunca
        emite. Dan `False` siempre, con el equipo sano y con el equipo mudo —
        medido el 21/08 en las dos condiciones. No discriminan nada.
        """
        try:
            print(f"[USB] Líneas {cuando}: "
                  f"cts={self.ser.cts} dsr={self.ser.dsr} cd={self.ser.cd} "
                  f"ri={self.ser.ri} | rts={self.ser.rts} dtr={self.ser.dtr} "
                  f"| {self.ser.baudrate}-{self.ser.bytesize}"
                  f"{self.ser.parity}{self.ser.stopbits} "
                  f"rtscts={self.ser.rtscts} dsrdtr={self.ser.dsrdtr} "
                  f"xonxoff={self.ser.xonxoff}")
        except Exception as e:
            print(f"[USB] No se pudo leer el estado de líneas: "
                  f"{type(e).__name__}: {e}")

    def _preparar(self, nivel: int):
        """Prepara el equipo para transmitir. Dos niveles, y nada más.

            0   enables a secas
            1+  + resync (stop de NIBP y apagar/prender streams)

        Acá hubo una escalada de cuatro niveles que agregaba un pulso de DTR/RTS
        y un break de línea. Se sacaron: sobre este hardware **no pueden hacer
        nada**. El COM6 no es un bridge CP210x/CH340 sino un CDC-ACM nativo del
        STM32 (VID_0483 PID_5740, driver usbser.inf), así que DTR/RTS es un
        SET_CONTROL_LINE_STATE y el break un SEND_BREAK: dos control requests
        por USB que el firmware ignora, no señales físicas atadas al reset del
        micro. Los logs del 20 y 21/08 lo confirman — decenas de pulsos y breaks,
        escalada hasta el nivel 10, cero recuperaciones.

        Cuando el equipo queda mudo no hay nada que esta función pueda hacer:
        está trabado el stack USB del STM32 y sólo sale cortándole la
        alimentación. Se probó y falló: `pnputil /restart-device`, y desenchufar
        el cable en vivo con el equipo alimentado (log del 21/08 12:48 — Windows
        reenumeró limpio y siguió sin transmitir). La única salida conocida es
        apagar y prender el equipo a mano; `run()` sigue reintentando cada 5s y
        engancha solo cuando eso pasa.
        """
        if nivel == 0:
            # Cuando el equipo está vivo ya viene transmitiendo solo: en el log
            # conecta con "Datos OK tras 0.0s (64 bytes)", o sea que había datos
            # antes de que mandáramos nada. Ahí apagarle los streams para volver
            # a prenderlos es riesgo sin beneficio. El buffer se limpia igual,
            # que no toca al equipo, para no dar por buenos bytes viejos.
            self.ser.reset_input_buffer()
            self._send_enables()
            return

        self._resync_device()
        self._send_enables()

    def _wait_for_data(self, timeout_sec: float = 5.0) -> bool:
        """Espera a que llegue al menos una cabecera de trama válida.

        Sin esto `connect()` devolvía True apenas abría el puerto, y `run()` se
        quedaba en su bucle infinito dando la conexión por buena: si el equipo
        no transmitía, la app parecía conectada y no reintentaba nunca. Eso es
        lo que obligaba a apagar y prender el Berry a mano después de reiniciar
        la PC.

        Corre antes de arrancar el hilo lector, así no compiten los dos por el
        puerto.
        """
        inicio = time.monotonic()
        limite = inicio + timeout_sec
        buf = bytearray()
        total = 0                 # bytes vistos, aunque se descarten

        while time.monotonic() < limite:
            trozo = self.ser.read(64)
            total += len(trozo)
            buf.extend(trozo)
            if HEADER in buf:
                print(f"[USB] Datos OK tras {time.monotonic() - inicio:.1f}s "
                      f"({total} bytes)")
                return True
            if len(buf) > 4096:          # no crecer sin límite si llega basura
                buf = buf[-1:]

        # Cuántos bytes llegaron distingue dos fallas muy distintas, y es el
        # dato que decide para dónde seguir:
        #   0 bytes  -> el equipo no transmite nada (no recibió los enables,
        #               o está en reset)
        #   >0 bytes -> transmite pero sin cabeceras válidas (baudios mal,
        #               línea sucia, o el bridge todavía inicializando)
        if total == 0:
            print(f"[USB] {timeout_sec:.0f}s sin UN SOLO BYTE del equipo")
        else:
            print(f"[USB] {total} bytes en {timeout_sec:.0f}s pero ninguna "
                  f"cabecera 55AA válida. Primeros bytes: {bytes(buf[:24]).hex()}")
        return False

    def _abort_connection(self):
        """Cierra el puerto para que el próximo intento arranque de cero."""
        self._run = False
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except Exception:
            pass
        self.ser = None

    def _connect_sync(self) -> bool:
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=0.3)
        except Exception as e:
            # Que el puerto no exista todavía no es el equipo colgado: después
            # de arrancar Windows COM6 puede tardar casi un minuto en aparecer.
            # No cuenta para la escalada.
            print(f"[USB] No se pudo abrir {self.port}: {e}")
            return False

        # La escalada tiene un solo escalón útil, así que satura en 1.
        nivel = min(self._fallos, 1)
        print(f"[USB] Puerto {self.port} abierto (recuperación nivel {nivel})")
        self._estado_lineas("al abrir")

        try:
            self._preparar(nivel)

            # Abrir el puerto no prueba nada: el bridge USB enumera aunque el
            # equipo esté colgado. Recién con datos reales damos la conexión
            # por buena; si no llegan, se cierra y `run()` reintenta a los 5s.
            if not self._wait_for_data(5.0):
                self._fallos += 1
                print(f"[USB] {self.port} abre pero el equipo no transmite "
                      f"(nivel {nivel}); se cierra y se reintenta")
                self._abort_connection()
                return False

            if nivel > 0:
                print(f"[USB] Recuperado en el nivel {nivel}")
            self._fallos = 0
            self._start_reader()
            return True
        except Exception as e:
            print(f"[USB] Error preparando {self.port}: "
                  f"{type(e).__name__}: {e}")
            self._abort_connection()
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
        sin_datos_desde = time.monotonic()
        aviso_mudo = False

        while self._run and self.ser and self.ser.is_open:
            try:
                data = self.ser.read(256)
            except Exception as e:
                # Antes esto mataba el hilo en silencio: la app seguía viva y
                # aparentemente conectada, sin recibir nada y sin reconectar.
                # Ahora al menos queda escrito con qué murió.
                print(f"[USB] LECTOR CAÍDO leyendo {self.port}: "
                      f"{type(e).__name__}: {e}")
                self._run = False
                break

            if not data:
                # Avisar una sola vez que el stream se cortó, con cuánto hace.
                mudo = time.monotonic() - sin_datos_desde
                if mudo > 5 and not aviso_mudo:
                    print(f"[USB] Sin datos hace {mudo:.0f}s (puerto abierto)")
                    aviso_mudo = True
                continue

            sin_datos_desde = time.monotonic()
            if aviso_mudo:
                print("[USB] Volvieron los datos")
                aviso_mudo = False
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
