#!/usr/bin/env python3
"""Graba capturas del Berry, una por escenario, para el set de tests.

CORRE EN WINDOWS, con el equipo enchufado. Es lo unico de todo esto que
necesita hardware: los tests que despues consumen estas capturas corren en
cualquier lado.

    python tools\\capturar_escenarios.py --puerto COM3

Te va pidiendo cada maniobra, graba, y te dice EN EL MOMENTO si la captura
sirve —parsea los bytes con el parser de produccion y verifica el estado que
esperaba ese escenario—. Si salio mal ofrece repetirla ahi mismo, que es la
diferencia entre perder 30 segundos y volver a enchufar todo otro dia.

Los .bin quedan en tests/capturas/ junto con un manifest.json, y a partir de
ahi `pytest` los usa solo.

    python tools\\capturar_escenarios.py --puerto COM3 --solo ocioso
    python tools\\capturar_escenarios.py --puerto COM3 --desde nibp_medicion
    python tools\\capturar_escenarios.py --listar
"""

import argparse
import datetime as dt
import json
import os
import sys
import time
from unittest.mock import patch

# La consola de Windows no siempre habla UTF-8 y los textos de las maniobras
# llevan acentos: sin esto, un print revienta con UnicodeEncodeError a mitad de
# la sesion de captura.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from src.data_parser import BMDataParser              # noqa: E402
from src.pm6750_protocol import build_command, build_startup_commands  # noqa: E402
from tests.escenarios import ESCENARIOS, POR_ID, advertencias  # noqa: E402

DESTINO = os.path.join(REPO, "tests", "capturas")
MANIFEST = os.path.join(DESTINO, "manifest.json")

RAYA = "-" * 68


def _config_del_equipo():
    """Config real del totem, si esta instalada, para capturar con la misma
    ganancia y el mismo modo con los que corre en produccion."""
    try:
        from config import get_config
        return get_config() or {}
    except Exception:
        return {}


def _abrir(puerto, baud, cfg):
    import serial
    ser = serial.Serial(puerto, baud, timeout=0.3)
    print(f"Puerto {puerto} abierto a {baud} baudios.")

    datos = b"".join(build_command(a1, a2)
                     for a1, a2 in build_startup_commands(cfg))
    ser.write(datos)
    ser.flush()
    print(f"Enables enviados ({len(datos)} bytes): {datos.hex()}")

    # Sin esto la primera captura arranca con lo que ya venia en el buffer del
    # sistema, que puede ser de antes de mandar los enables.
    time.sleep(1.0)
    ser.reset_input_buffer()
    return ser


# Segundos que se graban antes de permitir un corte anticipado. Sin esto, un
# `hasta()` que mira el estado del equipo podria darse por satisfecho con el
# primer paquete, que todavia describe el estado ANTERIOR al comando.
MINIMO_ANTES_DE_CORTAR = 5.0


def _grabar(ser, segundos, hasta=None):
    """Lee del puerto hasta `segundos`, o hasta que `hasta(parser)` diga basta.

    Va parseando lo que llega, en vez de esperar al final, por dos razones: es
    lo que permite cortar cuando la medicion de presion ya termino en vez de
    esperar el tope entero, y es lo que deja mostrar en vivo que el equipo esta
    mandando algo con sentido.
    """
    crudo = bytearray()
    parser = BMDataParser()
    inicio = time.monotonic()
    fin = inicio + segundos
    corto_solo = False

    while True:
        ahora = time.monotonic()
        restante = fin - ahora
        if restante <= 0:
            break
        d = ser.read(512)
        if d:
            crudo.extend(d)
            parser.add_data(bytearray(d))
        if (hasta is not None
                and ahora - inicio >= MINIMO_ANTES_DE_CORTAR
                and hasta(parser)):
            corto_solo = True
            break
        print(f"\r  grabando... {restante:4.1f}s  {len(crudo):7d} bytes",
              end="", flush=True)

    transcurrido = time.monotonic() - inicio
    cierre = " (el equipo termino solo)" if corto_solo else ""
    print(f"\r  listo: {len(crudo)} bytes en {transcurrido:.0f}s{cierre}"
          f"{' ' * 20}")
    return bytes(crudo)


