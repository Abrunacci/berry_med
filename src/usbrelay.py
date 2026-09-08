#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
USBRelay2 (DCTTech) - control directo desde Python en Windows
Sin librerías externas: usa solamente ctypes + HID/SetupAPI de Windows.

Placa:
    Product: USBRelay2
    VID:     0x16C0
    PID:     0x05DF

Uso:
    python usbrelay2.py status
    python usbrelay2.py on 1
    python usbrelay2.py off 1
    python usbrelay2.py restart 1 5
    python usbrelay2.py test 1

IMPORTANTE para conexión por NC:
    relay ON  = bobina energizada = NC abierto = dispositivo SIN 12 V
    relay OFF = bobina desenergizada = NC cerrado = dispositivo CON 12 V

El comando "restart" hace:
    ON -> espera 5 s -> OFF
"""

import ctypes
from ctypes import wintypes
import os
import sys
import time

VID = 0x16C0
PID = 0x05DF

# ---------------- Windows constants ----------------

DIGCF_PRESENT = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010

GENERIC_READ  = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ  = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
ERROR_NO_MORE_ITEMS = 259

# ---------------- Structures ----------------

class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]

# ---------------- DLLs ----------------

setupapi = ctypes.WinDLL("setupapi")
hid = ctypes.WinDLL("hid")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# HidD_GetHidGuid
hid.HidD_GetHidGuid.argtypes = [ctypes.POINTER(GUID)]
hid.HidD_GetHidGuid.restype = None

# HidD_SetFeature
hid.HidD_SetFeature.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
hid.HidD_SetFeature.restype = wintypes.BOOLEAN

# HidD_GetFeature
hid.HidD_GetFeature.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
hid.HidD_GetFeature.restype = wintypes.BOOLEAN

# SetupDiGetClassDevsW
setupapi.SetupDiGetClassDevsW.argtypes = [
    ctypes.POINTER(GUID),
    wintypes.LPCWSTR,
    wintypes.HWND,
    wintypes.DWORD,
]
setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE

# SetupDiEnumDeviceInterfaces
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.POINTER(GUID),
    wintypes.DWORD,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
]
setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL

# SetupDiGetDeviceInterfaceDetailW
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.c_void_p,
]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL

setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL

# CreateFileW
kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]
kernel32.CreateFileW.restype = wintypes.HANDLE

kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


def winerr(prefix):
    err = ctypes.get_last_error()
    return OSError(err, f"{prefix}: {ctypes.FormatError(err).strip()}")


def find_usbrelay2_path():
    """Busca el primer HID cuyo path contenga VID_16C0&PID_05DF."""
    hid_guid = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(hid_guid))

    devinfo = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(hid_guid),
        None,
        None,
        DIGCF_PRESENT | DIGCF_DEVICEINTERFACE,
    )

    if devinfo == INVALID_HANDLE_VALUE:
        raise winerr("SetupDiGetClassDevsW")

    target = f"vid_{VID:04x}&pid_{PID:04x}".lower()

    try:
        index = 0
        while True:
            interface_data = SP_DEVICE_INTERFACE_DATA()
            interface_data.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)

            ok = setupapi.SetupDiEnumDeviceInterfaces(
                devinfo,
                None,
                ctypes.byref(hid_guid),
                index,
                ctypes.byref(interface_data),
            )

            if not ok:
                err = ctypes.get_last_error()
                if err == ERROR_NO_MORE_ITEMS:
                    break
                raise winerr("SetupDiEnumDeviceInterfaces")

            required = wintypes.DWORD(0)

            # Primera llamada: obtener tamaño requerido.
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                devinfo,
                ctypes.byref(interface_data),
                None,
                0,
                ctypes.byref(required),
                None,
            )

            if required.value:
                buf = ctypes.create_string_buffer(required.value)

                # SP_DEVICE_INTERFACE_DETAIL_DATA_W.cbSize:
                # 8 bytes en Windows x64; 6 bytes en Windows x86.
                cbsize = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
                ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0] = cbsize

                ok = setupapi.SetupDiGetDeviceInterfaceDetailW(
                    devinfo,
                    ctypes.byref(interface_data),
                    buf,
                    required.value,
                    ctypes.byref(required),
                    None,
                )

                if ok:
                    # DevicePath comienza a offset 4 (después de DWORD cbSize).
                    path = ctypes.wstring_at(ctypes.addressof(buf) + 4)

                    if target in path.lower():
                        return path

            index += 1

    finally:
        setupapi.SetupDiDestroyDeviceInfoList(devinfo)

    return None


def open_relay():
    path = find_usbrelay2_path()

    if not path:
        raise RuntimeError(
            "No se encontró USBRelay2 VID=16C0 PID=05DF.\n"
            "Verifique que la placa esté conectada y reconocida por Windows."
        )

    handle = kernel32.CreateFileW(
        path,
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        0,
        None,
    )

    if handle == INVALID_HANDLE_VALUE:
        raise winerr("No se pudo abrir USBRelay2")

    return handle, path


def set_relay(relay_number, enabled):
    """
    enabled=True  -> comando 0xFF -> relay ON
    enabled=False -> comando 0xFD -> relay OFF

    Reporte HID:
        byte 0: report ID = 0
        byte 1: comando
        byte 2: número de relay
        byte 3..8: 0
    """
    if relay_number not in (1, 2):
        raise ValueError("El número de relay debe ser 1 o 2.")

    handle, _ = open_relay()

    try:
        report = (ctypes.c_ubyte * 9)()
        report[0] = 0x00
        report[1] = 0xFF if enabled else 0xFD
        report[2] = relay_number

        ok = hid.HidD_SetFeature(
            handle,
            ctypes.byref(report),
            ctypes.sizeof(report),
        )

        if not ok:
            raise winerr("HidD_SetFeature")

    finally:
        kernel32.CloseHandle(handle)


def read_status():
    """
    Lee el feature report de 9 bytes.
    En USBRelay2 el byte 8 contiene el estado:
        bit 0 = relay 1
        bit 1 = relay 2
    """
    handle, path = open_relay()

    try:
        report = (ctypes.c_ubyte * 9)()
        report[0] = 0x00

        ok = hid.HidD_GetFeature(
            handle,
            ctypes.byref(report),
            ctypes.sizeof(report),
        )

        if not ok:
            raise winerr("HidD_GetFeature")

        state = report[8]
        relay1 = bool(state & 0x01)
        relay2 = bool(state & 0x02)

        board_id = bytes(report[1:6]).decode("ascii", errors="replace")

        return {
            "path": path,
            "id": board_id,
            "relay1": relay1,
            "relay2": relay2,
            "raw": bytes(report),
        }

    finally:
        kernel32.CloseHandle(handle)


RESTORE_INTENTOS = 5
RESTORE_BACKOFF_SEC = 0.5


def restore_power(relay_number=1, intentos=RESTORE_INTENTOS):
    """Devuelve los 12 V al dispositivo y verifica que hayan vuelto.

    Es la única operación de este módulo de la que no nos podemos rendir a la
    primera: mientras el relay siga en ON el PM6750 está sin alimentación, y
    del otro lado hay un paciente monitoreado. Por eso se reintenta, y por eso
    se confirma leyendo el estado de la placa en vez de confiar en que
    `HidD_SetFeature` no devolvió error — el comando puede aceptarse sin que el
    relay conmute.

    Devuelve True sólo si la placa confirma el relay en OFF.
    """
    ultimo = None

    for intento in range(1, intentos + 1):
        try:
            set_relay(relay_number, False)
            estado = read_status()
            energizado = estado["relay2"] if relay_number == 2 else estado["relay1"]
            if not energizado:
                return True
            ultimo = RuntimeError(
                f"la placa reporta el relay {relay_number} todavía en ON"
            )
        except Exception as e:
            ultimo = e

        print(f"[RELAY] Intento {intento}/{intentos} de restaurar 12 V falló: "
              f"{type(ultimo).__name__}: {ultimo}")

        if intento < intentos:
            time.sleep(RESTORE_BACKOFF_SEC)

    print(f"[RELAY] CRÍTICO: no se pudieron restaurar los 12 V del relay "
          f"{relay_number}. El dispositivo puede haber quedado sin alimentación.")
    return False


def restart_device(relay_number=1, seconds=5):
    """
    Para dispositivo conectado entre COM y NC:
      ON  -> abre NC -> corta 12 V
      espera
      OFF -> cierra NC -> restaura 12 V
    """
    print(f"Cortando 12 V: relay {relay_number} ON")
    set_relay(relay_number, True)

    try:
        print(f"Esperando {seconds} segundos...")
        time.sleep(seconds)
    finally:
        # De acá no se sale sin intentar todo lo posible por devolver los 12 V,
        # incluso si un Ctrl+C interrumpió la espera.
        print(f"Restaurando 12 V: relay {relay_number} OFF")
        restaurado = restore_power(relay_number)

    # Si el corte quedó sin restaurar, el llamador tiene que enterarse: dar
    # `restart_device` por exitoso acá dejaría al equipo sin energía y a nadie
    # buscándolo.
    if not restaurado:
        raise RuntimeError(
            f"No se pudo restaurar la alimentación del relay {relay_number}"
        )

    print("Ciclo terminado.")


def show_status():
    s = read_status()
    print("USBRelay2 encontrada")
    print("ID placa :", s["id"])
    print("Relay 1  :", "ON" if s["relay1"] else "OFF")
    print("Relay 2  :", "ON" if s["relay2"] else "OFF")
    print("RAW      :", " ".join(f"{b:02X}" for b in s["raw"]))


def usage():
    print(
        """
