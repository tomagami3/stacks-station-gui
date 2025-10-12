import serial
import time

def get_checksum(data):
    """Calculate 8-bit checksum."""
    return sum(data) & 0xFF

def read_position(ser, slave_addr=0x01):
    """Send read command and receive 6-byte encoder value (position)."""
    request = [0xFA, slave_addr, 0x31]
    request.append(get_checksum(request))
    ser.write(bytearray(request))

    time.sleep(0.1)
    response = ser.read(10)

    if len(response) != 10:
        print("Invalid response length:", len(response))
        return None

    if response[0] != 0xFB or response[1] != slave_addr or response[2] != 0x31:
        print("Invalid header or slave response")
        return None

    if response[-1] != get_checksum(response[:-1]):
        print("Checksum error")
        return None

    # Extract lower 4 bytes from int48_t
    pos_bytes = response[5:9]
    position = int.from_bytes(pos_bytes, byteorder='big', signed=True)
    return position

def main():
    try:
        ser = serial.Serial("COM9", baudrate=9600, timeout=1)
    except serial.SerialException as e:
        print(f"Serial connection error: {e}")
        return

    print("Reading position from motor. Press Ctrl+C to stop.")
    try:
        while True:
            position = read_position(ser)
            if position is not None:
                print(f"Position: {position}")
            else:
                print("Failed to read position.")
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        ser.close()

if __name__ == "__main__":
    main()
