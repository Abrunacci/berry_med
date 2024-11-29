import certifi
# -- mode: python ; coding: utf-8 --


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[('C:\\Users\\franc\\AppData\\Local\\pypoetry\\Cache\\virtualenvs\\berry-integration-O9_8wKB9-py3.11\\Lib\\site-packages\\certifi\\cacert.pem', 'certifi'),  (certifi.where(), 'pusher/')],
    hiddenimports=['asyncio', 'bleak', 'pusher', 'pysher', 'certifi', 'src.data_parser', 'src.bluetooth_manager'],
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