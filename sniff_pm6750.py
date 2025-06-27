import serial, time, argparse

HEADER = b"\x55\xAA"
def checksum(n, data):
    return (~(n + sum(data)) & 0xFF) & 0xFF
def cmd(a1, a2):
    payload = bytes([a1, a2])
    n = len(payload) + 2
    return HEADER + bytes([n]) + payload + bytes([checksum(n, payload)])

ap = argparse.ArgumentParser()
ap.add_argument("--port", required=True)
args = ap.parse_args()

ser = serial.Serial(args.port, 115200, timeout=0.2)
print("Enviando: Firmware inquiry (0xFC)")
ser.write(cmd(0xFC, 0))
print("Enviando: Enable SpO2 waveform (0xFE 0x01)")
ser.write(cmd(0xFE, 1))

print("Escuchando 10 s… (imprime todo en hex)")
t_end = time.time() + 10
buf = bytearray()
while time.time() < t_end:
    data = ser.read(128)
    if data:
        buf.extend(data)
        while len(buf) >= 3:
            if buf[0:2] != HEADER:
                buf.pop(0)
                continue
            n = buf[2]
            if len(buf) < n + 3:
                break
            frame = bytes(buf[: n + 3])
            buf = buf[n + 3 :]
            print(frame.hex())
ser.close()
