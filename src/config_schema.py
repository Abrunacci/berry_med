"""Definición de la configuración de la app: única fuente de verdad.

El asistente de texto y el gráfico se arman los dos a partir de `FIELDS`, así no
pueden desincronizarse: agregar una clave acá la hace aparecer en los dos.

La lógica de carga, validación y guardado vive acá y no en la interfaz, para que
se pueda testear sin necesidad de una pantalla.

Ver `docs/protocolo_berry.md` §2.
"""

import json
from dataclasses import dataclass, field as _field
from pathlib import Path
from typing import Tuple

from config import DEFAULT_API_ENDPOINT, api_base_url, build_metrics_url, get_app_data_path

# Claves que la app entiende pero el asistente no pregunta (ver §2.3). No se
# ofrecen, pero si están en el archivo se conservan al guardar.
UNPROMPTED_KEYS = ("NIBP_TARGET_PRESSURE_MMHG",)


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    section: str
    kind: str = "text"        # text | password | choice | bool | int | float | path | port
    default: str = ""
    options: Tuple[str, ...] = ()
    help: str = ""
    required: bool = False


# Los campos se agrupan por sección y en este mismo orden: el asistente de
# texto imprime un encabezado cada vez que cambia la sección, así que mezclarlas
# haría aparecer el mismo título más de una vez.
SECTIONS = ("API", "Pusher", "Equipo", "Termómetro", "PM6750")

FIELDS = (
    # -------------------------------------------------------------- API ---
    # A dónde y con qué credenciales se postean los datos.
    #
    # El orden importa: los tres campos que arman la URL van juntos y primero,
    # para que la vista previa se complete de arriba hacia abajo mientras se
    # cargan. Ver URL_PARTS y metrics_url() más abajo.
    Field("TOTEM_ID", "Totem ID", "API", required=True,
          help="Identificador del tótem. Va en el medio de la URL de métricas "
               "y tiene que coincidir con el del canal de Pusher."),
    Field("API_URL", "API base URL", "API", required=True,
          help="Sólo la base del servicio, sin el tótem ni el endpoint. "
               "Ej: https://api.ondoctor365.com/v1/medical_devices"),
    Field("API_ENDPOINT", "Endpoint", "API", default="metrics",
          help="Último tramo de la URL, después del Totem ID."),
    Field("API_USERNAME", "API usuario", "API", required=True,
          help="Usuario de la autenticación básica del POST."),
    Field("API_PASSWORD", "API contraseña", "API", kind="password", required=True),
    Field("SSL_CERT_FILE_PATH", "Certificado SSL", "API", kind="path",
          help="Bundle de certificados (.pem). Se exporta como SSL_CERT_FILE."),

    # ----------------------------------------------------------- Pusher ---
    # Por acá llegan las órdenes: arrancar, parar y tomar la presión.
    Field("PUSHER_KEY", "Pusher key", "Pusher", required=True,
          help="App key de Pusher; por ahí llegan las órdenes de arranque y parada."),
    Field("PUSHER_CLUSTER", "Pusher cluster", "Pusher", default="us2"),
    Field("START_EVENT_NAME", "Evento de arranque", "Pusher",
          default="start-monitoring",
          help="Evento que habilita el envío de datos."),
    Field("STOP_EVENT_NAME", "Evento de parada", "Pusher",
          default="stop-monitoring",
          help="Evento que corta el envío y resetea el parser."),
    Field("PUBLIC_CHANNEL", "Canal público", "Pusher", required=True,
          help="Canal de Pusher al que se suscribe la app. Suele ser "
               "'totem.<Totem ID>'."),

    # ------------------------------------------------------------ Equipo ---
    Field("DEVICE_CONNECTION", "Conexión", "Equipo", kind="choice",
          options=("bt", "usb"), default="bt",
          help="bt = Bluetooth LE · usb = serie/RS232."),
    Field("DEVICE_PORT", "Puerto serie", "Equipo", kind="port", default="COM3",
          help="Sólo se usa si la conexión es 'usb'."),

    # -------------------------------------------------------- Termómetro ---
    Field("THERMOMETER_ENABLED", "Termómetro habilitado", "Termómetro",
          kind="bool", default="false",
          help="Dispositivo aparte del PM6750; su valor pisa la temperatura."),
    Field("THERMOMETER_PORT", "Puerto del termómetro", "Termómetro",
          kind="port", default="COM5"),
    Field("THERMOMETER_BAUD", "Baudios", "Termómetro", kind="int",
          default="115200"),
    Field("THERMOMETER_RECONNECT_SECONDS", "Reintento de conexión (seg)",
          "Termómetro", kind="float", default="2.0"),

    # ------------------------------------------------------------ PM6750 ---
    Field("ECG_MODE", "Modo de ECG", "PM6750", kind="choice",
          options=("operacion", "monitor", "diagnostico"), default="monitor",
          help="operacion 1-25Hz · monitor 0.5-75Hz · diagnostico 0.05-100Hz. "
               "Usar 'diagnostico' para conservar el segmento ST."),
    Field("ECG_GAIN", "Ganancia de ECG", "PM6750", kind="choice",
          options=("x0.25", "x0.5", "x1", "x2"), default="x1",
          help="Bajarla si la onda satura (valores pegados a 0 o 250)."),
    Field("RESP_GAIN", "Ganancia de respiración", "PM6750", kind="choice",
          options=("x0.25", "x0.5", "x1", "x2"), default="x1"),
    Field("NIBP_MODE", "Modo de paciente (NIBP)", "PM6750", kind="choice",
          options=("adulto", "nino", "neonato"), default="adulto",
          help="Define el rango de presión del manguito."),
)

