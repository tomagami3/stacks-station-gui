import tkinter as tk
from tkinter import ttk, messagebox
from pymodbus.client import ModbusSerialClient
import time, threading, math

# ---------------- Modbus connection (your settings) ----------------
PORT     = "COM11"
BAUD     = 57600
PARITY   = "N"      # 8-N-1
STOPBITS = 1
BYTESIZE = 8
UNIT_ID  = 1

client = ModbusSerialClient(
    port=PORT,
    baudrate=BAUD,
    parity=PARITY,
    stopbits=STOPBITS,
    bytesize=BYTESIZE,
    timeout=1
)

# ---------------- Register map (confirmed) ----------------
REG_SON        = 0x0303   # H03_03: S-ON (1=enable, 0=disable)
REG_MODE       = 0x0200   # H02_00: 1=Position, 2=Torque
REG_TORQUE_CMD = 0x0703   # H07_03: Int16, ±1000 -> ±100.0%
REG_POS_SET    = 0x110C   # H11_12: Int32 (low, high), relative displacement
REG_POS_ACT    = 0x0B07   # H0B_07: Int32 actual position counter (read-only)
REG_DI3_LOGIC  = 0x0307   # H03_07: 0=positive, 1=inverse
REG_SPEED      = 0x0B00   # H0B_00: Int16 actual speed (rpm or 0.1 rpm; here we treat as rpm)
REG_DISP_TYPE  = 0x1104   # H11_04: displacement instruction type (0=relative, 1=absolute)


PULSES_PER_REV_GEAR = 16000   # 1000 pulses per motor cycle * 16:1 gear
GEAR_STEPS          = 12      # we want the next 1/12 rotation boundary

def to_int16(x):
    return x - 0x10000 if (x & 0x8000) else x

def to_int32(low, high):
    val = (high << 16) | (low & 0xFFFF)
    if val & 0x80000000:
        val -= 0x100000000
    return val

def split_int32(value):
    if value < 0:
        value = (value + (1 << 32)) & 0xFFFFFFFF
    low =  value        & 0xFFFF
    high = (value >> 16) & 0xFFFF
    return [low, high]

class ServoGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Servo Control")
        self.grid_columnconfigure(1, weight=1)

        r = 0
        ttk.Label(self, text="Connection:").grid(row=r, column=0, sticky="w", padx=6, pady=4)
        self.conn_lbl = ttk.Label(self, text="(not connected)")
        self.conn_lbl.grid(row=r, column=1, sticky="w")

        # Enable/Disable
        r += 1
        ttk.Label(self, text="Servo:").grid(row=r, column=0, sticky="w", padx=6, pady=4)
        ttk.Button(self, text="Enable",  command=self.enable_servo).grid(row=r, column=1, sticky="w")
        ttk.Button(self, text="Disable", command=self.disable_servo).grid(row=r, column=2, sticky="w")

        # Mode select
        r += 1
        ttk.Label(self, text="Mode (H02_00):").grid(row=r, column=0, sticky="w", padx=6, pady=4)
        self.mode_var = tk.IntVar(value=1)  # default position
        ttk.Radiobutton(self, text="Position", value=1, variable=self.mode_var, command=self.apply_mode).grid(row=r, column=1, sticky="w")
        ttk.Radiobutton(self, text="Torque",   value=2, variable=self.mode_var, command=self.apply_mode).grid(row=r, column=2, sticky="w")

        # Torque command
        r += 1
        ttk.Label(self, text="Torque (±1000 = ±100%):").grid(row=r, column=0, sticky="w", padx=6, pady=4)
        self.torque_entry = ttk.Entry(self, width=10)
        self.torque_entry.insert(0, "40")
        self.torque_entry.grid(row=r, column=1, sticky="w")
        ttk.Button(self, text="Set H07_03", command=self.set_torque).grid(row=r, column=2, sticky="w")

        # Position set
        r += 1
        ttk.Label(self, text="Position set (pulses) H11_12:").grid(row=r, column=0, sticky="w", padx=6, pady=4)
        self.pos_entry = ttk.Entry(self, width=14)
        self.pos_entry.insert(0, "0")
        self.pos_entry.grid(row=r, column=1, sticky="w")
        ttk.Button(self, text="Move", command=self.move_position).grid(row=r, column=2, sticky="w")

        # Read actual position
        r += 1
        ttk.Label(self, text="Actual position (H0B_07):").grid(row=r, column=0, sticky="w", padx=6, pady=4)
        self.pos_lbl = ttk.Label(self, text="---")
        self.pos_lbl.grid(row=r, column=1, sticky="w")
        ttk.Button(self, text="Read", command=self.read_position).grid(row=r, column=2, sticky="w")

        # Read actual speed
        r += 1
        ttk.Label(self, text="Actual speed (H0B_00):").grid(row=r, column=0, sticky="w", padx=6, pady=4)
        self.speed_lbl = ttk.Label(self, text="---")
        self.speed_lbl.grid(row=r, column=1, sticky="w")
        ttk.Button(self, text="Read", command=self.read_speed).grid(row=r, column=2, sticky="w")

        # Auto close screw
        r += 1
        ttk.Button(self, text="Auto Close Screw", command=self.auto_close_button).grid(row=r, column=1, sticky="w", pady=6)

        # DI3 logic toggle
        r += 1
        ttk.Label(self, text="DI3 logic (H03_07):").grid(row=r, column=0, sticky="w", padx=6, pady=4)
        self.di3_var = tk.IntVar(value=0)  # 0=positive, 1=inverse
        ttk.Checkbutton(self, text="Inverse (1)", variable=self.di3_var, command=self.set_di3_logic).grid(row=r, column=1, sticky="w")

        # Status line
        r += 1
        ttk.Label(self, text="Status:").grid(row=r, column=0, sticky="w", padx=6, pady=4)
        self.status_lbl = ttk.Label(self, text="Idle")
        self.status_lbl.grid(row=r, column=1, sticky="w")

        # Connect at startup
        self.after(100, self._connect)

    # ----------------- helpers -----------------
    def log(self, msg):
        print(msg)
        self.status_lbl.config(text=msg)

    def read_speed_value(self):
        res = client.read_holding_registers(address=REG_SPEED, count=1)
        if res.isError():
            return None
        return to_int16(res.registers[0])  # treat as rpm

    def read_position_value(self):
        res = client.read_holding_registers(address=REG_POS_ACT, count=2)
        if res.isError():
            return None
        return to_int32(res.registers[0], res.registers[1])

    # -------------- actions --------------
    def _connect(self):
        if client.connect():
            self.conn_lbl.config(text=f"Connected {PORT} @ {BAUD}, unit {UNIT_ID}")
        else:
            self.conn_lbl.config(text=f"❌ failed to connect {PORT}")
            messagebox.showerror("Connection", "Failed to connect to the servo.")

    def enable_servo(self):
        client.write_register(address=REG_SON, value=1)

    def disable_servo(self):
        client.write_register(address=REG_SON, value=0)

    def apply_mode(self):
        mode = self.mode_var.get()  # 1=position, 2=torque
        client.write_register(address=REG_MODE, value=mode)

    def set_torque(self):
        try:
            val = int(self.torque_entry.get())
            if not -1000 <= val <= 1000:
                raise ValueError("Torque cmd must be in [-1000, 1000]")
            client.write_register(address=REG_TORQUE_CMD, value=val)
        except Exception as e:
            messagebox.showerror("Torque", str(e))

    def move_position(self):
        try:
            # Ensure relative mode for H11_12
            client.write_register(address=REG_DISP_TYPE, value=0)  # 0=relative
            tgt = int(self.pos_entry.get())
            lo, hi = split_int32(tgt)
            client.write_registers(address=REG_POS_SET, values=[lo, hi])
        except Exception as e:
            messagebox.showerror("Position", str(e))

    def read_position(self):
        val = self.read_position_value()
        self.pos_lbl.config(text=("ERR" if val is None else str(val)))

    def read_speed(self):
        v = self.read_speed_value()
        self.speed_lbl.config(text=("ERR" if v is None else f"{v} rpm"))

    def set_di3_logic(self):
        client.write_register(address=REG_DI3_LOGIC, value=(1 if self.di3_var.get() else 0))

    # -------------- Auto Close Screw sequence --------------
    def auto_close_button(self):
        try:
            torque_cmd = int(self.torque_entry.get())
        except:
            messagebox.showerror("Auto", "Invalid torque value")
            return
        threading.Thread(target=self.auto_close_screw, args=(torque_cmd,), daemon=True).start()

    def auto_close_screw(self, torque_cmd):
        try:
            # 1) disable
            self.log("1) Disable motor (S-ON=0)")
            client.write_register(address=REG_SON, value=0)

            # 2) torque set
            self.log(f"2) Set torque target H07_03 = {torque_cmd}")
            client.write_register(address=REG_TORQUE_CMD, value=torque_cmd)

            # 3) torque mode
            self.log("3) Set mode = Torque (2)")
            client.write_register(address=REG_MODE, value=2)

            # 4) enable
            self.log("4) Enable motor (S-ON=1)")
            client.write_register(address=REG_SON, value=1)

            # 5) status loop (speed)
            #    a) wait until |velocity| >= 100
            self.log("5a) Waiting for |speed| >= 100 rpm ...")
            t0 = time.time()
            while True:
                v = self.read_speed_value()
                self.log(f"   speed={v} rpm")
                if v is not None and abs(v) >= 100:
                    break
                if time.time() - t0 > 15:
                    raise TimeoutError("Timeout waiting for speed >= 100 rpm")
                time.sleep(0.05)

            #    b) wait until |velocity| returns to ~0
            self.log("5b) Waiting for speed to return to 0 rpm ...")
            t1 = time.time()
            while True:
                v = self.read_speed_value()
                self.log(f"   speed={v} rpm")
                if v is not None and abs(v) <= 2:
                    break
                if time.time() - t1 > 30:
                    self.log("   (warning) speed didn't settle exactly to 0; continuing")
                    break
                time.sleep(0.05)

            # 6) disable motor
            self.log("6) Disable motor (S-ON=0)")
            client.write_register(address=REG_SON, value=0)

            # 7) read current position
            pos = self.read_position_value()
            if pos is None:
                raise RuntimeError("Failed reading position")
            self.log(f"7) Current position H0B_07 = {pos} pulses")

            # 8) calculate spins & next 1/12 gear boundary
            spins = pos / float(PULSES_PER_REV_GEAR)
            # next boundary index (strictly ahead):
            m = (pos * GEAR_STEPS) // PULSES_PER_REV_GEAR + 1
            exact_boundary = m * PULSES_PER_REV_GEAR / GEAR_STEPS
            next_boundary_int = math.ceil(exact_boundary)  # integer pulses >= boundary
            diff = int(next_boundary_int - pos)
            self.log(f"8) Spins={spins:.4f}, nextBoundary={next_boundary_int}, diff={diff} pulses")

            # 9) write H11-12 = diff (relative)
            self.log("9) Write H11_12 (relative displacement) = diff")
            client.write_register(address=REG_DISP_TYPE, value=0)  # 0=relative
            lo, hi = split_int32(diff)
            client.write_registers(address=REG_POS_SET, values=[lo, hi])

            # 10) set mode = position
            self.log("10) Set mode = Position (1)")
            client.write_register(address=REG_MODE, value=1)
            client.write_register(address=REG_SON, value=1)

            # 11) toggle DI3 logic inverse ON then OFF
            self.log("11) DI3 logic inverse ON (H03_07=1) then OFF (0)")
            client.write_register(address=REG_DI3_LOGIC, value=1)
            time.sleep(0.1)
            client.write_register(address=REG_DI3_LOGIC, value=0)

            # 12) wait another second
            self.log("12) Wait 1s ...")
            time.sleep(1.0)

            self.log("✅ Auto Close Screw: DONE")

        except Exception as e:
            self.log(f"❌ Auto Close Screw error: {e}")

if __name__ == "__main__":
    app = ServoGUI()
    app.mainloop()
    try:
        client.close()
    except Exception:
        pass
