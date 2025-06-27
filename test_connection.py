#!/usr/bin/env python3
"""
pm6750_live_monitor.py
======================

Conecta al monitor BerryMed PM6750 (USB o Bluetooth‑SPP) y muestra en
tiempo real los parámetros vitales recibidos.

Uso:
    python pm6750_live_monitor.py --port COM3
    python pm6750_live_monitor.py --port /dev/ttyUSB0

Opciones:
    --baudrate  Velocidad (default 115200).
    --verbose   Muestra todos los paquetes brutos.

© 2025
"""

import argparse
import serial
import sys
import time
from datetime import datetime
from typing import Optional

HEADER = b"\x55\xAA"

def calc_checksum(length: int, payload: bytes) -> int:
    return (~(length + sum(payload)) & 0xFF)

def build_command(cmd_id: int, param: int) -> bytes:
    payload = bytes([cmd_id, param])
    length = len(payload) + 2
    checksum = calc_checksum(length, payload)
    return HEADER + bytes([length]) + payload + bytes([checksum])

def parse_param_packet(pkt_type: int, data: bytes) -> Optional[str]:
    """Devuelve una línea formateada lista para imprimir o None."""
    if pkt_type == 0x04 and len(data) >= 3:            # SpO2 Param
        status, spo2, pulse = data[:3]
        if status == 0x00:
            return f"[SPO2]  {spo2:3d} %  |  Pulse {pulse:3d} bpm"
    elif pkt_type == 0x03 and len(data) >= 5:          # NIBP Param
        status = data[0]
        cuff_pressure = data[1] * 2
        sys_p = data[2]
        mean_p = data[3]
        dia_p = data[4]
        return (f"[NIBP]  SYS:{sys_p}  DIA:{dia_p}  MAP:{mean_p}  " 
                f"Cuff:{cuff_pressure} mmHg  Status:0x{status:02X}")
    elif pkt_type == 0x05 and len(data) >= 3:          # TEMP Param
        status = data[0]
        if status == 0x00:
            temp_c = data[1] + data[2] / 10
            return f"[TEMP] {temp_c:4.1f} °C"
    elif pkt_type == 0x02 and len(data) >= 5:          # ECG Param
        hr = data[1]
        rr = data[2]
        st = int.from_bytes(bytes([data[3]]), byteorder='big', signed=True) / 100
        return f"[ECG]  HR:{hr:3d} bpm  RR:{rr:3d} bpm  ST:{st:+.2f} mV"
    return None

def main():
    ap = argparse.ArgumentParser(description="Monitor en vivo PM6750")
    ap.add_argument("--port", required=True)
    ap.add_argument("--baudrate", type=int, default=115200)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    try:
        ser = serial.Serial(
            port=args.port,
            baudrate=args.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=1,
        )
    except serial.SerialException as exc:
        print(f"No se pudo abrir {args.port}: {exc}")
        sys.exit(2)

    print(f"Conectado a {args.port} ({args.baudrate} baud).  Ctrl‑C para salir.")

    # Activar salidas de parámetros
    for cmd_id in (0x01, 0x03, 0x04):  # ECG, SpO2, Temp
        print(f"Enviando comando: {cmd_id}")
        ser.write(build_command(cmd_id, 0x01))
        ser.flush()
    # NIBP se inicia manualmente; aquí solo activamos salida de estatics si hubiera.

    buffer = bytearray()
    try:
        while True:
            data = ser.read(128)
            if data:
                buffer.extend(data)
                # sync
                while len(buffer) >= 3:
                    if buffer[0:2] != HEADER:
                        buffer.pop(0)
                        continue
                    length = buffer[2]
                    frame_len = length + 3
                    if len(buffer) < frame_len:
                        break
                    frame = bytes(buffer[:frame_len])
                    buffer = buffer[frame_len:]

                    payload = frame[3:-1]
                    pkt_type = payload[0]
                    body = payload[1:]
                    line = parse_param_packet(pkt_type, body)
                    if line:
                        ts = datetime.now().strftime("%H:%M:%S")
                        print(f"{ts}  {line}")
                    elif args.verbose:
                        print(f"RAW  {frame.hex()}")
            else:
                time.sleep(0.05)
    except KeyboardInterrupt:
        print("\nBye!")
    finally:
        ser.close()

if __name__ == "__main__":
    main()
