import serial
import time
import tkinter as tk
from tkinter import messagebox

# ---------------- Serial Setup ----------------
def connect_serial():
    try:
        return serial.Serial(
            port='COM4',  # Change if needed
            baudrate=9600,
            bytesize=8,
            parity='N',
            stopbits=1,
            timeout=0.3
        )
    except serial.SerialException as e:
        messagebox.showerror("Connection Error", str(e))
        return None

# ---------------- Communication ----------------
def checksum(cmd):
    return sum(cmd[:7]) & 0xFF

def send_command(cmd):
    ser = connect_serial()
    if ser is None:
        return
    try:
        full_cmd = bytearray(cmd)
        ser.write(full_cmd)
        time.sleep(0.1)
        if ser.in_waiting:
            response = ser.read(ser.in_waiting)
            print(f"Response: {response.hex(' ')}")
        else:
            print("No response")
    finally:
        ser.close()

# ---------------- Command Builders ----------------
def jog(direction):
    """
    direction: +1 (forward), -1 (backward)
    """
    mode = 0x01 if direction > 0 else 0x02
    cmd = [0x01, 0x01, 0x08, 0x00, 0x00, 0x00, mode]
    cmd.append(checksum(cmd + [0]))
    send_command(cmd)

def stop():
    cmd = [0x01, 0x01, 0x08, 0x00, 0x00, 0x00, 0x03]
    cmd.append(checksum(cmd + [0]))
    send_command(cmd)

def goto_position(pos):
    try:
        pos = int(pos)
    except ValueError:
        messagebox.showwarning("Invalid Input", "Enter a valid integer position.")
        return

    # ser = connect_serial()
    # if ser is None:
    #     return

    try:
        # Step 1: Clear coordinates
        cmd_clear = [0x01, 0x01, 0x07, 0x00, 0x00, 0x00, 0x00]
        cmd_clear.append(checksum(cmd_clear + [0]))
        send_command(cmd_clear)
        time.sleep(0.1)

        # Step 2: Set real position to 0
        cmd_real = [0x01, 0x01, 0x11, 0x00, 0x00, 0x00, 0x00]
        cmd_real.append(checksum(cmd_real + [0]))
        send_command(cmd_real)
        time.sleep(0.1)

        # Step 3: Set to position mode (mode = 0)
        cmd_mode = [0x01, 0x01, 0x08, 0x00, 0x00, 0x00, 0x00]
        cmd_mode.append(checksum(cmd_mode + [0]))
        send_command(cmd_mode)
        time.sleep(0.1)

        # Step 4: Send target position
        pos_bytes = pos.to_bytes(4, byteorder='little', signed=False)
        cmd_pos = [0x01, 0x01, 0x12] + list(pos_bytes)
        cmd_pos.append(checksum(cmd_pos + [0]))
        send_command(cmd_pos)

    finally:
        print('done')
        # ser.close()


def set_current(run_val=24, hold_val=8):
    # run_val and hold_val should be between 1 and 31
    if not (0 < run_val <= 31 and 0 < hold_val <= 31):
        messagebox.showerror("Invalid Current", "Run/Hold must be between 1 and 31.")
        return
    cmd = [0x01, 0x01, 0x01, 0x00, 0x1F, run_val, hold_val]
    cmd.append(checksum(cmd + [0]))
    send_command(cmd)

# ---------------- GUI ----------------
def run_gui():
    root = tk.Tk()
    root.title("ND556 Stepper Control")

    frame = tk.Frame(root, padx=10, pady=10)
    frame.pack()

    # Jog buttons
    tk.Button(frame, text="◀ Jog Backward", command=lambda: jog(-1), width=20).grid(row=0, column=0, pady=5)
    tk.Button(frame, text="▶ Jog Forward", command=lambda: jog(1), width=20).grid(row=0, column=1, pady=5)

    # Stop
    tk.Button(frame, text="⏹ Stop", command=stop, width=20, bg='red', fg='white').grid(row=1, column=0, columnspan=2, pady=10)

    # Position
    tk.Label(frame, text="Target Position:").grid(row=2, column=0)
    entry_pos = tk.Entry(frame)
    entry_pos.grid(row=2, column=1)
    tk.Button(frame, text="Go To Position", command=lambda: goto_position(entry_pos.get()), width=20).grid(row=3, column=0, columnspan=2, pady=5)

    # Set current
    tk.Button(frame, text="Set Current (4.2A / 1.4A)", command=lambda: set_current(6, 8), width=25).grid(row=4, column=0, columnspan=2, pady=10)

    root.mainloop()

# ---------------- Entry Point ----------------
if __name__ == "__main__":
    run_gui()
