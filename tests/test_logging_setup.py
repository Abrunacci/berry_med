"""El log repite la cabecera del build en cada archivo nuevo.

El log rota cada 2 MB y guarda tres archivos viejos: en un tótem que corre
días, la línea del arranque desaparece enseguida. Sin la cabecera repetida, el
log no diría qué build está corriendo.
"""

import logging

from src.logging_setup import _Rotativo


def test_cada_archivo_del_log_empieza_con_la_cabecera(tmp_path):
    ruta = tmp_path / "berry-monitor.log"
    handler = _Rotativo(ruta, maxBytes=300, backupCount=3, encoding="utf-8",
                        cabecera="[BUILD] berry-monitor 1.0.9 | Python 3.13.5")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("prueba.rotacion")
    logger.propagate = False
    logger.handlers[:] = [handler]
    logger.setLevel(logging.INFO)
    for i in range(80):
        logger.info("linea %03d, con relleno para que rote rapido", i)
    handler.close()

    archivos = [ruta] + [ruta.with_name(f"berry-monitor.log.{n}") for n in (1, 2, 3)]
    for archivo in archivos:
        primera = archivo.read_text(encoding="utf-8").splitlines()[0]
        assert primera.startswith("[BUILD] berry-monitor 1.0.9"), archivo.name


def test_sin_cabecera_rota_como_siempre(tmp_path):
    ruta = tmp_path / "x.log"
    handler = _Rotativo(ruta, maxBytes=200, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("prueba.sin_cabecera")
    logger.propagate = False
    logger.handlers[:] = [handler]
    logger.setLevel(logging.INFO)
    for i in range(30):
        logger.info("linea %03d con relleno", i)
    handler.close()
    assert not ruta.read_text(encoding="utf-8").startswith("[BUILD]")
