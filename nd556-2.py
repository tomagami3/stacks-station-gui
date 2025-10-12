import serial
import tkinter as tk
import time

def checksum(cmd):
    return sum(cmd[:7]) & 0xFF

def send_command(cmd):
    try:
        ser = serial.Serial('COM4', baudrate=9600, timeout=0.3)
        ser.write(bytearray(cmd))
        time.sleep(0.1)
        if ser.in_waiting:
            response = ser.read(ser.in_waiting)
            print("Received:", response.hex(" "))
            return list(response)
        else:
            print("No response")
            return []
    except Exception as e:
        print(f"Error: {e}")
        return []


def read_position(addr_high, addr_low):
    cmd = [0x01, 0x00, 0x04, addr_high, addr_low, 0x00, 0x04]
    cmd.append(checksum(cmd + [0]))
    res = send_command(cmd)

    if len(res) >= 8:
        # Expect: [01, 00, 04, <addrH>, <addrL>, D0, D1, D2, D3]
        data_bytes = bytes(res[5:9])  # Extract the 4 data bytes
        value = int.from_bytes(data_bytes, byteorder='little', signed=True)
        return value
    else:
        print("Response too short:", res)
        return None


def read_actual():
    val = read_position(0x00, 0x30)
    label.config(text=f"Actual Pos: {val}" if val is not None else "No response")

def read_target():
    val = read_position(0x00, 0x34)
    label.config(text=f"Target Pos: {val}" if val is not None else "No response")

# GUI
root = tk.Tk()
root.title("ND556 Position Reader")

tk.Button(root, text="Read Actual Position", command=read_actual).pack(pady=5)
tk.Button(root, text="Read Target Position", command=read_target).pack(pady=5)
label = tk.Label(root, text="Ready", font=("Arial", 14))
label.pack(pady=10)

root.mainloop()
