import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv

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
    if getattr(sys, 'frozen', False):
        # Running as compiled executable
        try:
            config_dir = get_app_data_path()
            config_dir.mkdir(parents=True, exist_ok=True)
            config_file = config_dir / 'credentials.json'
            
            if not config_file.exists():
                print(f"Credentials not found. Please run the configuration tool first.")
                print(f"Expected location: {config_file}")
                sys.exit(1)

            with open(config_file, 'r') as f:
                creds = json.load(f)
                return {
                    'app_id': creds.get('PUSHER_APP_ID'),
                    'key': creds.get('PUSHER_KEY'),
                    'secret': creds.get('PUSHER_SECRET'),
                    'cluster': creds.get('PUSHER_CLUSTER', 'us2')
                }
        except Exception as e:
            print(f"Error loading credentials: {e}")
            sys.exit(1)
    else:
        # Development environment - use .env
        load_dotenv()
        return {
            'app_id': os.getenv('PUSHER_APP_ID'),
            'key': os.getenv('PUSHER_KEY'),
            'secret': os.getenv('PUSHER_SECRET'),
            'cluster': os.getenv('PUSHER_CLUSTER', 'us2')
        }

PUSHER_CONFIG = get_config() 