class _LoopQuieto:
    """Event loop de mentira con el reloj detenido. Ver el mismo truco en
    tests/test_capturas.py: sin esto, el recuento de muestras de onda cambia
    segun si el parseo cruzo un cambio de segundo, y el aviso de saturacion
    daria un porcentaje distinto cada vez sobre los mismos bytes."""

    def time(self):
        return 0.0


def _analizar(crudo):
    """Pasa los bytes por el parser de produccion y devuelve lo que entendio."""
    parser = BMDataParser()
    with patch("asyncio.get_event_loop", return_value=_LoopQuieto()):
        parser.add_data(bytearray(crudo))
    return parser


def _resumen(parser):
    estados = {s: d["status"] for s, d in parser.sensor_status.items()}
    muestras = sum(len(v) for v in parser.data["ecg"].values())
    return estados, muestras


def _accion(ser, cfg, funcion, etiqueta):
    """Corre una accion del escenario sobre el equipo, sin dejar que un fallo
    tumbe la sesion de captura."""
    if funcion is None:
        return
    try:
        detalle = funcion(ser, cfg)
        print(f"  [equipo] {detalle or etiqueta}")
    except Exception as e:
        print(f"  [equipo] fallo {etiqueta}: {type(e).__name__}: {e}")


def _capturar_uno(ser, cfg, esc, forzar=False):
    """Devuelve la entrada de manifest, o None si se salteo."""
    print()
    print(RAYA)
    print(f"[{esc.id}]  {esc.titulo}")
    print(RAYA)
    print(f"  Para que sirve: {esc.porque}")
    print()
    # El punto de partida va primero y siempre, aunque venga encadenado del
    # escenario anterior: es el paso que se olvida, y sin el la captura sale
    # con una sonda todavia desenchufada de la maniobra de antes.
    print(f"  1. DEJA ASI EL EQUIPO: {esc.punto_de_partida}")
    print(f"  2. Y AHORA: {esc.maniobra}")
    if esc.hasta:
        print(f"  Duracion: hasta {esc.segundos}s, corta sola al terminar")
    else:
        print(f"  Duracion: {esc.segundos}s")
    if esc.preparar:
        print("  La medicion la arranca esta herramienta al apretar ENTER.")
    print()

    while True:
        r = input("  ENTER para grabar, 's' para saltear: ").strip().lower()
        if r == "s":
            print("  salteado.")
            return None

        try:
            _accion(ser, cfg, esc.preparar, "preparacion")
            crudo = _grabar(ser, esc.segundos, esc.hasta)
        finally:
            # Pase lo que pase: el manguito no se queda inflado.
            _accion(ser, cfg, esc.al_terminar, "cierre")

        parser = _analizar(crudo)
        estados, muestras = _resumen(parser)
        problemas = esc.verificar(parser)
        avisos = advertencias(parser, esc)

        print(f"  Sensores que informaron: {estados or 'ninguno'}")
        print(f"  Muestras de ECG: {muestras}")

        if avisos:
            for a in avisos:
                print(f"  >> OJO: {a}")

        if problemas:
            print("  >> LA CAPTURA NO ES LA QUE ESPERABAMOS:")
            for p in problemas:
                print(f"     - {p}")
            if not forzar:
                otra = input("  Repetir? [S/n]: ").strip().lower()
                if otra in ("", "s", "si"):
                    continue
                guardar = input("  Guardarla igual? [s/N]: ").strip().lower()
                if guardar not in ("s", "si"):
                    print("  descartada.")
                    return None
        elif avisos and not forzar:
            print("  La captura tiene lo que el escenario pedia, pero mira las "
                  "advertencias de arriba.")
            otra = input("  Repetir? [s/N]: ").strip().lower()
            if otra in ("s", "si"):
                continue
        else:
            print("  >> OK: la captura tiene lo que el escenario pedia.")

        archivo = os.path.join(DESTINO, f"{esc.id}.bin")
        with open(archivo, "wb") as f:
            f.write(crudo)
        print(f"  guardada en tests/capturas/{esc.id}.bin")

        return {
            "id": esc.id,
            "titulo": esc.titulo,
            "grabada": dt.datetime.now().isoformat(timespec="seconds"),
            "segundos": esc.segundos,
            "bytes": len(crudo),
            "sensores": estados,
            "muestrasEcg": muestras,
            "verificacionOk": not problemas,
            "problemas": problemas,
            "advertencias": avisos,
        }


