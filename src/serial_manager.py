import atexit, serial, threading, time, asyncio

from src.pm6750_protocol import (
    CMD_NIBP,
    HEADER,
    build_command as _cmd,
    build_nibp_commands,
    build_startup_commands,
    checksum as _cs,
)

# ---------------------------------------------------------------------------
# Watchdog PM6750
# ---------------------------------------------------------------------------
# El relay puede actuar en dos casos:
#   1) el COM configurado abre pero el PM no entrega datos válidos durante
#      la conexión inicial;
#   2) el PM ya estaba funcionando y luego queda mudo durante 20 s.
#
# Hay un tercer caso que NO usa el relay: si el USB desaparece y pyserial
# lanza una excepción de lectura, el watchdog descarta el handle viejo y
# espera que vuelva el mismo COM configurado para abrirlo nuevamente.
#
# Si el COM no existe o no se puede abrir durante el arranque normal, NO se
# toca el relay.
WATCHDOG_SILENCE_SEC = 20.0
WATCHDOG_RELAY = 1
WATCHDOG_POWER_OFF_SEC = 5.0
WATCHDOG_RECONNECT_RETRY_SEC = 5.0
WATCHDOG_USB_RECONNECT_RETRY_SEC = 2.0


def _relay_estado_seguro(momento: str) -> None:
    """Fuerza el relay del PM a OFF, o sea 12 V presentes.

    El relay queda latcheado en la placa: sobrevive a que se muera el proceso.
    Si BerryMonitor cae entre el corte y la restauración —taskkill, un crash,
    un update, el operador cerrando la ventana— el PM6750 queda sin
    alimentación, y en el arranque siguiente el COM no existe, así que
    `_connect_sync()` corta en el `serial.Serial()` y no llega nunca a la rama
    que accionaría el relay. Sin esto el equipo queda muerto hasta que alguien
    lo desenchufa a mano.

    Restaurar alimentación es siempre seguro, así que esto corre incondicional
    al arrancar. El `atexit` cubre las salidas ordenadas; las que no lo son
    (taskkill /F, corte de luz de la PC) las cubre el arranque siguiente.
    """
    try:
        from src.usbrelay import read_status, restore_power
        # Sondear primero: si no hay placa esto corta acá, en vez de meterse en
        # los reintentos de `restore_power` y demorar el arranque.
        read_status()
    except Exception:
        return

    if restore_power(WATCHDOG_RELAY):
        print(f"[RELAY] Estado seguro al {momento}: relay {WATCHDOG_RELAY} "
              f"OFF (12 V presentes)")


_estado_seguro_registrado = False


def _registrar_estado_seguro() -> None:
    """Aplica el estado seguro al arrancar y lo deja agendado para la salida."""
    global _estado_seguro_registrado
    if _estado_seguro_registrado:
        return
    _estado_seguro_registrado = True
    _relay_estado_seguro("arrancar")
    atexit.register(_relay_estado_seguro, "salir")


