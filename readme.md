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
   
This tool will prompt you to enter the required settings, which will be saved locally in a file (`credentials.json`) and used automatically by the monitoring app.

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
  Example: `COM3` (Windows) or `/dev/ttyUSB0` (Linux)

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

---

## 🧯 Troubleshooting

- Make sure your BerryMed device is turned on and Bluetooth is enabled (if using BT mode).
- For USB mode, ensure the correct COM port is selected.
- Install the [Visual C++ Redistributable](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist) if you encounter missing DLL errors.

---

## ▶️ Usage

1. Run `berry-configure.exe` to set up your credentials and certificate path.
2. Then run `berry-monitor.exe` to start monitoring your BerryMed device.

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