def _cargar_manifest():
    if os.path.exists(MANIFEST):
        try:
            with open(MANIFEST, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--puerto", default="COM3", help="COM3, COM6, /dev/ttyACM0...")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--solo", metavar="ID", help="graba un solo escenario")
    ap.add_argument("--desde", metavar="ID", help="retoma desde ese escenario")
    ap.add_argument("--forzar", action="store_true",
                    help="guarda aunque la verificacion falle, sin preguntar")
    ap.add_argument("--listar", action="store_true", help="lista los escenarios")
    args = ap.parse_args()

    if args.listar:
        for e in ESCENARIOS:
            print(f"  {e.id:26s} {e.segundos:3d}s  {e.titulo}")
        return 0

    pendientes = list(ESCENARIOS)
    if args.solo:
        if args.solo not in POR_ID:
            print(f"No existe el escenario '{args.solo}'. Ver --listar.")
            return 2
        pendientes = [POR_ID[args.solo]]
    elif args.desde:
        ids = [e.id for e in ESCENARIOS]
        if args.desde not in ids:
            print(f"No existe el escenario '{args.desde}'. Ver --listar.")
            return 2
        pendientes = pendientes[ids.index(args.desde):]

    os.makedirs(DESTINO, exist_ok=True)
    total_seg = sum(e.segundos for e in pendientes)

    print(RAYA)
    print(f"Captura de escenarios: {len(pendientes)} en total, "
          f"~{total_seg}s de grabacion mas el tiempo de las maniobras.")
    print("Ctrl+C corta en cualquier momento; lo grabado hasta ahi queda.")
    print(RAYA)

    cfg = _config_del_equipo()
    try:
        ser = _abrir(args.puerto, args.baud, cfg)
    except Exception as e:
        print(f"No se pudo abrir {args.puerto}: {type(e).__name__}: {e}")
        print("Revisa que el equipo este enchufado y que el COM sea el correcto.")
        return 1

    manifest = _cargar_manifest()
    try:
        for esc in pendientes:
            entrada = _capturar_uno(ser, cfg, esc, forzar=args.forzar)
            if entrada:
                manifest[esc.id] = entrada
                with open(MANIFEST, "w", encoding="utf-8") as f:
                    json.dump(manifest, f, indent=2, ensure_ascii=False)
    except KeyboardInterrupt:
        print("\n\nCortado a mano.")
        # Ctrl+C en mitad de una medicion deja el manguito inflado si nadie
        # manda el stop. `_capturar_uno` ya lo cubre con su `finally`, pero se
        # repite acá por si la interrupcion cae fuera de ese bloque.
        for esc in pendientes:
            _accion(ser, cfg, esc.al_terminar, "cierre de emergencia")
    finally:
        try:
            ser.close()
        except Exception:
            pass

    print()
    print(RAYA)
    print("RESUMEN")
    print(RAYA)
    for e in ESCENARIOS:
        entrada = manifest.get(e.id)
        if not entrada:
            estado = "FALTA"
        elif entrada["verificacionOk"]:
            estado = "ok"
        else:
            estado = "guardada con reparos"
        print(f"  {e.id:26s} {estado}")
    faltan = [e.id for e in ESCENARIOS if e.id not in manifest]
    print()
    if faltan:
        print("Faltan: " + ", ".join(faltan))
        print("Se pueden grabar despues con --solo <id>.")
    else:
        print("Estan todas. Ahora, en cualquier maquina:  pytest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
