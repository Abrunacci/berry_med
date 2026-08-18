import json
import os
import sys
from pathlib import Path


APP_NAME = "BerryMed Monitor"


def _wsl_windows_config_dir():
    """Config de Windows vista desde WSL, si la hay.

    En WSL `sys.platform` es 'linux', así que sin esto se busca la config en
    `~/.berrymed` y no se encuentra la real, que vive en el `%APPDATA%` de
    Windows. Es una comodidad de desarrollo: permite correr la app y el
    configurador desde WSL contra la misma configuración que usa el ejecutable.
    """
    try:
        with open("/proc/version", "r") as fh:
            if "microsoft" not in fh.read().lower():
                return None
    except OSError:
        return None

    for users in (Path("/mnt/c/Users"), Path("/c/Users")):
        if not users.is_dir():
            continue
        try:
            for home in sorted(users.iterdir()):
                candidate = home / "AppData" / "Roaming" / APP_NAME
                if (candidate / "credentials.json").is_file():
                    return candidate
        except OSError:
            continue
    return None


def get_app_data_path() -> Path:
    """Directorio donde vive `credentials.json`.

    Orden de preferencia:
      1. `BERRYMED_CONFIG_DIR`, si está definida (útil para pruebas).
      2. `%APPDATA%\\BerryMed Monitor` en Windows.
      3. `~/.berrymed` si ya tiene una config propia.
      4. La config de Windows, si se está corriendo bajo WSL.
      5. `~/.berrymed` (se creará ahí).
    """
    override = os.getenv("BERRYMED_CONFIG_DIR")
    if override:
        return Path(override)

    if sys.platform == "win32":
        return Path(os.getenv("APPDATA")) / APP_NAME

    local = Path.home() / ".berrymed"
    if (local / "credentials.json").is_file():
        return local

    return _wsl_windows_config_dir() or local


DEFAULT_API_ENDPOINT = "metrics"


def build_metrics_url(base, totem_id, endpoint=DEFAULT_API_ENDPOINT) -> str:
    """Arma la URL de métricas: `{base}/{totem_id}/{endpoint}`.

    Única fuente de verdad del armado: la usan la app para postear y el
    asistente para mostrar la vista previa, así lo que se ve al configurar es
    exactamente lo que se va a usar.

    Las partes vacías se omiten, para que la vista previa del asistente se vaya
    armando a medida que se completan los campos en vez de mostrar barras
    sueltas.
    """
    partes = [
        str(base or "").strip().rstrip("/"),
        str(totem_id or "").strip().strip("/"),
        str(endpoint or "").strip().strip("/"),
    ]
    return "/".join(p for p in partes if p)


def api_base_url(raw, totem_id=None, endpoint=DEFAULT_API_ENDPOINT) -> str:
    """Normaliza `API_URL` a la base, tolerando la forma vieja.

    Hasta ahora `API_URL` guardaba el endpoint entero, con el id del tótem y
    `/metrics` adentro:

        https://api.../v1/medical_devices/01ksj3g36a6.../metrics

    Ahora guarda sólo la base, y el resto se arma con `build_metrics_url()`.
    Las configuraciones ya instaladas se recortan al vuelo — si no, quedaría
    `.../{id}/metrics/{id}/metrics`. No se reescribe el archivo: el asistente lo
    normaliza cuando se vuelva a guardar.
    """
    if not raw:
        return ""

    base = str(raw).strip().rstrip("/")

    sufijo_endpoint = str(endpoint or "").strip().strip("/")
    if sufijo_endpoint and base.endswith(f"/{sufijo_endpoint}"):
        base = base[: -len(f"/{sufijo_endpoint}")]

    if totem_id:
        sufijo_totem = f"/{str(totem_id).strip()}"
        if base.endswith(sufijo_totem):
            base = base[: -len(sufijo_totem)]

    return base