class PM6750USBReader:
    """
    Lector PM-6750 vía USB.

    – async connect()   -> bool
    – start_monitoring() / stop_monitoring()
    – async start_nibp()               (medición única, evita reinflado)
    Mantiene la misma interfaz que el Bluetooth para que la app
    no tenga que distinguir el tipo de conexión.
    """
    def __init__(self, parser, port="COM4", baud=115200, device_config=None):
        self.parser = parser
        self.port   = port
        self.baud   = baud
        self.device_config = device_config or {}
        self.ser    = None
        self._run   = False
        self._t     = None              # hilo de lectura
        self.nibp_running = False       # ← flag
        self._nibp_timeout_task = None
        # monotonic() de la última lectura con datos. None = todavía nunca
        # llegó nada. Lo lee `link_status()` para el /health: es la única señal
        # de vida real del enlace — que `connect()` haya dado True sólo dice
        # que en algún momento hubo datos, no que siga habiéndolos.
        self._ultimo_dato = None
        # `_run` no alcanza para saber si hay que abandonar una recuperación:
        # `_abort_connection()` lo baja en cada power-cycle, que es justo lo que
        # el watchdog hace antes de reintentar. Esta bandera la levanta sólo
        # `stop_monitoring()` y la baja sólo un arranque pedido por la app.
        self._stop_pedido = False
        self.parser.register_callback(
            "on_nibp_params_received", self._nibp_done
        )
        _registrar_estado_seguro()

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
                    self.ser.write(_cmd(CMD_NIBP, 0x00))   # STOP NIBP
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
        self._stop_pedido = False
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._connect_sync)

    def _send_enables(self):
        """Configura el equipo y habilita los streams de datos.
        La secuencia la arma `build_startup_commands()`: primero la ganancia y
        el modo de ECG (que salen de la config), después los enables, para que
        las muestras salgan ya con la configuración pedida. NIBP queda afuera a
        propósito — ver el docstring de esa función.
        Se escribe todo junto y se loguea en hex: el equipo no hace eco de los
        comandos, así que si queda mudo la única evidencia de que la secuencia
        salió de esta punta del cable es esta línea.
        """
        datos = b"".join(
            _cmd(a1, a2) for a1, a2 in build_startup_commands(self.device_config)
        )
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
        self._stop_pedido = False
        if not (self.ser and self.ser.is_open):
            print("[USB] Puerto no abierto"); return
        self._send_enables()
        self._start_reader()

    def stop_monitoring(self):
        # Antes que nada: cortar cualquier recuperación del watchdog en curso.
        # El `join(timeout=1)` de abajo se rinde al segundo, y sin esta bandera
        # el hilo seguiría power-cycleando el equipo y, al reconectar, llamaría
        # a `_start_reader()` —que vuelve a poner `_run=True`— resucitando un
        # lector que la app acaba de detener.
        self._stop_pedido = True
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

    def _clear_parser_data(self, log_prefix="[WATCHDOG]"):
        """Descarta datos acumulados del PM antes/después de una caída de enlace.

        Limpia tanto el buffer interno del parser como los últimos valores
        persistentes que podrían seguir siendo tomados por send_data() mientras
        el PM está desconectado. No toca Pusher, la sesión ni las tareas de envío.
        """
        try:
            self.parser.reset_data()
            print(f"{log_prefix} Datos anteriores del PM6750 descartados")
        except Exception as e:
            print(f"{log_prefix} No se pudo limpiar el parser: "
                  f"{type(e).__name__}: {e}")

    def _abort_connection(self):
        """Cierra el puerto para que el próximo intento arranque de cero."""
        self._run = False
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except Exception:
            pass
        self.ser = None

    def _power_cycle_pm(self, reason: str) -> bool:
        """Corta y restaura los 12 V del PM usando USBRelay2.

        Antes de tocar el COM verifica que el Relay2 exista. Si no está
        disponible no se hace ninguna acción destructiva.
        """
        try:
            from src.usbrelay import read_status, restart_device
            estado = read_status()
            print(f"[WATCHDOG] USBRelay2 OK (ID={estado.get('id', '?')})")
        except Exception:
            print("[WATCHDOG] USBRelay2 modulo ausente")
            return False

        print(f"[WATCHDOG] {reason}")
        print(f"[WATCHDOG] Reinicio de alimentación: relay {WATCHDOG_RELAY}, "
              f"corte {WATCHDOG_POWER_OFF_SEC:.0f}s")

        # Siempre descartar el handle viejo antes del power-cycle: al cortar
        # alimentación Windows puede invalidar el VCOM y pyserial queda con un
        # handle que ya no sirve.
        self._abort_connection()

        try:
            restart_device(WATCHDOG_RELAY, WATCHDOG_POWER_OFF_SEC)
            return True
        except Exception as e:
            print(f"[WATCHDOG] Error accionando Relay2: {type(e).__name__}: {e}")
            return False

    def _watchdog_recover(self) -> bool:
        """Recuperación cuando el PM ya estaba funcionando y queda mudo."""
        sin_alimentacion = False

        if not self._power_cycle_pm(
            f"PM6750 sin datos durante {WATCHDOG_SILENCE_SEC:.0f}s"
        ):
            if self.ser is not None:
                # No se llegó a tocar el puerto (típicamente: no hay placa de
                # relay). El enlace sigue como estaba, así que se devuelve el
                # control a `_loop`, que reintenta con su propia cadencia.
                return False

            # El power-cycle quedó a medias: se cortaron los 12 V y no se
            # pudieron restaurar, y `_abort_connection()` ya cerró el puerto.
            # Acá NO se puede devolver el control: `_loop` vería `_run=False`,
            # terminaría el hilo, y `app.py` se quedaría en su `while True`
            # para siempre con el equipo apagado y nadie buscándolo.
            sin_alimentacion = True
            print("[WATCHDOG] Power-cycle incompleto; se insiste con la "
                  "restauración y la reconexión")

        intento = 0
        while not self._stop_pedido:
            intento += 1
            print(f"[WATCHDOG] Intento #{intento} de recuperar {self.port}")

            if sin_alimentacion:
                # Mientras el relay siga en ON no hay COM que abrir: insistir
                # con los 12 V es la precondición de todo lo demás.
                try:
                    from src.usbrelay import restore_power
                    if restore_power(WATCHDOG_RELAY):
                        print("[WATCHDOG] Alimentación del PM6750 restaurada")
                        sin_alimentacion = False
                except Exception as e:
                    print(f"[WATCHDOG] Error restaurando alimentación: "
                          f"{type(e).__name__}: {e}")

            try:
                # No permitir que _connect_sync() dispare OTRO power-cycle
                # desde esta recuperación. Así evitamos recursión y ciclos de
                # relay encadenados.
                if self._connect_sync(allow_relay_reset=False):
                    if self._stop_pedido:
                        # Pararon la app mientras abríamos el puerto: soltar lo
                        # recién abierto en vez de dejar un lector huérfano.
                        self._abort_connection()
                        break
                    print(f"[WATCHDOG] PM6750 recuperado en {self.port}")
                    return True
            except Exception as e:
                print(f"[WATCHDOG] Error de reconexión: {type(e).__name__}: {e}")
            time.sleep(WATCHDOG_RECONNECT_RETRY_SEC)

        print("[WATCHDOG] Recuperación abandonada: se pidió detener la lectura")
        return False

    def _watchdog_reconnect_usb(self) -> bool:
        """Recupera una caída física/lógica del VCOM sin accionar Relay2.

        Se usa cuando el reader recibe SerialException/OSError (por ejemplo al
        desenchufar el USB del PM). El handle que tenía pyserial ya no es
        reutilizable: se descarta y se intenta abrir de nuevo el MISMO COM
        configurado hasta que vuelva a existir y entregue datos.

        Importante: durante esta recuperación se llama a _connect_sync() con
        allow_relay_reset=False. Una desconexión USB no debe disparar por sí
        sola un corte de 12 V.
        """
        print(f"[WATCHDOG] Se perdió la conexión USB del PM6750 en {self.port}")

        # Cortar inmediatamente la publicación de valores anteriores: el parser
        # mantiene estado entre tramas y send_data() puede seguir consultándolo
        # aunque el reader haya caído.
        self._clear_parser_data()

        print(f"[WATCHDOG] Esperando que reaparezca {self.port}...")

        # El handle con el que falló ser.read() puede quedar inválido aunque
        # Windows vuelva a enumerar el dispositivo con el mismo número de COM.
        self._abort_connection()

        intento = 0
        while not self._stop_pedido:
            intento += 1
            print(f"[WATCHDOG] Intento #{intento} de reconexión USB en {self.port}")
            try:
                if self._connect_sync(allow_relay_reset=False):
                    if self._stop_pedido:
                        self._abort_connection()
                        break
                    print(f"[WATCHDOG] Conexión USB del PM6750 recuperada en {self.port}")
                    return True
            except Exception as e:
                print(f"[WATCHDOG] Error durante la reconexión USB: "
                      f"{type(e).__name__}: {e}")

            print(f"[WATCHDOG] {self.port} todavía no disponible o sin datos; "
                  f"nuevo intento en {WATCHDOG_USB_RECONNECT_RETRY_SEC:.0f}s")
            time.sleep(WATCHDOG_USB_RECONNECT_RETRY_SEC)

        print("[WATCHDOG] Reconexión USB abandonada: se pidió detener la lectura")
        return False

    def _connect_sync(self, allow_relay_reset: bool = True) -> bool:
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=0.3)
        except Exception as e:
            # Que el puerto no exista todavía no es el equipo colgado: después
            # de arrancar Windows COM6 puede tardar casi un minuto en aparecer.
            print(f"[USB] No se pudo abrir {self.port}: {e}")
            return False

        print(f"[USB] Puerto {self.port} abierto")
        try:
            # Se limpia el buffer y se manda la secuencia de arranque, y nada
            # más. No se le apagan los streams para volver a prenderlos: cuando
            # el equipo está vivo ya viene transmitiendo solo —en el log conecta
            # con "Datos OK tras 0.0s (64 bytes)", o sea que había datos antes
            # de que mandáramos nada— así que el apagado es riesgo sin
            # beneficio: si acepta el disable e ignora el enable, lo dejamos
            # mudo nosotros.
            #
            # Y cuando el equipo ya está mudo no hay nada que se pueda hacer
            # desde acá: está trabado el stack USB del STM32 y sólo sale
            # cortándole la alimentación. Descartado por logs del 20 y 21/08:
            # pulsos de DTR/RTS y breaks de línea (sobre un CDC-ACM nativo son
            # control requests que el firmware ignora, no señales atadas al
            # reset del micro), `pnputil /restart-device`, y desenchufar el
            # cable en vivo con el equipo alimentado (21/08 12:48 — Windows
            # reenumeró limpio y siguió sin transmitir). `run()` reintenta cada
            # 5s y engancha solo cuando alguien lo apaga y lo prende.
            self.ser.reset_input_buffer()

            # Frontera limpia entre la sesión anterior y la nueva. Además del
            # buffer de pyserial, se descarta cualquier trama parcial y cualquier
            # último valor persistente que haya quedado en el parser.
            self._clear_parser_data(log_prefix="[USB]")

            self._send_enables()
            # Abrir el puerto no prueba nada: el bridge USB enumera aunque el
            # equipo esté colgado. Recién con datos reales damos la conexión
            # por buena; si no llegan, se cierra y `run()` reintenta a los 5s.
            if not self._wait_for_data(5.0):
                print(f"[USB] {self.port} abre pero el equipo no transmite datos válidos")

                # Diferencia fundamental:
                #   - si el COM NO abrió, arriba simplemente devolvemos False y
                #     no tocamos el relay; puede estar desconectado físicamente.
                #   - si el COM SÍ abrió pero el PM quedó mudo, ése es el fallo
                #     conocido del stack USB y sí corresponde power-cycle.
                if allow_relay_reset:
                    if self._power_cycle_pm(
                        f"{self.port} está abierto pero el PM6750 no transmite "
                        "durante la conexión inicial"
                    ):
                        print("[WATCHDOG] Power-cycle inicial realizado; "
                              "se reintentará la conexión")
                    else:
                        # Si no hubo relay, cerrar igual este intento para que
                        # run() conserve su retry normal de 5 s.
                        self._abort_connection()
                else:
                    self._abort_connection()
                return False
            self._start_reader()
            return True
        except Exception as e:
            print(f"[USB] Error preparando {self.port}: "
                  f"{type(e).__name__}: {e}")
            self._abort_connection()
            return False

    def link_status(self) -> dict:
        """Estado del enlace con el equipo, para el /health.
        `connected` no es un flag que alguien setea al conectar: se deduce del
        estado real —puerto abierto y el hilo lector vivo—, porque el flag
        mentía. Si el lector muere a mitad de sesión, `run()` se queda en su
        bucle creyendo que sigue todo bien; acá eso da `readerAlive: False`.
        `lastFrameSecondsAgo` es la señal que de verdad importa: un enlace
        abierto por el que hace 30s que no llega una trama está caído aunque el
        puerto siga abierto.
        """
        abierto = bool(self.ser and self.ser.is_open)
        vivo = bool(self._t and self._t.is_alive())
        if self._ultimo_dato is None:
            edad = None
        else:
            edad = round(time.monotonic() - self._ultimo_dato, 1)
        return {
            "transport": "usb",
            "port": self.port,
            "connected": abierto and vivo,
            "portOpen": abierto,
            "readerAlive": vivo,
            "lastFrameSecondsAgo": edad,
        }

    def _start_nibp_sync(self):
        if self.ser and self.ser.is_open and not self.nibp_running:
            print("[USB] → start NIBP")
            # Modo y presión objetivo van acá, justo antes de arrancar: el
            # manual pide setearlos inmediatamente antes de cada medición.
            for a1, a2 in build_nibp_commands(self.device_config):
                self.ser.write(_cmd(a1, a2))
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
                    self.ser.write(_cmd(CMD_NIBP, 0x00))  # STOP NIBP
            except Exception:
                pass

    def _loop(self):
        buf = bytearray()
        sin_datos_desde = time.monotonic()
        aviso_mudo = False
        self._ultimo_dato = sin_datos_desde
        while self._run and self.ser and self.ser.is_open:
            try:
                data = self.ser.read(256)
            except Exception as e:
                # Caída del VCOM: no accionar Relay2. El watchdog se encarga de
                # descartar el handle inválido, esperar que Windows vuelva a
                # enumerar el mismo COM y abrirlo de nuevo.
                print(f"[USB] LECTOR CAÍDO leyendo {self.port}: "
                      f"{type(e).__name__}: {e}")
                if self._watchdog_reconnect_usb():
                    # _connect_sync() ya arrancó un reader NUEVO. Este hilo
                    # viejo debe terminar para que nunca haya dos readers sobre
                    # el mismo puerto.
                    return
                self._run = False
                return

            if not data:
                # Avisar una sola vez que el stream se cortó, con cuánto hace.
                mudo = time.monotonic() - sin_datos_desde
                if mudo > 5 and not aviso_mudo:
                    print(f"[USB] Sin datos hace {mudo:.0f}s (puerto abierto)")
                    aviso_mudo = True

                # Segundo caso del watchdog: PM previamente operativo + COM
                # todavía abierto + 20 s sin datos. El primer caso (COM abre
                # pero el PM nunca transmite al conectar) se maneja dentro de
                # _connect_sync().
                if mudo >= WATCHDOG_SILENCE_SEC:
                    if self._watchdog_recover():
                        # _connect_sync() ya arrancó un hilo lector NUEVO.
                        # Este hilo viejo no debe seguir leyendo el mismo COM.
                        return
                    # Relay ausente/fallo previo: dejar un período completo antes
                    # de volver a intentarlo para no inundar el log.
                    #
                    # Sólo se reinicia el anti-spam del log. `_ultimo_dato` NO
                    # se toca: alimenta `lastFrameSecondsAgo` del /health, y
                    # pisarlo acá haría que la edad oscilara entre 0 y 20s con
                    # el equipo muerto hace horas, dando `device_ok` en verde
                    # parte del tiempo. Justo en el escenario donde el watchdog
                    # no puede recuperarse solo, el /health tiene que decir la
                    # verdad para que intervenga una persona.
                    sin_datos_desde = time.monotonic()
                    aviso_mudo = False
                continue

            sin_datos_desde = time.monotonic()
            self._ultimo_dato = sin_datos_desde
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
