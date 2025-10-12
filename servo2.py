import tkinter as tk
from pymodbus.client.sync import ModbusSerialClient as ModbusClient

# Modbus connection setup
PORT = 'COM9'
BAUDRATE = 57600
UNIT_ID = 1

client = ModbusClient(method='rtu', port=PORT, baudrate=BAUDRATE, stopbits=1, parity='N', timeout=1)

# Register map (based on manual)
REG_ENABLE_SERVO = 0x0302  # DI1 function = 1 (servo on)
REG_POSITION = 0x0B07      # Absolute position
REG_SPEED = 0x0B00         # Real-time speed
REG_TORQUE = 0x0B02        # Real-time torque
REG_TARGET_TORQUE = 0x0703
REG_TARGET_POSITION = 0x0520
REG_TRIGGER_HOMING = 0x0530  # H05_30 - origin return
REG_CONTROL_MODE = 0x0200    # H02_00

class ServoGUI:
    def __init__(self, master):
        self.master = master
        master.title("Servo Motor GUI - Full Control")

        # Status readings
        row = 0
        tk.Label(master, text="Current Position:").grid(row=row, column=0)
        self.position_label = tk.Label(master, text="---")
        self.position_label.grid(row=row, column=1)
        tk.Button(master, text="Read", command=self.read_position).grid(row=row, column=2)

        row += 1
        tk.Label(master, text="Velocity (rpm):").grid(row=row, column=0)
        self.velocity_label = tk.Label(master, text="---")
        self.velocity_label.grid(row=row, column=1)
        tk.Button(master, text="Read", command=self.read_velocity).grid(row=row, column=2)

        row += 1
        tk.Label(master, text="Torque (%):").grid(row=row, column=0)
        self.torque_label = tk.Label(master, text="---")
        self.torque_label.grid(row=row, column=1)
        tk.Button(master, text="Read", command=self.read_torque).grid(row=row, column=2)

        # Enable / Disable
        row += 1
        tk.Label(master, text="Servo Control:").grid(row=row, column=0)
        tk.Button(master, text="Enable", command=self.enable_servo).grid(row=row, column=1)
        tk.Button(master, text="Disable", command=self.disable_servo).grid(row=row, column=2)

        # Torque control
        row += 1
        tk.Label(master, text="Set Torque (±1000 = ±100%):").grid(row=row, column=0)
        self.torque_entry = tk.Entry(master)
        self.torque_entry.grid(row=row, column=1)
        tk.Button(master, text="Set", command=self.set_torque).grid(row=row, column=2)

        # Jog
        row += 1
        tk.Label(master, text="Jog Motor:").grid(row=row, column=0)
        tk.Button(master, text="Jog +", command=lambda: self.set_torque_value(500)).grid(row=row, column=1)
        tk.Button(master, text="Jog -", command=lambda: self.set_torque_value(-500)).grid(row=row, column=2)

        row += 1
        tk.Button(master, text="Stop (Torque=0)", command=lambda: self.set_torque_value(0)).grid(row=row, column=1)

        # Position move
        row += 1
        tk.Label(master, text="Move to Position (pulses):").grid(row=row, column=0)
        self.position_entry = tk.Entry(master)
        self.position_entry.grid(row=row, column=1)
        tk.Button(master, text="Go", command=self.move_to_position).grid(row=row, column=2)

        # Origin
        row += 1
        tk.Button(master, text="Origin Homing", command=self.trigger_homing).grid(row=row, column=1)

    def enable_servo(self):
        client.write_register(REG_ENABLE_SERVO, 1, unit=UNIT_ID)

    def disable_servo(self):
        client.write_register(REG_ENABLE_SERVO, 0, unit=UNIT_ID)

    def read_pid_gains(self):
        v_p = client.read_holding_registers(0x0800, 1, unit=UNIT_ID).registers[0]
        v_i = client.read_holding_registers(0x0801, 1, unit=UNIT_ID).registers[0]
        p_p = client.read_holding_registers(0x0802, 1, unit=UNIT_ID).registers[0]
        print(f"Velocity P = {v_p}, I = {v_i} | Position P = {p_p}")
    def read_position(self):
        result = client.read_holding_registers(REG_POSITION, 2, unit=UNIT_ID)
        if not result.isError():
            val = self.combine_32bit(result.registers)
            self.position_label.config(text=str(val))
        else:
            self.position_label.config(text="ERR")
        self.read_pid_gains()
        client.write_register(0x0800, 100, unit=1)  # H08_00 = 100
        client.write_register(0x0801, 50, unit=1)  # H08_01 = 50
        client.write_register(0x0802, 10, unit=1)  # H08_02 = 10
        self.read_pid_gains()
    def read_velocity(self):
        result = client.read_holding_registers(REG_SPEED, 1, unit=UNIT_ID)
        if not result.isError():
            val = self.to_signed(result.registers[0])
            self.velocity_label.config(text=f"{val} rpm")
        else:
            self.velocity_label.config(text="ERR")

    def read_torque(self):
        result = client.read_holding_registers(REG_TORQUE, 1, unit=UNIT_ID)
        if not result.isError():
            val = self.to_signed(result.registers[0]) / 10.0
            self.torque_label.config(text=f"{val:.1f}%")
        else:
            self.torque_label.config(text="ERR")

    def set_torque(self):
        try:
            val = int(self.torque_entry.get())
            client.write_register(REG_TARGET_TORQUE, val, unit=UNIT_ID)
        except Exception as e:
            print("Error:", e)

    def set_torque_value(self, value):
        client.write_register(REG_TARGET_TORQUE, value, unit=UNIT_ID)

    def move_to_position(self):
        try:
            value = int(self.position_entry.get())
            low = value & 0xFFFF
            high = (value >> 16) & 0xFFFF
            client.write_registers(REG_TARGET_POSITION, [low, high], unit=UNIT_ID)
        except Exception as e:
            print("Move error:", e)

    def trigger_homing(self):
        client.write_register(REG_TRIGGER_HOMING, 4, unit=UNIT_ID)

    def combine_32bit(self, regs):
        return (self.to_signed(regs[1]) << 16) | regs[0]

    def to_signed(self, val):
        return val - 0x10000 if val > 0x7FFF else val


if __name__ == "__main__":
    if not client.connect():
        print("❌ Connection to motor failed.")
    else:
        print("✅ Connected to motor.")
        root = tk.Tk()
        app = ServoGUI(root)
        root.mainloop()
        client.close()
