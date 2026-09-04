"""Log a archivo, además de la consola.

Temporal, para diagnosticar el arranque donde no hay una consola a la vista:
autostart de Windows, pruebas en VirtualBox, reinicios sin nadie mirando.

No cambia ningún `print()` de la app: se envuelven `sys.stdout` y `sys.stderr`,
así que todo lo que ya se imprime queda también en el archivo, con hora. Eso
incluye los prints que salen de otros hilos — el lector serie y los callbacks
de Pusher — que son justamente los que interesan para este diagnóstico.

Además engancha los dos `excepthook`:

* `sys.excepthook` — excepciones que matan el proceso.
* `threading.excepthook` — excepciones que matan un hilo. Es el importante:
  el hilo lector de `serial_manager._loop()` muere en silencio si se desenchufa
  el equipo, y hoy no queda rastro de eso en ningún lado.

El archivo vive al lado de `credentials.json`:

    %APPDATA%\\BerryMed Monitor\\logs\\berry-monitor.log
"""

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from config import get_app_data_path

MAX_BYTES = 2 * 1024 * 1024   # 2 MB por archivo
BACKUPS = 3                   # + 3 rotados = 8 MB como mucho


def log_path(name: str = "berry-monitor") -> Path:
    return get_app_data_path() / "logs" / f"{name}.log"


class _Tee:
    """Escribe en el stream original y en el log, una entrada por línea.

    `print()` hace varias llamadas a `write()` por línea (el texto y después el
    salto), así que se acumula en un buffer y se emite recién al cerrar la
    línea. El lock es porque escriben varios hilos a la vez.
    """

    def __init__(self, stream, logger, level):
        self._stream = stream
        self._logger = logger
        self._level = level
        self._buf = ""
        self._lock = threading.Lock()

    def write(self, text):
        # La consola primero: si el log falla, no queremos perder también esto.
        if self._stream is not None:
            try:
                self._stream.write(text)
            except Exception:
                pass

        try:
            with self._lock:
                self._buf += text
                while "\n" in self._buf:
                    linea, self._buf = self._buf.split("\n", 1)
                    if linea.strip():
                        self._logger.log(self._level, linea)
        except Exception:
            # Loguear nunca puede romper a quien imprime.
            pass
        return len(text)

    def flush(self):
        if self._stream is not None:
            try:
                self._stream.flush()
            except Exception:
                pass

    def isatty(self):
        return getattr(self._stream, "isatty", lambda: False)()


def _install_excepthooks(logger):
    anterior_sys = sys.excepthook
    anterior_hilo = threading.excepthook

    # Los hooks sólo marcan el evento; el traceback lo imprime el hook por
    # defecto, que escribe a `sys.stderr` — o sea, al tee — y así queda en el
    # archivo una sola vez y también en la consola. Loguearlo con exc_info acá
    # además lo duplicaría.
    def sys_hook(tipo, valor, tb):
        logger.error(">>> EXCEPCIÓN NO CAPTURADA (el proceso termina)")
        anterior_sys(tipo, valor, tb)

    def hilo_hook(args):
        # Sin esto, un hilo que muere no deja rastro en ningún lado. Es el caso
        # del lector serie cuando se desenchufa el equipo: la app sigue viva y
        # aparentemente conectada, pero ya no recibe nada.
        logger.error(">>> HILO MUERTO POR EXCEPCIÓN: %s",
                     getattr(args.thread, "name", "?"))
        anterior_hilo(args)

    sys.excepthook = sys_hook
    threading.excepthook = hilo_hook


def setup(name: str = "berry-monitor") -> Optional[Path]:
    """Empieza a duplicar la salida al archivo. Devuelve la ruta, o None.

    Es best-effort a propósito: que no se pueda escribir el log nunca puede
    impedir que la app arranque.
    """
    try:
        ruta = log_path(name)
        ruta.parent.mkdir(parents=True, exist_ok=True)

        handler = RotatingFileHandler(
            ruta, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(threadName)s] %(message)s",
                              datefmt="%Y-%m-%d %H:%M:%S")
        )

        logger = logging.getLogger(f"berry.{name}")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.handlers.clear()
        logger.addHandler(handler)

        sys.stdout = _Tee(sys.stdout, logger, logging.INFO)
        sys.stderr = _Tee(sys.stderr, logger, logging.ERROR)
        _install_excepthooks(logger)

        # Separador bien visible: en el archivo se acumulan varios arranques y
        # hay que poder ver de un vistazo dónde empieza cada uno.
        logger.info("=" * 60)
        logger.info("ARRANQUE de %s", name)
        return ruta
    except Exception as exc:
        try:
            print(f"[WARN] No se pudo abrir el archivo de log: {exc}")
        except Exception:
            pass
        return None
