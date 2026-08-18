# BerryMed Vitals Monitor v1.0.5

Monitor de signos vitales BerryMed para Windows con soporte Bluetooth/USB y envio de datos via Pusher + HTTP.

## Novedades

### Termometro IR por USB (opcional)
- Lectura en segundo plano desde un puerto COM dedicado (`src/thermometer_reader.py`).
- Configuracion en `berry-configure.exe`:
  - `THERMOMETER_ENABLED`
  - `THERMOMETER_PORT` (distinto al puerto del Berry)
  - `THERMOMETER_BAUD` (por defecto `115200`)
  - `THERMOMETER_RECONNECT_SECONDS` (reintento ante desconexion)
- La temperatura se envia solo en `data.vitalSigns.temperature` (string en C).
- **Prioridad:** si hay lectura valida del termometro, reemplaza la temperatura del Berry; si no, se mantiene el valor del dispositivo (por ej. `"-"`).
- Conversion automatica de F a C cuando el termometro reporta en Fahrenheit.
- Logs con prefijo `[THERM]` para diagnostico de conexion y parseo.

### Herramientas de desarrollo
- Servidor mock HTTP (`tools/mock_api_server.py`) para inspeccionar payloads en local (`http://127.0.0.1:8080/vitals`).

## Cambios

- `configure.py` y `config.py`: nuevas claves de termometro en `credentials.json`.
- `app.py`: integracion del lector de termometro en el ciclo de envio de vitales.
- `berry-monitor.spec`: modulos ocultos para `pyserial` y `thermometer_reader`.
- `pyproject.toml`: dependencia `pyserial ^3.5`.
- `.gitignore`: exclusiones ampliadas (entorno virtual, artefactos de build, etc.).
- Ejecutables actualizados en `dist/`: `berry-configure.exe` y `berry-monitor.exe`.

## Documentacion

- `readme.md`: requisitos y troubleshooting del termometro.
- `development.md`: guia paso a paso de build en Windows, prueba con mock API y tabla de troubleshooting.

## Instalacion / actualizacion

1. Descargar `berry-configure.exe` y `berry-monitor.exe` de esta release.
2. Ejecutar `berry-configure.exe` de nuevo si queres habilitar el termometro (nuevos campos en `%APPDATA%\\BerryMed Monitor\\credentials.json`).
3. Usar puertos COM distintos para Berry y termometro.
4. Si compilas desde codigo: `poetry install` y rebuild con `--clean`:

```powershell
poetry run pyinstaller berry-configure.spec --clean --noconfirm
poetry run pyinstaller berry-monitor.spec --clean --noconfirm
```

## Requisitos

- Windows 10+
- BerryMed por Bluetooth o USB
- Termometro IR USB (opcional), puerto COM propio

## Licencia

GNU GPLv3.0

---

## Notas para crear la release

| Campo | Valor sugerido |
|--------|----------------|
| **Tag** | `1.0.5` |
| **Titulo** | `v1.0.5 - Termometro IR USB` |
| **Assets** | `dist/berry-configure.exe`, `dist/berry-monitor.exe` |

Si preferis marcar esto como feature release, podes usar **v1.1.0** con el mismo cuerpo cambiando solo el numero de version.
