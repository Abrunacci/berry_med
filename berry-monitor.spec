# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[],
    # `src` es un namespace package (no tiene __init__.py), así que
    # PyInstaller no resuelve sus submódulos solo: van todos listados acá.
    # Si se agrega un módulo a src/, agregarlo también en esta lista o el exe
    # revienta al arrancar con ModuleNotFoundError.
    hiddenimports=['asyncio', 'bleak', 'pysher', 'src.data_parser', 'src.bluetooth_manager', 'src.thermometer_reader', 'src.serial_manager', 'src.logging_setup', 'src.health', 'src.pm6750_protocol', 'logging.handlers', 'aiohttp', 'aiohttp.client', 'serial', 'serial.serialwin32', 'serial.tools.list_ports_windows'],
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
