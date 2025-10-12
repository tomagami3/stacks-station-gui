import serial
import time

def send_command(ser, command):
    data = bytearray(command)
    print(f"Sending: {data.hex(' ')}")
    ser.write(data)
    time.sleep(0.1)
    if ser.in_waiting:
        response = ser.read(ser.in_waiting)
        print(f"Received: {response.hex(' ')}")
        return response
    else:
        print("No response")
        return None

if __name__ == "__main__":
    try:
        ser = serial.Serial(
            port='COM4',
            baudrate=9600,
            bytesize=8,
            parity='N',
            stopbits=1,
            timeout=0.3
        )
        print("COM7 opened successfully.")
    except serial.SerialException as e:
        print(f"Failed to open COM7: {e}")
        exit(1)

    try:
        while True:
            # Try reading status register (function 0x04)
            cmd = [0x01, 0x00, 0x04, 0x00, 0x00, 0x00, 0x00, 0x05]
            send_command(ser, cmd)
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        ser.close()
