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

4. Configure your Pusher credentials:
```bash
poetry run python configure.py
```

## Running the Application

### Development Mode

```bash
poetry run python app.py
```

### Building Executables

1. First, build the configuration tool:
```bash
poetry run pyinstaller --noconfirm --onefile --clean --name berry-configure ^
    --hidden-import=json ^
    --hidden-import=getpass ^
    --hidden-import=pathlib ^
    configure.py
```

2. Then build the monitor application:
```bash
poetry run pyinstaller berry-monitor.spec
```

The executables will be created in the `dist` directory:
- `berry-configure.exe` - Configuration utility
- `berry-monitor.exe` - Main monitoring application

## Usage

1. Run `berry-configure.exe` first to set up your Pusher credentials
2. Run `berry-monitor.exe` to start monitoring your BerryMed device

## Troubleshooting

1. **Bluetooth Connection Issues**
   - Ensure Bluetooth is enabled on your computer
   - Check if the device is powered on
   - Try restarting the device

2. **Missing DLL Errors**
   - Install the [Visual C++ Redistributable](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)

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