import asyncio
from typing import Callable, Optional

from bleak import BleakClient, BleakScanner

from src.pm6750_protocol import (
    CMD_NIBP,
    build_command,
    build_nibp_commands,
    build_startup_commands,
)


class BMPatientMonitor:
    def __init__(self, data_parser, status_callback: Callable, device_config=None):
        self.data_parser = data_parser
        self.status_callback = status_callback
        self.device_config = device_config or {}
        self.client: Optional[BleakClient] = None
        self.device = None
        self.connected = False

        # BLE UUIDs
        self.DEVICE_NAME = "BerryMed"
        self.SERVICE_UUID = "49535343-fe7d-4ae5-8fa9-9fafd205e455"
        self.CHAR_RECEIVE_UUID = "49535343-1e4d-4bd9-ba61-23c647249616"
        self.CHAR_SEND_UUID = "49535343-8841-43f4-a8d4-ecbe34729bb3"

        self.reconnect_interval = 5  # seconds between reconnection attempts
        self.is_device_active = False
        self.last_data_timestamp = 0

    async def connect(self) -> bool:
        while True:
            try:
                self.status_callback("Scanning for BerryMed device...")

                devices = await BleakScanner.discover()
                for device in devices:
                    if device.name and self.DEVICE_NAME in device.name:
                        self.device = device
                        break

                if not self.device:
                    self.status_callback(
                        "BerryMed device not found, retrying in 5 seconds..."
                    )
                    await asyncio.sleep(self.reconnect_interval)
                    continue

                self.status_callback(f"Connecting to {self.device.name}...")
                self.client = BleakClient(self.device)
                await self.client.connect()
                self.connected = True

                await asyncio.sleep(1)

                await self.client.start_notify(
                    self.CHAR_RECEIVE_UUID, self._handle_data
                )

                await self.start_monitoring()

                self.status_callback("Connected to BerryMed")
                return True

            except Exception as e:
                self.status_callback(
                    f"Connection error: {str(e)}, retrying in 5 seconds..."
                )
                self.connected = False
                await asyncio.sleep(self.reconnect_interval)

    async def start_monitoring(self):
        """Configura el módulo y habilita los streams.

        Esta vía antes no mandaba ningún comando: dependía de que el módulo
        arrancara transmitiendo solo y con la configuración que tuviera guardada.
        Ahora manda la misma secuencia que la vía USB (`pm6750_protocol`), así
        ganancia y modo de ECG quedan explícitos en las dos.

        Es best-effort: si falla, se avisa pero no se corta la conexión, porque
        el equipo puede estar ya transmitiendo por su cuenta.
        """
        if not (self.client and self.client.is_connected):
            return
        try:
            for a1, a2 in build_startup_commands(
                self.device_config, log=self.status_callback
            ):
                await self.client.write_gatt_char(
                    self.CHAR_SEND_UUID, bytearray(build_command(a1, a2))
                )
                # El módulo pierde comandos si llegan pegados.
                await asyncio.sleep(0.05)
            self.status_callback("Configuración enviada al PM6750")
        except Exception as e:
            self.status_callback(f"No se pudo configurar el PM6750: {e}")

    async def disconnect(self):
        if self.client and self.client.is_connected:
            await self.client.disconnect()
            self.connected = False
            self.status_callback("Disconnected")

    async def start_nibp(self):
        if self.client and self.client.is_connected:
            # Modo y presión objetivo van acá, justo antes de arrancar: el
            # manual pide setearlos inmediatamente antes de cada medición.
            for a1, a2 in build_nibp_commands(
                self.device_config, log=self.status_callback
            ):
                await self.client.write_gatt_char(
                    self.CHAR_SEND_UUID, bytearray(build_command(a1, a2))
                )
                await asyncio.sleep(0.05)

    def _handle_data(self, _, data: bytearray):
        # Update last data timestamp
        self.last_data_timestamp = asyncio.get_event_loop().time()
        self.is_device_active = True
        self.data_parser.add_data(data)

    async def check_connection(self):
        """Check if device is still connected and sending valid data"""
        current_time = asyncio.get_event_loop().time()
        if self.connected and (current_time - self.last_data_timestamp) > 5:
            self.is_device_active = False
            if not await self.client.is_connected():
                self.status_callback("Device disconnected, attempting to reconnect...")
                self.connected = False
                await self.connect()

    def send_nibp_command(self):
        """Send NIBP command synchronously"""
        if self.client and self.client.is_connected:
            return bytearray(build_command(CMD_NIBP, 0x01))
        return None
