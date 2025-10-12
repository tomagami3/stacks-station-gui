import serial
import time

def calculate_checksum(data):
    return sum(data[:7]) & 0xFF

def build_read_command(address, function_code):
    # Byte3–Byte6 = 0x00 for read operations
    command = [address, 0x00, function_code, 0x00, 0x00, 0x00, 0x00]
    checksum = calculate_checksum(command + [0])
    command.append(checksum)
    return command

def send_command(ser, command):
    ser.write(bytearray(command))
    print(f"Sent: {[hex(b) for b in command]}")
    time.sleep(0.1)
    if ser.in_waiting:
        response = ser.read(ser.in_waiting)
        print(f"Received: {[hex(b) for b in response]}")
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

    address = 0x01  # or 0x00 if still in default mode
    function_code = 0x04  # status register

    try:
        while True:
            cmd = build_read_command(address, function_code)
            send_command(ser, cmd)
            time.sleep(0.5)  # repeat every 0.5s
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        ser.close()
