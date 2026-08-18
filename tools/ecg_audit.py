#!/usr/bin/env python3
"""
Auditoría de ECG del Berry.

Objetivo (requisito médico): confirmar cuántas muestras de ECG envía el Berry
en cada paquete de onda (type 0x01) y verificar cuántas de esas muestras
conserva/envía realmente la aplicación. Si el Berry manda 5, tenemos que mandar
las 5.

Qué hace:
  1. Toma el stream crudo del Berry (USB en vivo, o un archivo .bin capturado).
  2. Reconstruye las tramas EXACTAMENTE con la misma lógica del parser de
     producción (cabecera 0x55 0xAA, byte de longitud, checksum).
  3. Cuenta, para los paquetes de ECG (0x01 onda, 0x02 params, 0x30 pico):
       - cuántas muestras trae cada paquete de onda,
       - la distribución de muestras/paquete,
       - la frecuencia (paquetes/seg y muestras/seg) en modo vivo.
  4. Pasa los MISMOS bytes por el BMDataParser real y cuenta cuántas muestras
     de ECG termina reteniendo la app, para medir la pérdida real.

Uso:
  # Captura y auditoría en vivo por USB (Ctrl+C para terminar):
  python tools/ecg_audit.py --port COM3

  # 30 segundos, guardando el crudo para re-analizar después:
  python tools/ecg_audit.py --port COM3 --seconds 30 --record captura.bin

  # Analizar offline una captura previa:
  python tools/ecg_audit.py --file captura.bin

Nota: NO modifica el código de producción. Es una herramienta aparte.
"""

import argparse
import os
import signal
import sys
import time
from collections import Counter

# Permitir importar el parser real de producción (src es namespace package,
# igual que lo usa app.py). Agregamos la raíz del repo al path.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# --- Constantes de protocolo (idénticas al parser de producción) -----------
HEADER = (0x55, 0xAA)
PACKAGE_MIN_LENGTH = 4

# Tipos relacionados con ECG (ver src/data_parser.py callbacks)
ECG_WAVE = 0x01   # onda de ECG  -> es la que importa para "las 5 muestras"
ECG_PARAMS = 0x02  # frecuencia cardíaca / respiratoria
ECG_PEAK = 0x30   # marca de pico (hoy ignorada por app.py)

ECG_TYPES = {ECG_WAVE, ECG_PARAMS, ECG_PEAK}

# Tope y comportamiento del pipeline actual (src/data_parser.py)
CURRENT_SAMPLES_PER_PACKET = 1     # el parser toma solo package[4]
MAX_WAVEFORM_POINTS = 25           # tope por segundo de data["ecg"]


def outgoing_cmd(a1: int, a2: int) -> bytes:
    """Comando de habilitación de stream (mismo formato que serial_manager)."""
    payload = bytes([a1, a2])
    n = len(payload) + 2
    checksum = (~(n + sum(payload)) & 0xFF) & 0xFF
    return bytes([HEADER[0], HEADER[1], n]) + payload + bytes([checksum])


def checksum_ok(package: list) -> bool:
    """Misma validación que BMDataParser._check_sum."""
    return (~sum(package[2:-1]) & 0xFF) == package[-1]


class Reframer:
    """
    Reconstruye tramas desde un stream de bytes usando EXACTAMENTE la lógica de
    BMDataParser.add_data (start + length + 2), para reflejar lo que el parser
    de producción realmente ve.
    """

    def __init__(self):
        self.buf = []

    def feed(self, data: bytes):
        """Devuelve una lista de paquetes (cada uno como lista de ints)."""
        self.buf.extend(data)
        packages = []

        while len(self.buf) >= PACKAGE_MIN_LENGTH:
            start = self._find_start()
            if start == -1:
                self.buf = self.buf[-1:]
                break

            length = self.buf[start + 2]
            end = start + length + 2
            if end > len(self.buf):
                break

            package = self.buf[start:end]
            self.buf = self.buf[end:]
            packages.append(package)

        return packages

    def _find_start(self) -> int:
        for i in range(len(self.buf) - 1):
            if self.buf[i] == HEADER[0] and self.buf[i + 1] == HEADER[1]:
                return i
        return -1


