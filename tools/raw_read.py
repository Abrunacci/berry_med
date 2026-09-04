#!/usr/bin/env python3
"""
Replica EXACTA de lo que hace serial_manager.start_monitoring():
abrir el puerto y mandar los comandos de activacion 55 AA .. 01, luego leer.
Nada de DTR/RTS/fases. Imprime lo que escribe y lo que llega, en vivo.

Uso:
    py tools\\raw_read.py COM6
    python3 tools/raw_read.py /dev/ttyACM0
"""
import sys
import time

import serial

HEADER = b"\x55\xAA"


def _cmd(a1, a2):
    payload = bytes([a1, a2])
    n = len(payload) + 2
    cs = (~(n + sum(payload)) & 0xFF) & 0xFF
    return HEADER + bytes([n]) + payload + bytes([cs])


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "COM6"
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else 115200
    secs = int(sys.argv[3]) if len(sys.argv) > 3 else 15

    ser = serial.Serial(port, baud, timeout=0.3)
    print(f"Abierto {port} @ {baud} | is_open={ser.is_open}")

    # === activacion: IDENTICO al start_monitoring actual (serial_manager.py) ===
    # Se usa el mismo builder que la app para no volver a divergir. Antes acá
    # había un (0xFB, 1) que no existe en el protocolo del PM6750: era un no-op.
    from src.pm6750_protocol import build_startup_commands
    enables = build_startup_commands()
    for a1, a2 in enables:
        frame = _cmd(a1, a2)
        written = ser.write(frame)
        print(f"  -> enviado {frame.hex()}  ({written} bytes)")
    ser.flush()
    print(f"Escuchando {secs}s...\n")

    total = 0
    t = time.time()
    while time.time() - t < secs:
        d = ser.read(256)
        if d:
            total += len(d)
            print(f"[RX {len(d):3d}] {d[:48].hex()}")
    ser.close()

    print("\n" + "-" * 50)
    print(f"TOTAL bytes recibidos: {total}")
    if total == 0:
        print("0 bytes: estos comandos NO activan este equipo, o COM6 no es")
        print("el puerto de datos. Necesitamos el comando de activacion correcto.")


if __name__ == "__main__":
    main()
