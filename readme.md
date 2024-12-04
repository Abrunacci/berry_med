# BerryMed Vitals Monitor

A Python application that connects to BerryMed devices via Bluetooth LE and streams vital signs data via HTTP. The application listens for start/stop commands through a Pusher public channel.

> **Note**: This application is currently only compatible with Windows 10 or higher.

## Prerequisites

- Windows 10 or higher (required for Bluetooth LE support)
- Python 3.11 or higher
- Poetry (Python package manager)
- BerryMed compatible device

## Installation

1. Clone the repository:
```bash
git clone https://github.com/Abrunacci/berry_med.git
cd berry_med
```

2. Install Poetry if you haven't already:
```bash
curl -sSL https://install.python-poetry.org | python3
```

3. Install dependencies:
```bash
poetry install
``` 

4. Configure your credentials:
```bash
poetry run python configure.py
```

You will need to provide:
- Pusher Key (for receiving commands)
- Pusher Cluster
- Totem ID
- API URL (for sending vital signs)
- API Username
- API Password
- Public Channel Name
- Start/Stop Event Names

## Running the Application

### Development Mode

```bash
poetry run python app.py
```

### Building Executables

1. First, build the configuration tool:
```bash
poetry run pyinstaller --noconfirm --onefile --clean --name berry-configure configure.py
```

2. Then build the monitor application. First, get the certifi path:
```bash
poetry run python -c "import certifi; print(certifi.where())"
```

3. Use the path from above in the following command (replace PATH_TO_CERTIFI with the output from step 2):

For PowerShell:
```powershell
poetry run pyinstaller --noconfirm --onefile --clean --name berry-monitor `
    --hidden-import asyncio `
    --hidden-import bleak `
    --hidden-import pysher `
    --hidden-import certifi `
    --hidden-import src.data_parser `
    --hidden-import src.bluetooth_manager `
    --hidden-import aiohttp `
    --hidden-import aiohttp.client `
    --add-data "PATH_TO_CERTIFI;certifi" `
    app.py
```

For Command Prompt (CMD):
```cmd
poetry run pyinstaller --noconfirm --onefile --clean --name berry-monitor ^
    --hidden-import asyncio ^
    --hidden-import bleak ^
    --hidden-import pysher ^
    --hidden-import certifi ^
    --hidden-import src.data_parser ^
    --hidden-import src.bluetooth_manager ^
    --hidden-import aiohttp ^
    --hidden-import aiohttp.client ^
    --add-data "PATH_TO_CERTIFI;certifi" ^
    app.py
```

The executables will be created in the `dist` directory:
- `berry-configure.exe` - Configuration utility
- `berry-monitor.exe` - Main monitoring application

## Usage

1. Run `berry-configure.exe` first to set up your credentials
2. Run `berry-monitor.exe` to start monitoring your BerryMed device

## Communication Flow

1. The application listens for start/stop events on a public Pusher channel
2. When monitoring is active, vital signs data is sent via HTTP POST to the configured API URL
3. Basic authentication is used for API requests using the configured username and password

## Troubleshooting

1. **Bluetooth Connection Issues**
   - Ensure Bluetooth is enabled on your computer
   - Check if the device is powered on
   - Try restarting the device

2. **Missing DLL Errors**
   - Install the [Visual C++ Redistributable](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)

## Dependencies

- bleak: Bluetooth LE communication
- pysher: Pusher client for event listening
- aiohttp: Async HTTP client
- certifi: SSL certificate verification
- PyInstaller: Executable creation

## License

GNU GPLv3.0

## Support

For issues and feature requests, please [open an issue](https://github.com/Abrunacci/berry_med/issues) on GitHub.