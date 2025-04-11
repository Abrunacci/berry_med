from typing import Callable, Optional
from bleak import BleakClient, BleakScanner
import asyncio

class BMPatientMonitor:
    def __init__(self, data_parser, status_callback: Callable):
        self.data_parser = data_parser
        self.status_callback = status_callback
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
                    self.status_callback("BerryMed device not found, retrying in 5 seconds...")
                    await asyncio.sleep(self.reconnect_interval)
                    continue

                self.status_callback(f"Connecting to {self.device.name}...")
                self.client = BleakClient(self.device)
                await self.client.connect()
                self.connected = True
                
                await asyncio.sleep(1)
                
                await self.client.start_notify(
                    self.CHAR_RECEIVE_UUID,
                    self._handle_data
                )

                self.status_callback("Connected to BerryMed")
                return True

            except Exception as e:
                self.status_callback(f"Connection error: {str(e)}, retrying in 5 seconds...")
                self.connected = False
                await asyncio.sleep(self.reconnect_interval)

    async def disconnect(self):
        if self.client and self.client.is_connected:
            await self.client.disconnect()
            self.connected = False
            self.status_callback("Disconnected")

    async def start_nibp(self):
        if self.client and self.client.is_connected:
            command = bytearray([0x55, 0xaa, 0x04, 0x02, 0x01, 0xf8])
            await self.client.write_gatt_char(self.CHAR_SEND_UUID, command)

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