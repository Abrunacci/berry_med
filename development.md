# Development Guide – BerryMed Vitals Monitor

This document is for contributors or developers working with the BerryMed source code.

---

## 🧰 Prerequisites

- Windows 10+
- Python 3.11+
- [Poetry](https://python-poetry.org/) – Python dependency manager
- A compatible BerryMed device

---

## 📦 Setup

### 1. Clone the repository

```bash
git clone https://github.com/Abrunacci/berry_med.git
cd berry_med
```

### 2. Install dependencies

```bash
poetry install
```

### 3. Run the configuration script

```bash
poetry run python configure.py
```

You will be prompted for:

- Pusher and API credentials
- Connection type (`bt` or `usb`)
- USB port (e.g., `COM3`)
- Path to a local SSL certificate (e.g., `cacert.pem`)

The SSL cert is used for HTTPS requests and stored in `credentials.json`.

---

## 🧪 Development Mode

Run the application directly:

```bash
poetry run python app.py
```

---

## 🛠 Building Executables

### 1. Build the configuration utility

```bash
poetry run pyinstaller --noconfirm --onefile --clean --name berry-configure configure.py
```

### 2. Build the monitor application

The cert is not bundled anymore. Instead, it's provided by the user during configuration.

```bash
poetry run pyinstaller --noconfirm --onefile --clean --name berry-monitor ^
    --hidden-import asyncio ^
    --hidden-import bleak ^
    --hidden-import pysher ^
    --hidden-import src.data_parser ^
    --hidden-import src.bluetooth_manager ^
    --hidden-import aiohttp ^
    --hidden-import aiohttp.client ^
    app.py
```

Resulting `.exe` files will be in the `dist/` folder.

---

## 📁 Project Structure

- `app.py` – main monitoring loop
- `configure.py` – credential and config generator
- `config.py` – reads local credentials file
- `src/` – device interfaces, parser, communication handlers
- `pyproject.toml` – dependencies and build config (Poetry)

---

## 🧯 Troubleshooting

- Ensure COM port or Bluetooth permissions are granted.
- Log messages will print to console with `[DEBUG]` or `[ERROR]` prefixes.

---

## 📄 License

GNU GPLv3.0
