import json
import os
import sys
from pathlib import Path


def get_app_data_path() -> Path:
    """Get the appropriate AppData path for storing credentials"""
    app_name = "BerryMed Monitor"
    if sys.platform == "win32":
        app_data = Path(os.getenv("APPDATA"))
        return app_data / app_name
    else:
        # Fallback for other OS (though we're targeting Windows)
        return Path.home() / ".berrymed"


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

        # Return all configurations from credentials file
        return {
            "key": credentials.get("PUSHER_KEY"),
            "cluster": credentials.get("PUSHER_CLUSTER"),
            "TOTEM_ID": credentials.get("TOTEM_ID"),
            "api_url": credentials.get("API_URL"),
            "api_username": credentials.get("API_USERNAME"),
            "api_password": credentials.get("API_PASSWORD"),
            "public_channel": credentials.get("PUBLIC_CHANNEL"),
            "start_event_name": credentials.get("START_EVENT_NAME"),
            "stop_event_name": credentials.get("STOP_EVENT_NAME"),
            "device_connection": credentials.get("DEVICE_CONNECTION", "bt"),
            "device_port": credentials.get("DEVICE_PORT", "COM3"),
        }

    except Exception as e:
        print(f"Error reading credentials: {e}")
        return None


PUSHER_CONFIG = get_config()
