# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['configure.py'],
    pathex=[],
    binaries=[],
    datas=[],
    # tkinter y configure_gui van explícitos: la GUI se importa dentro de una
    # función (para poder caer al asistente de texto si no está), y así se
    # garantiza que PyInstaller igual la empaquete.
    hiddenimports=['asyncio', 'bleak', 'pysher', 'certifi', 'src.data_parser', 'src.bluetooth_manager', 'aiohttp', 'aiohttp.client', 'tkinter', 'tkinter.ttk', 'tkinter.filedialog', 'tkinter.messagebox', 'src.config_schema', 'src.configure_gui', 'serial.tools.list_ports'],
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
    name='berry-configure',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    # Modo ventana: no abre consola. El asistente es gráfico, así que una
    # consola negra de fondo sólo estorba. `configure.py` está preparado para
    # este modo: se engancha a la terminal del padre si lo lanzaron con --cli,
    # y si no puede abrir la ventana avisa con un cuadro de diálogo nativo en
    # vez de romper por no tener stdout.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
