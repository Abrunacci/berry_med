import asyncio
import json
import pusher
import pysher
from src.data_parser import BMDataParser
from src.bluetooth_manager import BMPatientMonitor
from config import get_config
import sys

class VitalsMonitor:
    def __init__(self):
        self.data_parser = BMDataParser()
        self.monitor = BMPatientMonitor(self.data_parser, self.status_callback)
        
        # Register callbacks
        self.data_parser.register_callback("on_ecg_waveform_received", self.handle_ecg_wave)
        self.data_parser.register_callback("on_spo2_waveform_received", self.handle_spo2_wave)
        self.data_parser.register_callback("on_resp_waveform_received", self.handle_resp_wave)
        self.data_parser.register_callback("on_ecg_params_received", self.handle_ecg)
        self.data_parser.register_callback("on_spo2_params_received", self.handle_spo2)
        self.data_parser.register_callback("on_temp_params_received", self.handle_temperature)
        self.data_parser.register_callback("on_nibp_params_received", self.handle_nibp)
        
        # Initialize credentials
        self.credentials = get_config()
        if not self.credentials:
            print("Error: No se encontraron credenciales")
            sys.exit(1)
            
        # Initialize Pusher for sending events
        self.pusher_server = pusher.Pusher(
            app_id=self.credentials['app_id'],
            key=self.credentials['key'],
            secret=self.credentials['secret'],
            cluster=self.credentials['cluster'],
            ssl=True
        )
        
        # Initialize Pysher for receiving events - PROPER PRIVATE CHANNEL SETUP
        print("Initializing Pusher subscriber...")
        self.pusher_client = pysher.Pusher(
            key=self.credentials['key'],
            cluster=self.credentials['cluster'],
            secret=self.credentials['secret']  # Add secret for private channels
        )
        
        # Create auth function for private channels
        def auth(socket_id, channel_name):
            return self.pusher_server.authenticate(
                channel=channel_name,
                socket_id=socket_id
            )
        
        self.pusher_client.authenticate = auth
        
        self.channel_name = f"private-urgentcareid.share-{self.credentials['TOTEM_ID']}"
        self.target_channel = None
        
        # Bind connection handlers for the client
        self.pusher_client.connection.bind('pusher:connection_established', self.connect_handler)
        self.pusher_client.connect()

        # Initialize channel names
        self.totem_id = self.credentials['TOTEM_ID']
        self.initial_channel = f"private-urgentcareid.share-{self.totem_id}"
        self.target_channel = None  # Will be set when we receive the share event

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

    async def initialize_pusher(self):
        """Initialize Pusher connection and subscribe to initial channel"""
        print(f"Initializing Pusher with initial channel: {self.initial_channel}")
        
        self.pusher_subscriber = pysher.Pusher(
            key=self.credentials['key'],
            cluster=self.credentials['cluster'],
            auth_endpoint='https://guardiavirtualmedica.app/api/broadcasting/auth'
        )
        
        # Add connection handlers
        self.pusher_subscriber.connection.bind('connected', self.connect_handler)
        self.pusher_subscriber.connection.bind('error', self.connect_error_handler)
        
        await self.pusher_subscriber.connect()
        print("Pusher connection established")

    def connect_handler(self, data):
        """Handler for successful connection"""
        print(f"[DEBUG] Connected to Pusher, subscribing to: {self.initial_channel}")
        try:
            channel = self.pusher_subscriber.subscribe(self.initial_channel)
            if channel:
                print(f"[DEBUG] Successfully subscribed to {self.initial_channel}")
                channel.bind('share_urgentcare_id', self.handle_share_event)
                print("[DEBUG] Waiting for share_urgentcare_id event...")
            else:
                print("[ERROR] Failed to subscribe to initial channel")
        except Exception as e:
            print(f"[ERROR] Subscription error: {e}")

    def handle_share_event(self, event_data):
        """Handle the share event and switch to target channel"""
        try:
            data = json.loads(event_data)
            urgent_care_id = data.get('urgentCareId')
            
            if urgent_care_id:
                self.target_channel = f"private-urgent-care.{urgent_care_id}"
                print(f"[DEBUG] Switching to target channel: {self.target_channel}")
                
                # Subscribe to new channel
                channel = self.pusher_subscriber.subscribe(self.target_channel)
                if channel:
                    print(f"[DEBUG] Successfully subscribed to target channel")
                    # Add any bindings needed for the target channel
                    # channel.bind('some-event', self.handle_target_channel_event)
            else:
                print("[ERROR] Received share event without urgentCareId")
                
        except json.JSONDecodeError:
            print("[ERROR] Invalid JSON in share event")
        except Exception as e:
            print(f"[ERROR] Error processing share event: {e}")

    async def send_data(self):
        """Send data to the appropriate channel"""
        while True:
            try:
                data = self.data_parser.get_current_data()
                channel = self.target_channel or self.initial_channel
                
                self.pusher_server.trigger(
                    channels=[channel],
                    event_name='vitals-event',
                    data=data
                )
                print(f"[DEBUG] Sent data to channel: {channel}")
                
            except Exception as e:
                print(f"[ERROR] Error sending data: {e}")
            await asyncio.sleep(1)

    async def run(self):
        try:
            # Instead of connecting to Berry device, we'll just start sending test data
            print("Starting test mode - no Berry device connected")
            asyncio.create_task(self.send_test_data())
            while True:
                await asyncio.sleep(0.1)
        except Exception as e:
            print(f"Error: {str(e)}")

    async def send_test_data(self):
        """Simulates data sending for testing purposes"""
        test_counter = 0
        while True:
            try:
                # Simulate some varying vital signs
                test_data = {
                    "spo2": {
                        "wave": [test_counter % 100],  # Simulated wave between 0-99
                        "value": 98  # Normal SPO2
                    },
                    "vitalSigns": {
                        "heartRate": 75 + (test_counter % 10),  # Varying heart rate
                        "temperature": 36.5 + (test_counter % 10) / 10,  # Varying temperature
                        "bloodPressure": {
                            "systolic": 120,
                            "diastolic": 80
                        }
                    }
                }
                
                print("\nSending Test Data:")
                print(json.dumps(test_data, indent=2))
                
                # Send using Pusher to private channel
                self.pusher_server.trigger(
                    channels=[self.channel_name],
                    event_name='vitals-event',
                    data=test_data
                )
                
                test_counter += 1
            except Exception as e:
                print(f"Error sending test data: {e}")
            await asyncio.sleep(2)  # Send data every 2 seconds

    def connect_error_handler(self, data):
        """Handler for connection error"""
        print(f"[ERROR] Connection error: {data}")

async def main():
    monitor = VitalsMonitor()
    try:
        await monitor.run()
    except Exception as e:
        print(f"Error during initialization: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())