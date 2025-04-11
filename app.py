import asyncio
import json
import pusher
from src.data_parser import BMDataParser
from src.bluetooth_manager import BMPatientMonitor
from config import PUSHER_CONFIG

class VitalsMonitor:
    def __init__(self):
        self.data_parser = BMDataParser()
        self.monitor = BMPatientMonitor(self.data_parser, self.status_callback)
        
        # Initialize Pusher server
        self.pusher_server = pusher.Pusher(
            app_id=PUSHER_CONFIG['app_id'],
            key=PUSHER_CONFIG['key'],
            secret=PUSHER_CONFIG['secret'],
            cluster=PUSHER_CONFIG['cluster']
        )
        
        # Register callbacks
        self.data_parser.register_callback("on_ecg_waveform_received", self.handle_ecg_wave)
        self.data_parser.register_callback("on_spo2_waveform_received", self.handle_spo2_wave)
        self.data_parser.register_callback("on_resp_waveform_received", self.handle_resp_wave)
        self.data_parser.register_callback("on_ecg_params_received", self.handle_ecg)
        self.data_parser.register_callback("on_spo2_params_received", self.handle_spo2)
        self.data_parser.register_callback("on_temp_params_received", self.handle_temperature)
        self.data_parser.register_callback("on_nibp_params_received", self.handle_nibp)

    def status_callback(self, message: str):
        if "Connected to BerryMed" in message:
            print(message)

    # Handler methods
    def handle_ecg_wave(self, value: int): pass
    def handle_spo2_wave(self, value: int): pass
    def handle_resp_wave(self, value: int): pass
    def handle_ecg(self, states: int, heart_rate: int, resp_rate: int): pass
    def handle_spo2(self, states: int, spo2: int, pulse_rate: int): pass
    def handle_temperature(self, states: int, temp: float): pass
    def handle_nibp(self, states: int, cuff: int, sys: int, mean: int, dia: int): pass

    async def send_data(self):
        while True:
            try:
                data = self.data_parser.get_current_data()
                print("\nWaveforms and Vital Signs:")
                print(f"SPO2: {data['spo2']}")
                print(f"Vitals: {data['vitalSigns']}")
                
                # Send using Pusher
                self.pusher_server.trigger(
                    channels=['testing-staging'],
                    event_name='vitals-event',
                    data=data
                )
            except Exception as e:
                print(f"Error sending data: {e}")
            await asyncio.sleep(1)

    async def run(self):
        try:
            connected = await self.monitor.connect()
            if not connected:
                return

            asyncio.create_task(self.send_data())
            while True:
                await asyncio.sleep(0.1)
        except Exception as e:
            print(f"Error: {str(e)}")
        finally:
            await self.monitor.disconnect()

async def main():
    monitor = VitalsMonitor()
    await monitor.run()

if __name__ == "__main__":
    asyncio.run(main())