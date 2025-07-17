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
   This will prompt you to enter your credentials, device settings, and path to the SSL certificate (`cacert.pem`).

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
