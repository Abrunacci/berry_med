from typing import Callable, Dict, Optional, Tuple
import asyncio

class BMDataParser:
    PACKAGE_MIN_LENGTH = 4
    PACKAGE_HEADER = [0x55, 0xaa]
    
    def __init__(self):
        self.raw_buffer = []
        self.callbacks: Dict[int, Tuple[str, Optional[Callable]]] = {
            0x01: ("on_ecg_waveform_received", None),
            0x02: ("on_ecg_params_received", None),
            0x03: ("on_nibp_params_received", None),
            0x04: ("on_spo2_params_received", None),
            0x05: ("on_temp_params_received", None),
            0x30: ("on_ecg_peak_received", None),
            0x31: ("on_spo2_peak_received", None),
            0xfe: ("on_spo2_waveform_received", None),
            0xff: ("on_resp_waveform_received", None)
        }
        
        # Data storage with default values
        self.data = {
            'spo2': [],          # SpO2 waveform
            'ecg': [],           # ECG waveform
            'resp': [],          # Respiratory waveform
            'vitalSigns': {
                'heartRate': '- -',
                'nibp': '- - /- -',
                'spo2Pulse': '- - /- -',
                'temperature': '- -',
                'respRate': '- -'
            }
        }
        self.max_waveform_points = 25
        self.last_update_time = 0

    def register_callback(self, name: str, callback: Callable) -> None:
        for key, (callback_name, _) in self.callbacks.items():
            if callback_name == name:
                self.callbacks[key] = (callback_name, callback)
                break

    def add_data(self, data: bytearray) -> None:
        self.raw_buffer.extend(data)
        
        while len(self.raw_buffer) >= self.PACKAGE_MIN_LENGTH:
            try:
                start_idx = self._find_package_start()
                if start_idx == -1:
                    self.raw_buffer = self.raw_buffer[-1:]
                    continue
                
                package_length = self.raw_buffer[start_idx + 2]
                end_idx = start_idx + package_length + 2
                
                if end_idx > len(self.raw_buffer):
                    break
                
                package = self.raw_buffer[start_idx:end_idx]
                self.raw_buffer = self.raw_buffer[end_idx:]
                
                if self._check_sum(package):
                    self._parse_package(package)
                    
            except Exception as e:
                print(f"Error processing package: {e}")
                self.raw_buffer = self.raw_buffer[-1:]

    def _find_package_start(self) -> int:
        for i in range(len(self.raw_buffer) - 1):
            if (self.raw_buffer[i] == self.PACKAGE_HEADER[0] and 
                self.raw_buffer[i + 1] == self.PACKAGE_HEADER[1]):
                return i
        return -1

    def _check_sum(self, package: list) -> bool:
        checksum = ~sum(package[2:-1]) & 0xFF
        return checksum == package[-1]

    def _format_value(self, value) -> str:
        """Convert 0 to '-' or format the value as string"""
        return '-' if value == 0 else str(value)

    def _parse_package(self, package: list) -> None:
        package_type = package[3]
        if package_type not in self.callbacks:
            return
            
        callback_name, callback = self.callbacks[package_type]
        if callback is None:
            return

        try:
            current_time = int(asyncio.get_event_loop().time())
            
            # Process and store data based on type
            if callback_name == "on_spo2_waveform_received":
                # Only store if we have less than 25 points in the current second
                if current_time > self.last_update_time:
                    self.data['spo2'] = []  # Clear previous second's data
                    self.last_update_time = current_time
                
                if len(self.data['spo2']) < self.max_waveform_points:
                    self.data['spo2'].append(package[4])
                callback(package[4])
                
            elif callback_name == "on_ecg_waveform_received":
                if current_time > self.last_update_time:
                    self.data['ecg'] = []
                if len(self.data['ecg']) < self.max_waveform_points:
                    self.data['ecg'].append(package[4])
                callback(package[4])
                
            elif callback_name == "on_resp_waveform_received":
                if current_time > self.last_update_time:
                    self.data['resp'] = []
                if len(self.data['resp']) < self.max_waveform_points:
                    self.data['resp'].append(package[4])
                callback(package[4])
                
            elif callback_name == "on_ecg_params_received":
                heart_rate = self._format_value(package[5])
                resp_rate = self._format_value(package[6])
                self.data['vitalSigns']['heartRate'] = heart_rate
                self.data['vitalSigns']['respRate'] = resp_rate
                callback(package[4], package[5], package[6])
                
            elif callback_name == "on_spo2_params_received":
                spo2 = package[5]
                pulse = package[6]
                
                # If SPO2 > 100, sensor is disconnected
                if spo2 > 100:
                    self.data['vitalSigns']['spo2Pulse'] = '- - /- -'
                else:
                    spo2_value = self._format_value(spo2)
                    pulse_value = self._format_value(pulse)
                    self.data['vitalSigns']['spo2Pulse'] = f"{spo2_value}/{pulse_value}"
                    
                callback(package[4], spo2, pulse)
                
            elif callback_name == "on_temp_params_received":
                temp = (package[5] * 10 + package[6]) / 10.0
                temp_str = self._format_value(temp)
                self.data['vitalSigns']['temperature'] = temp_str
                callback(package[4], temp)
                
            elif callback_name == "on_nibp_params_received":
                sys = package[6]  # 113
                dia = package[8]  # 76
                
                sys_value = self._format_value(sys)
                dia_value = self._format_value(dia)
                
                # Only update if we have valid values (non-zero)
                if sys != 0 or dia != 0:
                    self.data['vitalSigns']['nibp'] = f"{sys_value}/{dia_value}"
                
                callback(package[4], package[5] * 2, sys, package[7], dia)
            
            elif callback_name in ["on_ecg_peak_received", "on_spo2_peak_received"]:
                callback()

        except Exception as e:
            print(f"Error in callback {callback_name}: {e}")

    def get_current_data(self):
        """Return the current state of all data"""
        return self.data