USBRelay2 - VID 16C0 / PID 05DF

Uso:
  python usbrelay2.py status
  python usbrelay2.py on 1
  python usbrelay2.py off 1
  python usbrelay2.py restart 1 5
  python usbrelay2.py test 1

Para COM + NC:
  ON  = corta los 12 V
  OFF = restablece los 12 V
""".strip()
    )


def main():
    if os.name != "nt":
        print("ERROR: este script sin dependencias es exclusivamente para Windows.")
        return 1

    if len(sys.argv) < 2:
        usage()
        return 0

    cmd = sys.argv[1].lower()

    try:
        if cmd == "status":
            show_status()

        elif cmd == "on":
            relay = int(sys.argv[2]) if len(sys.argv) > 2 else 1
            set_relay(relay, True)
            print(f"Relay {relay}: ON")

        elif cmd == "off":
            relay = int(sys.argv[2]) if len(sys.argv) > 2 else 1
            set_relay(relay, False)
            print(f"Relay {relay}: OFF")

        elif cmd == "restart":
            relay = int(sys.argv[2]) if len(sys.argv) > 2 else 1
            seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 5.0
            restart_device(relay, seconds)

        elif cmd == "test":
            relay = int(sys.argv[2]) if len(sys.argv) > 2 else 1
            print(f"Prueba relay {relay}: ON durante 1 segundo")
            set_relay(relay, True)
            time.sleep(1)
            set_relay(relay, False)
            print("Prueba terminada: relay OFF")

        else:
            usage()
            return 1

        return 0

    except KeyboardInterrupt:
        print("\nInterrumpido por usuario.")
        return 130

    except Exception as e:
        print("\nERROR:", e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