def get_config():
    """Read and return the credentials from the JSON file"""
    try:
        config_dir = get_app_data_path()
        config_file = config_dir / "credentials.json"

        if not config_file.exists():
            print(f"Credentials file not found at: {config_file}")
            return None

        with open(config_file, "r") as f:
            credentials = json.load(f)

        default_thermometer_port = "COM5" if sys.platform == "win32" else "/dev/ttyACM0"

        # Return all configurations from credentials file
        return {
            "key": credentials.get("PUSHER_KEY"),
            "cluster": credentials.get("PUSHER_CLUSTER"),
            "totem_id": credentials.get("TOTEM_ID"),
            # `api_url` es el valor crudo del archivo; `api_base_url` es la
            # base ya recortada. La URL final la arma la app con
            # build_metrics_url(): {api_base_url}/{totem_id}/{api_endpoint}
            "api_url": credentials.get("API_URL"),
            "api_endpoint": str(
                credentials.get("API_ENDPOINT") or DEFAULT_API_ENDPOINT
            ).strip().strip("/"),
            "api_base_url": api_base_url(
                credentials.get("API_URL"),
                credentials.get("TOTEM_ID"),
                credentials.get("API_ENDPOINT") or DEFAULT_API_ENDPOINT,
            ),
            "api_username": credentials.get("API_USERNAME"),
            "api_password": credentials.get("API_PASSWORD"),
            "public_channel": credentials.get("PUBLIC_CHANNEL"),
            "start_event_name": credentials.get("START_EVENT_NAME"),
            "stop_event_name": credentials.get("STOP_EVENT_NAME"),
            # Tope de minutos que puede durar una sesión si nunca llega el
            # evento de stop; pasado eso el totem corta el envío y limpia solo.
            # 0 desactiva el corte. Ver docs/protocolo_berry.md §7.
            "max_session_minutes": float(credentials.get("MAX_SESSION_MINUTES", 30)),
            "device_connection": credentials.get("DEVICE_CONNECTION", "bt"),
            "device_port": credentials.get("DEVICE_PORT", "COM3"),
            "ssl_cert_file": credentials.get("SSL_CERT_FILE_PATH"),
            "thermometer_enabled": str(
                credentials.get("THERMOMETER_ENABLED", "false")
            ).lower()
            in {"1", "true", "yes", "on"},
            "thermometer_port": credentials.get(
                "THERMOMETER_PORT", default_thermometer_port
            ),
            "thermometer_baud": int(credentials.get("THERMOMETER_BAUD", 115200)),
            "thermometer_reconnect_seconds": float(credentials.get("THERMOMETER_RECONNECT_SECONDS", 2.0)),
            # --- Configuración del PM6750 (ver docs/protocolo_berry.md §4) ----
            # Los defaults son los valores que el equipo ya venía usando, así
            # que no cambian el comportamiento: sólo lo vuelven explícito y
            # reproducible en vez de depender del estado interno del módulo.
            #   ECG_MODE:  "operacion" (1-25Hz) | "monitor" (0.5-75Hz)
            #              | "diagnostico" (0.05-100Hz)
            #   ECG_GAIN:  "x0.25" | "x0.5" | "x1" | "x2"
            #              Si la onda satura (valores pegados a 0/250), bajarla.
            #   RESP_GAIN: idem ECG_GAIN, para la onda de respiración.
            #   NIBP_MODE: "adulto" | "nino" | "neonato"
            #   NIBP_TARGET_PRESSURE_MMHG: presión objetivo; None = default del
            #              equipo (adulto 150, niño 100, neonato 70).
            "ecg_mode": str(credentials.get("ECG_MODE", "monitor")).lower(),
            "ecg_gain": str(credentials.get("ECG_GAIN", "x1")).lower(),
            "resp_gain": str(credentials.get("RESP_GAIN", "x1")).lower(),
            "nibp_mode": str(credentials.get("NIBP_MODE", "adulto")).lower(),
            "nibp_target_pressure_mmhg": credentials.get(
                "NIBP_TARGET_PRESSURE_MMHG"
            ),
        }

    except Exception as e:
        print(f"Error reading credentials: {e}")
        return None


PUSHER_CONFIG = get_config()
