# -*- mode: python ; coding: utf-8 -*-
import sys

sys.path.insert(0, SPECPATH)
from tools.build_meta import preparar

# Corta si el build no es con Python 3.13 o si falta truststore, y sella la
# version: build_info.json adentro del exe (va al log al arrancar) y recurso de
# version de Windows. Vale se corra como se corra el build. Ver tools/build_meta.py.
meta = preparar('berry-monitor', SPECPATH, requiere=('truststore',))

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[(meta['json'], '.')],
    # `src` es un namespace package (no tiene __init__.py), así que
    # PyInstaller no resuelve sus submódulos solo: van todos listados acá.
    # Si se agrega un módulo a src/, agregarlo también en esta lista o el exe
    # revienta al arrancar con ModuleNotFoundError.
    # truststore elige su backend por SO con un import condicional: el de
    # Windows va explícito para no depender de que el análisis lo siga.
    hiddenimports=['asyncio', 'bleak', 'pysher', 'src.data_parser', 'src.bluetooth_manager', 'src.thermometer_reader', 'src.serial_manager', 'src.logging_setup', 'src.health', 'src.pm6750_protocol', 'src.usbrelay', 'src.ssl_context', 'src.cert_check', 'src.build_info', 'logging.handlers', 'aiohttp', 'aiohttp.client', 'serial', 'serial.serialwin32', 'serial.tools.list_ports_windows', 'truststore', 'truststore._windows'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='berry-monitor',
    # Clic derecho > Propiedades > Detalles: version, commit y Python.
    version=meta['version'],
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
