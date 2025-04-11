# BerryMed Vitals Monitor

A Python application that connects to BerryMed devices via Bluetooth LE and streams vital signs data through Pusher.

## Prerequisites

- Python 3.11 or higher
- Poetry (Python package manager)
- Windows 10 or higher (for Bluetooth LE support)
- BerryMed compatible device

## Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/berry-integration.git
cd berry-integration
```

2. Install Poetry if you haven't already:
```bash
curl -sSL https://install.python-poetry.org | python3
```

3. Install dependencies:
```bash
poetry install
``` 

4. Create a `.env` file in the root directory with your Pusher credentials:
```env
PUSHER_APP_ID=<your-app-id>
PUSHER_KEY=<your-key>
PUSHER_SECRET=<your-secret>
PUSHER_CLUSTER=<your-cluster>
```

## Running the Application

### Development Mode

```bash
poetry run python app.py
```

### Building Executable

1. Create the executable:
```bash
poetry run pyinstaller --onefile --name berry-monitor app.py
```

The executable will be created in the `dist` directory as `berry-monitor.exe`.

## Project Structure

```
berry-integration/
├── app.py              # Main application
├── config.py           # Configuration settings
├── src/
│   ├── data_parser.py    # Data parsing logic
│   └── bluetooth_manager.py  # BLE connection handling
├── .env               # Environment variables (not in repo)
├── pyproject.toml    # Project dependencies
└── README.md
```

## Usage

1. Connect your BerryMed device and ensure it's powered on
2. Run the executable `berry-monitor.exe`
3. The application will:
   - Automatically scan for BerryMed devices
   - Connect to the first available device
   - Start streaming vital signs data to Pusher
   - Display connection status and data in the console

## Troubleshooting

1. **Bluetooth Connection Issues**
   - Ensure Bluetooth is enabled on your computer
   - Check if the device is powered on
   - Try restarting the device

2. **Missing DLL Errors**
   - Install the [Visual C++ Redistributable](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)

3. **Environment Variables**
   - Verify your Pusher credentials in the `.env` file
   - Make sure the `.env` file is in the same directory as the executable

## Dependencies

- bleak: Bluetooth LE communication
- pusher: Real-time data streaming
- python-dotenv: Environment variable management
- PyInstaller: Executable creation

## Building from Source

To modify and rebuild the application:

1. Make your changes to the source code
2. Update version in `pyproject.toml` if needed
3. Build new executable:
```bash
# Clean previous builds
rm -rf build dist
# Create new executable
poetry run pyinstaller --onefile --name berry-monitor app.py
```

The new executable will be in `dist/berry-monitor.exe`

## License

GNU GPLv3.0

## Support

For issues and feature requests, please [open an issue](https://github.com/yourusername/berry-integration/issues) on GitHub.