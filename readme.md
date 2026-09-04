# BerryMed Vitals Monitor

A simple tool for monitoring vital signs from BerryMed devices over Bluetooth or USB.

> **Note:** This application currently only supports **Windows 10 or higher**.

---

## 🧰 Requirements

- Windows 10+
- A compatible BerryMed device (Bluetooth or USB)
- `berry-configure.exe` and `berry-monitor.exe` from the `dist/` folder

---

## ▶️ Getting Started

1. Run the configuration utility:
   ```
   berry-configure.exe
   ```
   
This tool will prompt you to enter the required settings, which are saved to `%APPDATA%\BerryMed Monitor\credentials.json` on Windows and used automatically by the monitoring app.

#### Required fields:

- **Pusher Key / Cluster**  
  Provided by the platform that controls event broadcasting.  
  ⚠️ Make sure the Pusher **channel is PUBLIC**, otherwise it will not work.

- **Totem ID**  
  A unique identifier for the monitoring device or station.

- **API URL / Username / Password**  
  This is the destination where your device will POST vital signs data.  
  Credentials must be valid and have write access.

- **Public Channel Name**  
  Name of the Pusher channel you will be listening to.  
  Must match the backend configuration exactly.

- **Start / Stop Event Names**  
  Event identifiers (e.g., `start-monitoring`, `stop-monitoring`)  
  These control when the data stream begins or ends.

- **Device Connection Type**  
  `bt` for Bluetooth or `usb` for USB serial connection.

- **USB Port (only if using USB mode)**  
  Example: `COM3` (Windows). This is the Berry monitor port.

- **Thermometer (optional USB IR)**  
  - `THERMOMETER_ENABLED`: `true` or `false`  
  - `THERMOMETER_PORT`: separate COM port (e.g. `COM5`)  
  - `THERMOMETER_BAUD`: usually `115200`  
  Temperature is sent in `vitalSigns.temperature` (Celsius). Thermometer data takes priority over Berry; if disabled or unavailable, Berry value is used (e.g. `"-"`).

- **Path to SSL Certificate (.pem)**  
  The certificate used to validate secure HTTPS requests.  
  This must be a valid `.pem` file. You can:
  - Use one provided by your company
  - Download a public CA bundle (see below)
  - Or extract one manually using OpenSSL



### Need help getting a certificate?

See [About the SSL Certificate](#about-the-ssl-certificate) for instructions.

---

2. Start monitoring:
   ```
   berry-monitor.exe
   ```

---

## 🔄 How It Works

- The application subscribes to a public Pusher channel.
- It listens for `start-monitoring` and `stop-monitoring` events.
- While monitoring is active, vital signs are sent via HTTP POST to your configured API endpoint using Basic Auth.
- Temperature appears only under `data.vitalSigns.temperature` (no separate `thermometer` field in the payload).

---

## 📚 Documentación

| Documento | Para qué |
|---|---|
| [`docs/backend.md`](docs/backend.md) | **Qué manda el tótem al backend**: endpoints, autenticación, los dos payloads con ejemplos reales, y los eventos de Pusher que escucha. Si lo vas a mandar al equipo de backend, la [§0](docs/backend.md#0-qué-cambia-para-el-backend) es el resumen de qué tienen que cambiar. |
| [`docs/configuracion.md`](docs/configuracion.md) | **Todas las claves de configuración**, qué hace cada una y cómo setearlas. |
| [`docs/health.md`](docs/health.md) | El contrato del `/health` en detalle: qué se mide y cómo se decide `ok`/`degraded`/`down`. |
| [`docs/ecg.md`](docs/ecg.md) | Las 7 derivaciones, el muestreo y la línea de base. |
| [`docs/protocolo_berry.md`](docs/protocolo_berry.md) | El protocolo del PM6750, aguas arriba de todo. |
| [`development.md`](development.md) | Build, tests y capturas del equipo. |

## 🧯 Troubleshooting

- Make sure your BerryMed device is turned on and Bluetooth is enabled (if using BT mode).
- For USB mode, ensure the correct COM port is selected for the Berry (`DEVICE_PORT`).
- Thermometer uses a **different** COM port (`THERMOMETER_PORT`); both cannot share the same port.
- If temperature never updates from the USB thermometer, rebuild `berry-monitor.exe` with `poetry run pyinstaller berry-monitor.spec --clean --noconfirm` and check startup logs for `[THERM] Connected to COMx`.
- Install the [Visual C++ Redistributable](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist) if you encounter missing DLL errors.

---

## ▶️ Usage

1. Run `berry-configure.exe` to set up your credentials and certificate path.
2. Then run `berry-monitor.exe` to start monitoring your BerryMed device.

---

## Building from source (Windows)

Guía resumida; el paso a paso completo está en [development.md — Building Executables on Windows](development.md#building-executables-on-windows-step-by-step).

1. Instalar Python 3.11+ y [Poetry](https://python-poetry.org/) en Windows.
2. Clonar el repo y abrir terminal en la carpeta `berry_med`.
3. `poetry install`
4. `poetry run pyinstaller berry-configure.spec --clean --noconfirm`
5. `poetry run pyinstaller berry-monitor.spec --clean --noconfirm`
6. Ejecutar `dist\berry-configure.exe` (configura credenciales en AppData).
7. Ejecutar `dist\berry-monitor.exe` (monitoreo).

Salida en `dist\`: `berry-configure.exe` y `berry-monitor.exe`. Tras cambios de código, repetir el build del exe afectado con `--clean`.

---

### ⚠️ Common Issues

- 🔐 **SSL certificate error**  
  You must specify a valid path to a `.pem` certificate file during configuration.  
  This file is required to send data securely over HTTPS.

- 📡 **No data received from Pusher channel**  
  Make sure the channel you configured is **public**, not private or protected.  
  The application does not support private channels or those requiring auth.

---



## 📄 License

GNU GPLv3.0

---

## 🛟 Support

For help or feature requests, please [open an issue](https://github.com/Abrunacci/berry_med/issues).
