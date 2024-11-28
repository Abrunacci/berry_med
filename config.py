import os
import sys
import json
from pathlib import Path

def get_app_data_path() -> Path:
    """Get the appropriate AppData path for storing credentials"""
    app_name = "BerryMed Monitor"
    if sys.platform == "win32":
        app_data = Path(os.getenv('APPDATA'))
        return app_data / app_name
    else:
        # Fallback for other OS (though we're targeting Windows)
        return Path.home() / ".berrymed"

def get_config():
    """Read and return the credentials from the JSON file"""
    try:
        config_dir = get_app_data_path()
        config_file = config_dir / 'credentials.json'
        
        if not config_file.exists():
            print(f"Credentials file not found at: {config_file}")
            return None
            
        with open(config_file, 'r') as f:
            credentials = json.load(f)
            
        # Convert the stored credentials format to the format expected by Pusher
        return {
            'app_id': credentials.get('PUSHER_APP_ID'),
            'key': credentials.get('PUSHER_KEY'),
            'secret': credentials.get('PUSHER_SECRET'),
            'cluster': credentials.get('PUSHER_CLUSTER'),
            'TOTEM_ID': credentials.get('TOTEM_ID')
        }
            
    except Exception as e:
        print(f"Error reading credentials: {e}")
        return None

PUSHER_CONFIG = get_config() 