BY_KEY = {f.key: f for f in FIELDS}

# Campos que arman la URL de métricas, en el orden en que se piden. Las dos
# interfaces refrescan la vista previa cuando cambia cualquiera de estos.
URL_PARTS = ("TOTEM_ID", "API_URL", "API_ENDPOINT")


def metrics_url(values: dict) -> str:
    """URL de métricas que va a usar la app, armada con lo cargado hasta ahora.

    Es la vista previa del asistente: usa el mismo `build_metrics_url()` que
    `app._build_api_url()`, así lo que se ve al configurar es literalmente lo
    que se va a postear.
    """
    return build_metrics_url(
        values.get("API_URL", ""),
        values.get("TOTEM_ID", ""),
        values.get("API_ENDPOINT", "") or DEFAULT_API_ENDPOINT,
    )


# ----------------------------------------------------------------- rutas ---

def config_path() -> Path:
    return get_app_data_path() / "credentials.json"


def load_existing() -> dict:
    """Lee el archivo actual. Devuelve {} si no existe o está corrupto."""
    path = config_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def initial_values(existing=None) -> dict:
    """Valores con los que arrancar el formulario: lo guardado, o el default."""
    existing = load_existing() if existing is None else existing
    values = {}
    for f in FIELDS:
        raw = existing.get(f.key)
        values[f.key] = f.default if raw is None or raw == "" else str(raw)

    # Las configs viejas guardaban la URL entera (con tótem y /metrics). Se
    # muestra ya recortada para que abrir el asistente y guardar migre solo,
    # sin que nadie tenga que editar la URL a mano.
    if values.get("API_URL"):
        values["API_URL"] = api_base_url(
            values["API_URL"],
            values.get("TOTEM_ID"),
            values.get("API_ENDPOINT") or DEFAULT_API_ENDPOINT,
        )

    return values


# ------------------------------------------------------------ validación ---

def validate(values: dict) -> dict:
    """Devuelve {clave: mensaje de error}. Vacío significa que está todo bien."""
    errors = {}
    for f in FIELDS:
        raw = str(values.get(f.key, "")).strip()

        if f.required and not raw:
            errors[f.key] = "Requerido"
            continue
        if not raw:
            continue

        if f.kind == "choice" and raw.lower() not in f.options:
            errors[f.key] = f"Debe ser uno de: {', '.join(f.options)}"
        elif f.kind == "int":
            try:
                if int(raw) <= 0:
                    errors[f.key] = "Debe ser mayor que cero"
            except ValueError:
                errors[f.key] = "Debe ser un número entero"
        elif f.kind == "float":
            try:
                if float(raw) <= 0:
                    errors[f.key] = "Debe ser mayor que cero"
            except ValueError:
                errors[f.key] = "Debe ser un número"
        elif f.kind == "bool" and raw.lower() not in ("true", "false"):
            errors[f.key] = "Debe ser true o false"

    # API_URL es sólo la base: si trae el tótem o el endpoint, se volverían a
    # agregar al armar la URL y quedaría duplicado.
    url = str(values.get("API_URL", "")).strip().rstrip("/")
    totem = str(values.get("TOTEM_ID", "")).strip()
    endpoint = str(values.get("API_ENDPOINT", "")).strip().strip("/") \
        or DEFAULT_API_ENDPOINT
    if url.endswith(f"/{endpoint}"):
        errors["API_URL"] = f"Sacá el /{endpoint}: se agrega solo"
    elif totem and url.endswith(f"/{totem}"):
        errors["API_URL"] = "Sacá el Totem ID de la URL: se agrega solo"

    if values.get("DEVICE_CONNECTION", "").lower() == "usb" \
            and not str(values.get("DEVICE_PORT", "")).strip():
        errors["DEVICE_PORT"] = "Requerido cuando la conexión es 'usb'"

    if str(values.get("THERMOMETER_ENABLED", "")).lower() == "true" \
            and not str(values.get("THERMOMETER_PORT", "")).strip():
        errors["THERMOMETER_PORT"] = "Requerido si el termómetro está habilitado"

    return errors


def merge(values: dict, existing=None) -> dict:
    """Arma el JSON final.

    Conserva las claves que el asistente no pregunta (§2.3) para que volver a
    correrlo no borre lo que se cargó a mano.
    """
    existing = load_existing() if existing is None else existing
    out = {}
    for key, raw in existing.items():
        if key not in BY_KEY:
            out[key] = raw
    for f in FIELDS:
        raw = str(values.get(f.key, "")).strip()
        out[f.key] = raw.lower() if f.kind in ("choice", "bool") else raw
    return out


def save(values: dict, existing=None) -> Path:
    """Valida, mezcla y escribe. Lanza ValueError si algo no valida."""
    errors = validate(values)
    if errors:
        detail = "; ".join(f"{k}: {v}" for k, v in errors.items())
        raise ValueError(detail)

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(merge(values, existing), fh, indent=2, ensure_ascii=False)
    return path


def list_serial_ports():
    """Puertos serie detectados, para ofrecerlos como sugerencia."""
    try:
        from serial.tools import list_ports
        return [p.device for p in list_ports.comports()]
    except Exception:
        return []
