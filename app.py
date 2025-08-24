import asyncio
import base64
import os
import sys
import time

import aiohttp
import certifi
import pysher

from config import get_config
from src.bluetooth_manager import BMPatientMonitor
from src.data_parser import BMDataParser
from src.serial_manager import PM6750USBReader

os.environ["SSL_CERT_FILE"] = get_config()["ssl_cert_file"]


class VitalsMonitor:
    def __init__(self):
        self.data_parser = BMDataParser()
        cfg = get_config()
        print(cfg)

        if cfg.get("device_connection", "bt") == "usb":
            self.monitor = PM6750USBReader(
                parser=self.data_parser,
                port=cfg.get("device_port", "COM3"),
            )
        else:
            self.monitor = BMPatientMonitor(self.data_parser, self.status_callback)
        
        # self.monitor = BMPatientMonitor(self.data_parser, self.status_callback)
        self.main_loop = None  # Almacenar el loop principal

        # Register callbacks
        self.data_parser.register_callback(
            "on_ecg_waveform_received", self.handle_ecg_wave
        )
        self.data_parser.register_callback(
            "on_spo2_waveform_received", self.handle_spo2_wave
        )
        self.data_parser.register_callback(
            "on_resp_waveform_received", self.handle_resp_wave
        )
        self.data_parser.register_callback("on_ecg_params_received", self.handle_ecg)
        self.data_parser.register_callback("on_spo2_params_received", self.handle_spo2)
        self.data_parser.register_callback(
            "on_temp_params_received", self.handle_temperature
        )
        if not isinstance(self.monitor, PM6750USBReader):
            self.data_parser.register_callback("on_nibp_params_received", self.handle_nibp)

        # Initialize credentials
        self.credentials = get_config()
        if not self.credentials:
            print("Error: No se encontraron credenciales")
            sys.exit(1)

        # Initialize Pusher subscriber for public channel
        print("Initializing Pusher subscriber...")
        self.pusher_subscriber = pysher.Pusher(
            key=self.credentials["key"],
            cluster=self.credentials["cluster"],
        )

        # Initialize HTTP session for data sending
        self.api_url = self.credentials["api_url"]
        self.auth_token = base64.b64encode(
            f"{self.credentials['api_username']}:{self.credentials['api_password']}".encode()
        ).decode()

        # Initialize channel names from config
        self.public_channel = self.credentials["public_channel"]
        self.start_event_name = self.credentials["start_event_name"]
        self.stop_event_name = self.credentials["stop_event_name"]

        self.is_sending_data = False

        # Bind connection handlers for the client
        self.pusher_subscriber.connection.bind(
            "pusher:connection_established", self.connect_handler
        )
        self.pusher_subscriber.connect()

        self.command_queue = asyncio.Queue()

    def status_callback(self, message: str):
        """Callback for device status updates"""
        print(f"[BERRY STATUS] {message}")

    # Handler methods
    def handle_ecg_wave(self, value: int):
        
        pass

    def handle_spo2_wave(self, value: int):
        
        pass

    def handle_resp_wave(self, value: int):
        
        pass

    def handle_ecg(self, states: int, heart_rate: int, resp_rate: int):
        
        pass

    def handle_spo2(self, states: int, spo2: int, pulse_rate: int):
        
        pass

    def handle_temperature(self, states: int, temp: float):
        
        pass

    def handle_nibp(self, states: int, cuff: int, sys: int, mean: int, dia: int):
        
        pass

    def connect_handler(self, data):
        """Handler for successful connection"""
        print(f"[DEBUG] Connected to Pusher, subscribing to: {self.public_channel}")
        try:
            channel = self.pusher_subscriber.subscribe(self.public_channel)
            if channel:
                channel.bind(self.start_event_name, self.handle_start_event)
                channel.bind(self.stop_event_name, self.handle_stop_event)
                channel.bind('start-blood-pressure', self.handle_blood_pressure_event)
                print(f"[DEBUG] Successfully subscribed to {self.public_channel}")
            else:
                print("[ERROR] Failed to subscribe to channel")
        except Exception as e:
            print(f"[ERROR] Subscription error: {e}")

    def handle_start_event(self, event_data):
        """Handle the start monitoring event"""
        try:
            self.is_sending_data = True
            print("[DEBUG] Starting data transmission")
        except Exception as e:
            print(f"[ERROR] Error processing start event: {e}")

    def handle_stop_event(self, event_data):
        """Handle the stop monitoring event"""
        self.is_sending_data = False
        if isinstance(self.monitor, PM6750USBReader):
            self.monitor.reset_state()
        self.monitor.parser.reset_data()
        print("[DEBUG] Stopped data transmission")

    def handle_blood_pressure_event(self, event_data):
        """Handle the start blood pressure measurement event"""
        try:
            print("[DEBUG] Starting blood pressure measurement")
            if self.main_loop:
                future = asyncio.run_coroutine_threadsafe(
                    self.monitor.start_nibp(),
                    self.main_loop
                )
                future.result()  # Espera el resultado
            else:
                print("[ERROR] Main event loop not initialized")
        except Exception as e:
            print(f"[ERROR] Error starting blood pressure measurement: {e}")

    async def send_data(self):
        """Send data via HTTP POST"""
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    if not self.is_sending_data:
                        await asyncio.sleep(1)
                        continue

                    data = self.data_parser.get_current_data()
                    if not self._is_valid_data(data):
                        await asyncio.sleep(1)
                        continue

                    # Prepare the payload
                    payload = {"timestamp": int(time.time() * 1000), "data": data}

                    # Send data with retry logic
                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            print(f"[DEBUG] Sending data: {data}")
                            async with session.post(
                                self.api_url,
                                json=payload,
                                headers={
                                    "Authorization": f"Basic {self.auth_token}",
                                    "Content-Type": "application/json",
                                },
                            ) as response:
                                if response.status == 200:
                                    
                                    print("[DATA] Vital signs sent successfully")
                                    break
                                else:
                                    print(
                                        f"[ERROR] API returned status {response.status}"
                                    )

                        except Exception as e:
                            if attempt == max_retries - 1:
                                print(
                                    f"[ERROR] Failed to send data after {max_retries} attempts: {e}"
                                )
                            else:
                                print(
                                    f"[WARN] Retry attempt {attempt + 1} after error: {e}"
                                )
                                await asyncio.sleep(0.5)

                except Exception as e:
                    print(f"[ERROR] Error in send_data loop: {e}")

                await asyncio.sleep(1)

    def _is_valid_data(self, data):
        """Check if data contains any valid measurements"""
        if not data or not isinstance(data, dict):
            return False

        # Check vital signs
        vital_signs = data.get("vitalSigns", {})
        if not vital_signs:
            return False

        # Check if all vital signs are empty/default
        default_values = {"-", "- -", "- - /- -"}
        all_vitals_empty = all(
            str(value) in default_values for value in vital_signs.values()
        )

        # Check waveforms
        spo2_empty = all(v == 0 for v in data.get("spo2", []))
        ecg_empty = len(data.get("ecg", [])) == 0
        resp_empty = len(data.get("resp", [])) == 0

        # Data is valid only if we have some vital signs OR some non-zero waveform data
        return not (all_vitals_empty and spo2_empty and ecg_empty and resp_empty)

    async def run(self):
        self.main_loop = asyncio.get_running_loop()  # Guardar referencia al loop principal
        while True:
            try:
                print("\n[BERRY] Attempting to connect to Berry device...")
                connected = await self.monitor.connect()
                if not connected:
                    print("[BERRY] Connection failed, retrying in 5 seconds...")
                    await asyncio.sleep(5)
                    continue

                asyncio.create_task(self.send_data())
                asyncio.create_task(self.process_commands())
                print("[BERRY] Connection successful, starting data monitoring...")
                while True:
                    await asyncio.sleep(0.1)
            except Exception as e:
                print(f"[ERROR] Berry connection error: {str(e)}")
                await asyncio.sleep(5)

    async def process_commands(self):
        while True:
            command = await self.command_queue.get()
            if command == "start_nibp":
                await self.monitor.start_nibp()
            await asyncio.sleep(0.1)


async def main():
    monitor = VitalsMonitor()
    try:
        await monitor.run()
    except Exception as e:
        print(f"Error during initialization: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