class ECGAudit:
    def __init__(self):
        # Transporte: cuántos bytes/tramas llegan realmente
        self.raw_bytes = 0
        self.total_frames = 0
        self.type_hist = Counter()     # type -> cantidad de tramas (checksum ok)
        self.first_raw = bytearray()   # primeros bytes crudos para hexdump

        # Contadores crudos (lo que el Berry ENVÍA)
        self.wave_packets = 0
        self.wave_samples_total = 0          # suma de muestras crudas
        self.samples_per_packet = Counter()  # distribución: N muestras -> veces
        self.params_packets = 0
        self.peak_packets = 0
        self.bad_checksum = 0
        self.non_ecg_packets = 0

        # Muestras de valores para inspección visual
        self.sample_examples = []

        # Buckets por segundo (solo modo vivo, con timestamps de llegada)
        self._sec_bucket_wave_packets = Counter()  # segundo -> #paquetes onda
        self._sec_bucket_wave_samples = Counter()  # segundo -> #muestras crudas

        # Parser REAL de producción para medir lo que la app conserva
        self._parser = None
        self._app_wave_callbacks = 0
        self._init_real_parser()

        self._t0 = None
        self._t_last = None

    def _init_real_parser(self):
        try:
            from src.data_parser import BMDataParser

            self._parser = BMDataParser()
            # Cada vez que la app procesa una muestra de onda ECG, este
            # callback se dispara UNA vez por paquete (con package[4]).
            self._parser.register_callback(
                "on_ecg_waveform_received", self._on_app_wave
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[AUDIT][WARN] No se pudo cargar BMDataParser real: {exc}")
            print("[AUDIT][WARN] Se continúa solo con el análisis crudo.")

    def _on_app_wave(self, value):
        self._app_wave_callbacks += 1

    # ---- ingesta -----------------------------------------------------------
    def note_raw(self, data: bytes):
        """Registra bytes crudos recibidos, independiente del framing."""
        self.raw_bytes += len(data)
        if len(self.first_raw) < 64:
            self.first_raw.extend(data[: 64 - len(self.first_raw)])

    def ingest_package(self, package: list, arrival: float):
        if self._t0 is None:
            self._t0 = arrival
        self._t_last = arrival
        self.total_frames += 1

        if not checksum_ok(package):
            self.bad_checksum += 1
            return

        ptype = package[3]
        self.type_hist[ptype] += 1
        payload = package[4:-1]  # bytes de datos (sin header/len/type/checksum)

        if ptype == ECG_WAVE:
            n_samples = len(payload)
            self.wave_packets += 1
            self.wave_samples_total += n_samples
            self.samples_per_packet[n_samples] += 1
            if len(self.sample_examples) < 10:
                self.sample_examples.append(list(payload))
            sec = int(arrival - self._t0)
            self._sec_bucket_wave_packets[sec] += 1
            self._sec_bucket_wave_samples[sec] += n_samples
        elif ptype == ECG_PARAMS:
            self.params_packets += 1
        elif ptype == ECG_PEAK:
            self.peak_packets += 1
        else:
            self.non_ecg_packets += 1

    def feed_to_real_parser(self, raw: bytes):
        if self._parser is not None:
            self._parser.add_data(bytearray(raw))

    # ---- reporte -----------------------------------------------------------
    def report(self):
        dur = (self._t_last - self._t0) if (self._t0 and self._t_last) else 0.0
        line = "=" * 68
        print("\n" + line)
        print("AUDITORÍA DE ECG DEL BERRY — RESULTADO")
        print(line)

        print("\n[0] Transporte (¿llega algo del device?)")
        print(f"    Bytes crudos recibidos:  {self.raw_bytes}")
        print(f"    Tramas 55AA reconstruidas: {self.total_frames}")
        if self.type_hist:
            types = " ".join(f"0x{t:02X}={c}" for t, c in
                             sorted(self.type_hist.items()))
            print(f"    Tipos de trama vistos:   {types}")
        if self.first_raw:
            print(f"    Primeros bytes (hex):    {self.first_raw.hex()}")
        if self.raw_bytes == 0:
            print("    ⚠️  CERO bytes: el device no está transmitiendo nada.")

        print("\n[1] Lo que el Berry ENVÍA (análisis crudo de tramas)")
        print(f"    Paquetes de onda ECG (0x01):     {self.wave_packets}")
        print(f"    Muestras crudas totales de onda: {self.wave_samples_total}")
        print(f"    Paquetes de params (0x02, HR/RR): {self.params_packets}")
        print(f"    Paquetes de pico  (0x30):        {self.peak_packets}")
        print(f"    Paquetes con checksum inválido:  {self.bad_checksum}")

        print("\n[2] Muestras por paquete de onda (LA PREGUNTA CLAVE)")
        if not self.samples_per_packet:
            print("    Sin paquetes de onda de ECG capturados.")
        else:
            for n in sorted(self.samples_per_packet):
                cnt = self.samples_per_packet[n]
                pct = 100.0 * cnt / self.wave_packets
                print(f"    {n} muestra(s)/paquete: {cnt} paquetes ({pct:.1f}%)")
            distinct = sorted(self.samples_per_packet)
            if distinct == [1]:
                print("    → El Berry manda 1 muestra por paquete.")
            else:
                print(f"    → El Berry manda hasta {max(distinct)} muestras por "
                      f"paquete.")
            print("    Ejemplos de payload (bytes de muestra):")
            for ex in self.sample_examples[:5]:
                print(f"        {ex}")

        print("\n[3] Lo que la APP conserva hoy (BMDataParser real)")
        if self._parser is not None:
            print(f"    Callbacks de onda ECG disparados: "
                  f"{self._app_wave_callbacks}")
            print(f"    (= 1 muestra por paquete, toma solo package[4])")
        else:
            print("    Parser real no disponible; se usa el modelo del código:")
            print(f"    1 muestra/paquete → {self.wave_packets} muestras")

        kept = self.wave_packets * CURRENT_SAMPLES_PER_PACKET
        lost = self.wave_samples_total - kept
        print("\n[4] Pérdida a nivel de paquete (antes del tope de 25/seg)")
        print(f"    Recibidas:  {self.wave_samples_total} muestras")
        print(f"    Conservadas:{kept} muestras (1 por paquete)")
        if self.wave_samples_total > 0:
            loss_pct = 100.0 * lost / self.wave_samples_total
            print(f"    Perdidas:   {lost} muestras ({loss_pct:.1f}%)")

        # Segundo recorte: tope de 25 muestras/seg + reset por segundo
        if self._sec_bucket_wave_packets:
            capped = sum(min(p, MAX_WAVEFORM_POINTS)
                         for p in self._sec_bucket_wave_packets.values())
            print("\n[5] Segundo recorte: tope de "
                  f"{MAX_WAVEFORM_POINTS} muestras/seg (data['ecg'])")
            print(f"    Muestras que sobrevivirían el tope: {capped}")
            secs_over_cap = sum(
                1 for p in self._sec_bucket_wave_packets.values()
                if p > MAX_WAVEFORM_POINTS
            )
            print(f"    Segundos que exceden el tope de 25: {secs_over_cap}")

        if dur > 0:
            print("\n[6] Frecuencia (modo vivo)")
            print(f"    Duración:            {dur:.1f} s")
            print(f"    Paquetes onda/seg:   {self.wave_packets / dur:.1f}")
            print(f"    Muestras crudas/seg: "
                  f"{self.wave_samples_total / dur:.1f}")

        print("\n[7] VEREDICTO")
        distinct = sorted(self.samples_per_packet)
        if not distinct:
            print("    Sin datos de ECG. Verificá conexión, puerto y que se")
            print("    haya disparado el monitoreo.")
        elif distinct == [1]:
            print("    ✅ El Berry manda 1 muestra por paquete y la app la")
            print("    conserva. No hay pérdida a nivel de paquete.")
            print("    (Revisá igual el punto [5] por el tope de 25/seg.)")
        else:
            print(f"    ⚠️  El Berry manda hasta {max(distinct)} muestras por")
            print("    paquete, pero la app conserva SOLO 1 (package[4]).")
            print("    Se está PERDIENDO señal de ECG. Para cumplir el")
            print("    requisito médico hay que enviar todas las muestras del")
            print("    payload (package[4:-1]), no solo la primera.")
        print(line + "\n")


# --- Fuentes de datos -------------------------------------------------------
def run_file(path: str, audit: ECGAudit):
    print(f"[AUDIT] Analizando captura: {path}")
    with open(path, "rb") as f:
        raw = f.read()
    reframer = Reframer()
    now = 0.0  # sin timestamps reales en archivo; frecuencia no aplica
    audit.note_raw(raw)
    audit.feed_to_real_parser(raw)
    for pkg in reframer.feed(raw):
        audit.ingest_package(pkg, now)
    audit.report()


def run_usb(port, baud, seconds, record, audit, passive=False,
            dtr=None, rts=None):
    try:
        import serial
    except ImportError:
        print("[AUDIT][ERROR] pyserial no está instalado. `poetry install`.")
        sys.exit(1)

    try:
        ser = serial.Serial(port, baud, timeout=0.3)
    except Exception as exc:  # noqa: BLE001
        print(f"[AUDIT][ERROR] No se pudo abrir {port}: {exc}")
        sys.exit(1)

    print(f"[AUDIT] Puerto {port} @ {baud} abierto")

    # Control de líneas DTR/RTS solo si se pide explícitamente. Por defecto se
    # deja el estado que pone pyserial al abrir (= lo que hace producción).
    # OJO: en algunas placas STM32 la línea RTS está cableada al reset; forzar
    # RTS puede mantener el equipo en reset (0 bytes). Por eso es configurable.
    if dtr is not None:
        ser.setDTR(dtr)
        print(f"[AUDIT] DTR = {dtr}")
    if rts is not None:
        ser.setRTS(rts)
        print(f"[AUDIT] RTS = {rts}")
    if dtr is not None or rts is not None:
        time.sleep(0.3)

    if passive:
        print("[AUDIT] MODO PASIVO: no se envía ningún comando. Solo escucha.")
    else:
        # Habilitar streams idéntico a serial_manager.start_monitoring().
        # Se usa el mismo builder que la app para no volver a divergir; antes
        # acá iba un (0xFB, 1) inexistente en el protocolo (no-op).
        from src.pm6750_protocol import build_startup_commands
        for a1, a2 in build_startup_commands():
            n = ser.write(outgoing_cmd(a1, a2))
        print(f"[AUDIT] Comandos de enable enviados (último write={n} bytes)")
    print("[AUDIT] Capturando... (Ctrl+C para cortar)")

    reframer = Reframer()
    rec = open(record, "wb") if record else None
    stop = {"flag": False}

    def _sigint(_sig, _frm):
        stop["flag"] = True
    signal.signal(signal.SIGINT, _sigint)

    t_start = time.time()
    try:
        while not stop["flag"]:
            if seconds and (time.time() - t_start) >= seconds:
                break
            data = ser.read(256)
            if not data:
                continue
            audit.note_raw(data)
            if rec:
                rec.write(data)
            audit.feed_to_real_parser(data)
            arrival = time.time()
            for pkg in reframer.feed(data):
                audit.ingest_package(pkg, arrival)
    finally:
        try:
            ser.close()
        except Exception:  # noqa: BLE001
            pass
        if rec:
            rec.close()
            print(f"[AUDIT] Crudo guardado en: {record}")

    audit.report()


def main():
    ap = argparse.ArgumentParser(
        description="Auditoría de ECG del Berry (muestras por paquete y pérdida)."
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--port", help="Puerto USB del Berry (ej: COM3, /dev/ttyACM0)")
    src.add_argument("--file", help="Archivo .bin con captura cruda previa")
    ap.add_argument("--baud", type=int, default=115200, help="Baudios (def 115200)")
    ap.add_argument("--seconds", type=float, default=0,
                    help="Duración en vivo; 0 = hasta Ctrl+C")
    ap.add_argument("--record", default="",
                    help="Guardar el crudo en este archivo (modo vivo)")
    ap.add_argument("--passive", action="store_true",
                    help="No enviar ningún comando; solo escuchar")
    ap.add_argument("--dtr", choices=["on", "off"], default=None,
                    help="Forzar línea DTR (por defecto: no tocar)")
    ap.add_argument("--rts", choices=["on", "off"], default=None,
                    help="Forzar línea RTS (por defecto: no tocar)")
    args = ap.parse_args()

    dtr = None if args.dtr is None else (args.dtr == "on")
    rts = None if args.rts is None else (args.rts == "on")

    audit = ECGAudit()
    if args.file:
        run_file(args.file, audit)
    else:
        run_usb(args.port, args.baud, args.seconds, args.record, audit,
                passive=args.passive, dtr=dtr, rts=rts)


if __name__ == "__main__":
    main()
