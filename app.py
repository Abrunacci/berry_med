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
        """Callback for device status updates"""
        print(f"[BERRY STATUS] {message}")

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
            channel = self.pusher_client.subscribe(self.initial_channel)
            if channel:
                channel.bind('share_urgentcare_id', self.handle_share_event)
                print(f"[DEBUG] Successfully subscribed to {self.initial_channel}")
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
                self.current_channel = self.pusher_client.subscribe(self.target_channel)
                if self.current_channel:
                    self.current_channel.bind('doctor.video.end', self.handle_stop_sending_data)
                    print(f"[DEBUG] Setting up {self.target_channel} as target channel")
                else:
                    print(f"[ERROR] Failed to subscribe to {self.target_channel}")
            else:
                print("[ERROR] Received share event without urgentCareId")
                
        except json.JSONDecodeError:
            print("[ERROR] Invalid JSON in share event")
        except Exception as e:
            print(f"[ERROR] Error processing share event: {e}")

    def handle_stop_sending_data(self, event_data):
        """Handle the stop sending data event"""
        try:
            self.pusher_client.unsubscribe(self.current_channel)
            self.target_channel = None
            print(f"[DEBUG] TARGET CHANNEL SETTED AS NONE")
        except Exception as e:
            print(f"[ERROR] Error processing stop sending data event: {e}")

    async def send_data(self):
        """Send data to the appropriate channel"""
        while True:
            try:
                data = self.data_parser.get_current_data()
                channel = self.target_channel
                
                # Skip sending if no target channel
                if not channel:
                    print("[DEBUG] No target channel set - cannot send data")
                    await asyncio.sleep(1)
                    continue
                
                # Skip empty or invalid data
                if not self._is_valid_data(data):
                    print("[DEBUG] Skipping invalid/empty data")
                    await asyncio.sleep(1)
                    continue
                    
                print("[DATA] Sending vital signs:")
                print(json.dumps(data.get('vitalSigns', {}), indent=2))
                
                self.pusher_server.trigger(
                    channels=[channel],
                    event_name='vitals-event',
                    data=data
                )
                
            except Exception as e:
                print(f"[ERROR] Error sending data: {e}")
            await asyncio.sleep(1)

    def _is_valid_data(self, data):
        """Check if data contains any valid measurements"""
        if not data or not isinstance(data, dict):
            return False
        
        # Check vital signs
        vital_signs = data.get('vitalSigns', {})
        if not vital_signs:
            return False
        
        # Check if all vital signs are empty/default
        default_values = {'-', '- -', '- - /- -'}
        all_vitals_empty = all(
            str(value) in default_values 
            for value in vital_signs.values()
        )
            
        # Check waveforms
        spo2_empty = all(v == 0 for v in data.get('spo2', []))
        ecg_empty = len(data.get('ecg', [])) == 0
        resp_empty = len(data.get('resp', [])) == 0
        
        # Data is valid only if we have some vital signs OR some non-zero waveform data
        return not (all_vitals_empty and spo2_empty and ecg_empty and resp_empty)

    async def run(self):
        try:
            print("\n[BERRY] Attempting to connect to Berry device...")
            connected = await self.monitor.connect()
            if not connected:
                return
            
            asyncio.create_task(self.send_data())
            print("[BERRY] Connection successful, starting data monitoring...")
            while True:
                await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[ERROR] Berry connection error: {str(e)}")

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