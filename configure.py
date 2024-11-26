import json
import getpass
import sys
from pathlib import Path
from config import get_app_data_path

def setup_credentials():
    print("BerryMed Monitor Configuration")
    print("-----------------------------")
    print("Please enter your Pusher credentials:")
    
    credentials = {
        'PUSHER_APP_ID': input("App ID: ").strip(),
        'PUSHER_KEY': input("Key: ").strip(),
        'PUSHER_SECRET': getpass.getpass("Secret: ").strip(),
        'PUSHER_CLUSTER': input("Cluster (default 'us2'): ").strip() or 'us2'
    }
    
    try:
        # Get AppData directory
        config_dir = get_app_data_path()
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / 'credentials.json'
        
        # Save credentials
        with open(config_file, 'w') as f:
            json.dump(credentials, f)
        
        print("\nConfiguration saved successfully!")
        print(f"Credentials stored in: {config_file}")
        
    except Exception as e:
        print(f"Error saving configuration: {e}")
        sys.exit(1)

if __name__ == "__main__":
    setup_credentials() 