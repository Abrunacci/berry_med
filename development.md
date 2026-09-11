# Development Guide – BerryMed Vitals Monitor

This document is for contributors or developers working with the BerryMed source code.

---

## Prerequisites

- Windows 10+
- Python 3.13 (exacta: ver [Building](#building-executables-on-windows))
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

## Building Executables on Windows

Los exe se generan **siempre con `build.ps1`**, en Windows. Hace los mismos
pasos en cualquier PC y en GitHub Actions, así dos builds del mismo commit salen
iguales.

> **Python 3.13, exacta.** La versión de Python cambia cómo el exe valida TLS
> (en 3.13 hay modo estricto: ver `docs/certificado_vencido.md`, §2.3), y ya
> hubo exes con 3.11 y con 3.13 mezclados en los tótems. Los `.spec` cortan el
> build con cualquier otra versión, se los corra como se los corra
> (`tools/build_meta.py`).

### Build local

Requisitos: Windows 10+, Python 3.13 (python.org o Microsoft Store) y Git. No
hace falta instalar Poetry: el script usa su propia copia.

```powershell
cd C:\ruta\a\berry_med
powershell -NoProfile -ExecutionPolicy Bypass -File build.ps1
```

Qué hace:

1. Busca Python 3.13 (`py -3.13`, `python3.13` o `python`). Si no está, corta.
2. Arma `.venv` con las versiones exactas de `poetry.lock` (`poetry sync`).
   Poetry vive aparte, en `.tools\poetry`.
3. Corre los tests. `-SinTests` los saltea.
4. Genera `dist\berry-monitor.exe` y `dist\berry-configure.exe` con los `.spec`.
5. Verifica cada exe (trae Python 3.13; el monitor trae truststore) y deja en
   `dist\build-info.txt` la versión y el SHA-256 de cada uno.

`dist/` no se versiona: los exe no se commitean.

### Versión

Sale de git (`git describe --tags`): en un tag queda `1.0.9`; en commits
posteriores, `1.0.9-3-gabc1234`; con cambios sin commitear termina en `-dirty`.
Queda sellada en tres lugares:

- En el **log** de berry-monitor: una línea `[BUILD] ...` al arrancar, que se
  repite al principio de cada archivo cuando el log rota.
- En las **propiedades del exe**: clic derecho > Propiedades > Detalles >
  Versión del producto.
- En el **`/health`**, campo `build`.

### Releases

1. Crear la release en GitHub (Releases > Draft a new release) con un tag nuevo,
   por ejemplo `1.0.9`, y publicarla.
2. El workflow `build` genera los exe en Windows con `build.ps1` y los adjunta a
   la release, junto con `build-info.txt`. Tarda unos minutos.

Los técnicos bajan los exe de la release. Para un build de prueba sin release:
Actions > build > Run workflow, y bajar el artefacto `berry-exes`.

### Probar el exe

1. `dist\berry-configure.exe` para cargar la configuración
   (`%APPDATA%\BerryMed Monitor\credentials.json`).
2. `dist\berry-monitor.exe`. Al arrancar tiene que mostrar la línea `[BUILD]`
   con la versión y `Python 3.13`.

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
| `No se encontro Python 3.13` | No está instalado, o no está en el PATH | Instalar Python 3.13 y volver a correr `build.ps1` |
| `[BUILD] Los exe se generan con Python 3.13, y este es 3.x` | Se corrió PyInstaller con otro Python | Usar `build.ps1` |
| `[BUILD] Faltan módulos en este entorno` | El entorno no tiene las versiones de `poetry.lock` | Usar `build.ps1` |
| `ModuleNotFoundError` al ejecutar el exe | Se agregó un módulo a `src/` sin sumarlo a `hiddenimports` del spec | Agregarlo; `tests/test_build.py` lo avisa |
| Falta DLL al abrir el exe | Runtime de VC++ no instalado | Instalar VC++ Redistributable x64 |
| `[THERM][WARN] Connection issue` | Puerto COM mal o ocupado | Administrador de dispositivos → Puertos COM; Berry y termómetro en puertos distintos |

---

## Tests

```bash
poetry install          # instala pytest (grupo dev)
poetry run pytest
```

Corren en cualquier lado —WSL, Linux, Windows— y **no necesitan el Berry**. La
entrada del parser (`add_data()`) es una función pura de bytes a estado, así
que casi todo el pipeline se prueba alimentándolo con tramas.

Hay dos fuentes de bytes, y hacen falta las dos:

| | De dónde salen | Qué prueban |
|---|---|---|
| **Tramas sintéticas** (`tests/tramas.py`) | Construidas acá, según el manual | Qué hace el código ante un byte dado. Permiten armar el caso raro: un checksum roto, una trama partida al medio, un SpO2 en error. |
| **Capturas reales** (`tests/capturas/*.bin`) | Grabadas del equipo | Que el equipo mande los bytes que creemos. Si nuestra lectura del manual estuviera mal, sólo esto se entera. |

Sin capturas, esos tests saltean solos y el resto corre igual.

### Grabar las capturas (esto sí necesita el equipo)

En Windows, con el Berry enchufado:

```bash
poetry run python tools\capturar_escenarios.py --puerto COM3
```

Te va pidiendo una maniobra por escenario —poner el dedo, desenchufar la sonda
de SpO2, despegar un electrodo— graba, y **te dice en el momento si la captura
sirve**: parsea los bytes con el parser de producción y verifica el estado que
ese escenario esperaba. Si salió mal, ofrece repetirla ahí mismo. Son unos 6
minutos en total.

```bash
poetry run python tools\capturar_escenarios.py --listar
poetry run python tools\capturar_escenarios.py --puerto COM3 --solo ocioso
```

Los `.bin` y su `manifest.json` van versionados: grabarlos cuesta una sesión con
el hardware y son el registro de qué manda el equipo en cada estado.

### Lo que los tests no cubren

`_connect_sync()`: abrir el puerto, mandar los enables y esperar respuesta es
I/O contra el equipo, y un `.bin` no dice si el Berry engancha. Después de tocar
`serial_manager.py`, esa parte se sigue probando enchufando: alcanza con ver
`Datos OK tras Xs (N bytes)` en el log de arranque.

## Project Structure

- `app.py` – main monitoring loop, Pusher events, HTTP POST
- `configure.py` – credential and config generator
- `config.py` – reads local credentials file
- `src/thermometer_reader.py` – USB thermometer serial reader
- `src/` – device interfaces, parser, communication handlers
- `tools/mock_api_server.py` – local HTTP server for payload inspection
- `pyproject.toml` – dependencies and build config (Poetry)
- `tests/` – set de tests (pytest); `tests/escenarios.py` define qué se captura
- `tools/capturar_escenarios.py` – graba las capturas del equipo para los tests
- `docs/backend.md` – qué se manda al backend y a qué endpoints
- `docs/configuracion.md` – todas las claves de configuración
- `berry-monitor.spec` / `berry-configure.spec` – PyInstaller build specs
- `build.ps1` – build de los dos exe (ver Building)
- `tools/build_meta.py` – guarda de Python 3.13 y sello de versión que usan los `.spec`
- `src/build_info.py` – lee el sello desde el exe; va al log y al `/health`

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
