import getpass
import json
import sys

from config import get_app_data_path


def setup_credentials():
    print("BerryMed Monitor Configuration")
    print("-----------------------------")

    credentials = {
        "PUSHER_KEY": input("Pusher Key: ").strip(),
        "PUSHER_CLUSTER": input("Pusher Cluster (default 'us2'): ").strip() or "us2",
        "TOTEM_ID": input("Totem ID: ").strip(),
        "API_URL": input("API URL: ").strip(),
        "API_USERNAME": input("API Username: ").strip(),
        "API_PASSWORD": getpass.getpass("API Password: ").strip(),
        "PUBLIC_CHANNEL": input("Public Channel Name: ").strip(),
        "START_EVENT_NAME": input(
            "Start Event Name (default 'start-monitoring'): "
        ).strip()
        or "start-monitoring",
        "STOP_EVENT_NAME": input(
            "Stop Event Name (default 'stop-monitoring'): "
        ).strip()
        or "stop-monitoring",
    }

    try:
        # Get AppData directory
        config_dir = get_app_data_path()
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "credentials.json"

        # Save credentials
        with open(config_file, "w") as f:
            json.dump(credentials, f)

        print("\nConfiguration saved successfully!")
        print(f"Credentials stored in: {config_file}")

    except Exception as e:
        print(f"Error saving configuration: {e}")
        sys.exit(1)


if __name__ == "__main__":
    setup_credentials()
