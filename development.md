# Development Guide – BerryMed Vitals Monitor

This document is for contributors or developers working with the BerryMed source code.

---

## Prerequisites

- Windows 10+
- Python 3.11+
- [Poetry](https://python-poetry.org/) – Python dependency manager
- A compatible BerryMed device (Bluetooth or USB)
- Optional: USB IR thermometer on a separate COM port

---

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/Abrunacci/berry_med.git
cd berry_med
```

### 2. Install dependencies

```bash
poetry install
```

### 3. Run the configuration script

```bash
poetry run python configure.py
```

You will be prompted for:

- Pusher and API credentials
- Connection type (`bt` or `usb`)
- Berry USB port (e.g., `COM3`)
- Path to a local SSL certificate (e.g., `cacert.pem`)
- Thermometer settings: enabled, COM port, baud rate, reconnect delay

Credentials are saved to `%APPDATA%\BerryMed Monitor\credentials.json` on Windows.

---

## Development Mode

Run the application directly:

```bash
poetry run python app.py
```

### Local API mock (optional)

To inspect outgoing payloads without a real backend:

```bash
python tools/mock_api_server.py
```

Set `API_URL` in credentials to `http://127.0.0.1:8080/vitals` (use `127.0.0.1`, not `0.0.0.0`).
It is a **base** URL: the app posts to `{API_URL}/{TOTEM_ID}/metrics`. The mock server
accepts any path, so no extra setup is needed.

---

## Payload: temperature

Temperature is sent only in `data.vitalSigns.temperature` (Celsius string).

- **Priority:** USB thermometer reading overrides Berry temperature when a valid reading exists.
- **Fallback:** If the thermometer is disabled or has no reading, the Berry value is kept (e.g. `"-"`).
- **Unit conversion:** If the thermometer reports `F`, it is converted to `C` before sending.
- There is no separate `thermometer` key in the JSON payload.

---

## Building Executables on Windows (step by step)

PyInstaller genera un `.exe` para el sistema operativo donde lo ejecutás. El build **debe hacerse en Windows**, aunque el código esté en WSL o en otro equipo: copiá el repo a Windows y seguí estos pasos ahí.

Usá siempre los archivos `.spec` del repositorio (`berry-configure.spec` y `berry-monitor.spec`). No uses el comando largo antiguo con `--hidden-import` suelto.

---

### Paso 0 — Requisitos previos

Instalá en la PC de build:

| Herramienta | Versión | Verificación |
|-------------|---------|--------------|
| Windows | 10 o superior | — |
| Python | 3.11, 3.12 o 3.13 | `python --version` |
| Poetry | Última estable | `poetry --version` |
| Git (opcional) | Cualquiera | `git --version` |

Instalación de Poetry (PowerShell):

```powershell
(Invoke-WebRequest -Uri https://install.python-poetry.org -UseBasicParsing).Content | python -
```

Cerrá y volvé a abrir la terminal después de instalar Poetry.

---

### Paso 1 — Abrir terminal en la carpeta del proyecto

**PowerShell** o **CMD**:

```powershell
cd C:\ruta\donde\clonaste\berry_med
```

Si clonás desde cero:

```powershell
git clone https://github.com/Abrunacci/berry_med.git
cd berry_med
```

Confirmá que existan estos archivos en la raíz:

- `app.py`
- `configure.py`
- `berry-monitor.spec`
- `berry-configure.spec`
- `pyproject.toml`

---

### Paso 2 — Instalar dependencias con Poetry

```powershell
poetry install
```

Qué debería pasar:

- Poetry crea un entorno virtual (`.venv`) si no existe.
- Instala dependencias de `pyproject.toml`: `bleak`, `pyserial`, `aiohttp`, `pysher`, `pyinstaller`, etc.
- Al final no debería haber errores de resolución de paquetes.

Verificación opcional:

```powershell
poetry run python -c "import serial; import aiohttp; print('OK')"
```

Si imprime `OK`, el entorno está listo para el build.

---

### Paso 3 — Build de `berry-configure.exe`

```powershell
poetry run pyinstaller berry-configure.spec --clean --noconfirm
```

Qué hace cada flag:

- `berry-configure.spec` — define entrada `configure.py`, nombre del exe y módulos ocultos.
- `--clean` — borra caché de builds anteriores (recomendado siempre tras cambios de código).
- `--noconfirm` — sobrescribe `dist/` y `build/` sin preguntar.

Al terminar, deberías ver algo como:

```text
Building EXE from EXE-00.toc completed successfully.
```

Archivo generado:

```text
dist\berry-configure.exe
```

---

### Paso 4 — Build de `berry-monitor.exe`

```powershell
poetry run pyinstaller berry-monitor.spec --clean --noconfirm
```

Este spec incluye lo necesario para USB y termómetro:

- `serial`, `serial.serialwin32`, `serial.tools.list_ports_windows`
- `src.thermometer_reader`, `src.data_parser`, `src.bluetooth_manager`
- `bleak`, `pysher`, `aiohttp`

Archivo generado:

```text
dist\berry-monitor.exe
```

Comprobá que `dist\` contiene **ambos** ejecutables:

```powershell
dir dist\*.exe
```

---

### Paso 5 — Configurar credenciales (primera vez o cambio de entorno)

Ejecutá el configurador:

```powershell
.\dist\berry-configure.exe
```

Completá los prompts. Ejemplo de valores:

| Campo | Ejemplo |
|-------|---------|
| Pusher Key / Cluster | Los de tu cuenta Pusher |
| Totem ID | `totem12` |
| API URL | `https://tu-api.com/vitals` o `http://127.0.0.1:8080/vitals` (pruebas locales) |
| Device connection | `usb` o `bt` |
| Device port (Berry) | `COM6` |
| Thermometer enabled | `true` |
| Thermometer port | `COM5` (distinto al del Berry) |
| SSL cert path | Ruta a un `.pem` válido (puede quedar vacío solo para HTTP local) |

El archivo se guarda en:

```text
%APPDATA%\BerryMed Monitor\credentials.json
```

Ruta típica:

```text
C:\Users\<tu_usuario>\AppData\Roaming\BerryMed Monitor\credentials.json
```

Podés abrirlo con el Bloc de notas para revisar que `THERMOMETER_ENABLED` y `THERMOMETER_PORT` quedaron como esperás.

---

### Paso 6 — Ejecutar el monitor

```powershell
.\dist\berry-monitor.exe
```

Señales de que el build es correcto (código actual):

1. Al inicio imprime un diccionario de config con claves como:
   - `thermometer_enabled`
   - `thermometer_port`
   - `thermometer_baud`
2. Si el termómetro está habilitado, aparecen líneas `[THERM]`:
   - `[THERM] Reader started on COM5 @ 115200`
   - `[THERM] Connected to COM5` (o `[THERM][WARN] Connection issue` si el puerto falla)
3. Tras el evento Pusher `start-monitoring`, los POST incluyen `vitalSigns.temperature` (sin clave `thermometer` en el JSON).

Si el `print(cfg)` **no** muestra `thermometer_enabled`, el exe es viejo: volvé al Paso 4 con `--clean`.

---

### Paso 7 — Distribuir los ejecutables (opcional)

Para usar en otra PC Windows sin instalar Python:

1. Copiá `berry-configure.exe` y `berry-monitor.exe` desde `dist\`.
2. En la PC destino, ejecutá primero `berry-configure.exe` (genera su propio `credentials.json` en AppData de ese usuario).
3. Conectá Berry y termómetro; instalá [Visual C++ Redistributable](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist) si el exe reporta DLL faltante.

No hace falta copiar la carpeta `build\` ni todo el repo, solo los `.exe` de `dist\`.

---

### Paso 8 — Rebuild después de cambiar código

Cada vez que modifiques `app.py`, `config.py`, módulos en `src/` o los `.spec`:

```powershell
poetry install
poetry run pyinstaller berry-monitor.spec --clean --noconfirm
```

Si también cambiaste `configure.py`:

```powershell
poetry run pyinstaller berry-configure.spec --clean --noconfirm
```

Siempre usá `--clean` para no empaquetar bytecode o dependencias de un build anterior.

---

### Prueba local del payload (opcional)

Terminal 1 — mock API:

```powershell
python tools\mock_api_server.py
```

En `credentials.json`, temporalmente:

```json
"API_URL": "http://127.0.0.1:8080/vitals"
```

Usá `127.0.0.1`, no `0.0.0.0` (el cliente no puede conectar a `0.0.0.0`).

`API_URL` es la **base**: la app postea a `{API_URL}/{TOTEM_ID}/metrics`, o sea
`http://127.0.0.1:8080/vitals/<totem>/metrics`. El mock acepta cualquier ruta,
así que no hay nada más que configurar.

Terminal 2 — monitor:

```powershell
.\dist\berry-monitor.exe
```

Dispará `start-monitoring` desde Pusher; en la terminal del mock deberías ver el JSON con `vitalSigns.temperature`.

---

### Build troubleshooting

| Síntoma | Causa probable | Qué hacer |
|---------|----------------|-----------|
| `poetry: command not found` | Poetry no instalado o no en PATH | Reinstalar Poetry; reiniciar terminal |
| `ModuleNotFoundError: serial` al ejecutar el exe | Build sin `pyserial` o spec viejo | `poetry install` + rebuild con `berry-monitor.spec --clean` |
| `pyinstaller` no reconocido | Se invocó fuera del venv | Prefijo `poetry run` en todos los comandos |
| Exe no lee `THERMOMETER_*` | Binario compilado antes del soporte termómetro | Rebuild monitor con `--clean`; verificar `print(cfg)` |
| `Cannot connect to host 0.0.0.0:8080` | URL incorrecta en API_URL | Usar `http://127.0.0.1:8080/...` |
| Falta DLL al abrir el exe | Runtime de VC++ no instalado | Instalar VC++ Redistributable x64 |
| `[THERM][WARN] Connection issue` | Puerto COM mal o ocupado | Administrador de dispositivos → Puertos COM; Berry y termómetro en puertos distintos |
| Build muy lento la primera vez | Normal | PyInstaller analiza todas las dependencias; builds siguientes son más rápidos con caché (pero tras cambios de código usá `--clean`) |

---

## Project Structure

- `app.py` – main monitoring loop, Pusher events, HTTP POST
- `configure.py` – credential and config generator
- `config.py` – reads local credentials file
- `src/thermometer_reader.py` – USB thermometer serial reader
- `src/` – device interfaces, parser, communication handlers
- `tools/mock_api_server.py` – local HTTP server for payload inspection
- `pyproject.toml` – dependencies and build config (Poetry)
- `berry-monitor.spec` / `berry-configure.spec` – PyInstaller build specs

---

## Troubleshooting

- Ensure COM port or Bluetooth permissions are granted.
- Berry and thermometer must use different COM ports.
- Log prefixes: `[DEBUG]`, `[ERROR]`, `[THERM]`, `[USB]`, `[BERRY STATUS]`.
- On startup, `print(cfg)` should include `thermometer_enabled`, `thermometer_port`, etc.
- If the exe ignores thermometer settings, rebuild with `berry-monitor.spec --clean`.

---

## License

GNU GPLv3.0
