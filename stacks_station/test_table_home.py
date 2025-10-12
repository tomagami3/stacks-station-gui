import time
import threading
import tkinter as tk
from tkinter import ttk, messagebox

# =========================
# Robust imports (pymodbus)
# =========================
PM3 = False
try:
    from pymodbus.client import ModbusTcpClient, ModbusSerialClient  # pymodbus >= 3.x
    PM3 = True
except Exception:
    # pymodbus 2.x fallback
    from pymodbus.client.sync import ModbusTcpClient, ModbusSerialClient  # type: ignore

# =========================
# Try to reuse your project
# =========================
# If your Stacks station GUI created wrappers for IO/motor, we’ll reuse them.
# Otherwise we fall back to generic adapters below.
HAS_MT3A = False
HAS_MOTOR_LIB = False

MT3A_CLASS = None
MOTOR_CLASS = None

# Try common names you’ve used in previous Stacks station code
for modname in ("mt3a_io", "io1632", "mt3a", "mt3aio"):
    try:
        mod = __import__(modname)
        # Accept a class or a factory function; adjust if your project names differ
        for cand in ("MT3AIO", "MT3A", "IO1632"):
            if hasattr(mod, cand):
                MT3A_CLASS = getattr(mod, cand)
                HAS_MT3A = True
                break
        if HAS_MT3A:
            break
    except Exception:
        pass

for modname in ("actuator_modbus", "servo42d", "motor_client", "table_motor"):
    try:
        mod = __import__(modname)
        # Accept a class that exposes enable, set_mode_velocity, set_mode_position,
        # set_target_speed, set_target_pos_rel, run, stop, read_position, connect, close
        for cand in ("Servo42DModbus", "MotorClient", "TableMotor", "ActuatorClient"):
            if hasattr(mod, cand):
                MOTOR_CLASS = getattr(mod, cand)
                HAS_MOTOR_LIB = True
                break
        if HAS_MOTOR_LIB:
            break
    except Exception:
        pass

# =========================
# CONFIG (adjust if needed)
# =========================
IO_HOST = "192.168.1.12"
IO_PORT = 502
INPUT10_INDEX_1BASED = 10         # as named in your app
INPUT10_BIT = INPUT10_INDEX_1BASED - 1  # zero-based

SERIAL_PORT = "COM10"
BAUDRATE = 38400
PARITY = "N"
STOPBITS = 1
BYTESIZE = 8
MOTOR_SLAVE_ID = 3                # table motor address

# If your own motor lib is not found, these generic registers will be used.
# !! Replace with your actual map if you use the fallback !!
REG_ENABLE_COIL        = 0x0000
REG_CLEAR_ALARM_COIL   = 0x0003
REG_MODE_HOLD          = 0x0100  # 0=pos, 1=vel
REG_TARGET_SPEED_HOLD  = 0x0101  # signed
REG_TARGET_POS_HOLD    = 0x0102  # signed relative
REG_COMMAND_COIL_RUN   = 0x0001  # run/start
REG_COMMAND_COIL_STOP  = 0x0002  # stop
REG_ACTUAL_POS_HOLD    = 0x0200  # readback (16-bit here; adapt to 32-bit if needed)

FAST_JOG_SPEED = 20
SLOW_JOG_SPEED = 10
REL_STEP_DEFAULT = 1000
CENTER_OFFSET_DEFAULT = 2000

IO_POLL_HZ = 20
MOTOR_POLL_HZ = 10
DEBOUNCE_SEC = 0.02

# =========================
# Helpers
# =========================
def _safe(call, default=None):
    try:
        res = call()
        if res is None:
            return default
        if hasattr(res, "isError") and res.isError():
            return default
        return res
    except Exception:
        return default

def _to_s16(v):
    v &= 0xFFFF
    return v - 0x10000 if (v & 0x8000) else v

# =========================
# IO Adapters
# =========================
class IoGeneric:
    """Generic Modbus TCP reader for Input10. Adjust to your IO map if you use this fallback."""
    def __init__(self, host, port):
        self.client = ModbusTcpClient(host=host, port=port, timeout=1.0)
        self.connected = False
        self.lock = threading.Lock()

    def connect(self):
        self.connected = self.client.connect()
        return self.connected

    def close(self):
        with self.lock:
            try:
                self.client.close()
            except Exception:
                pass
            self.connected = False

    def get_input10(self):
        if not self.connected:
            return None
        # Example: read input register 0 (16 DI packed)
        r = _safe(lambda: self.client.read_input_registers(address=0, count=1))
        if r is None:
            return None
        word = r.registers[0] & 0xFFFF
        return bool((word >> INPUT10_BIT) & 1)

def build_io():
    if HAS_MT3A and MT3A_CLASS is not None:
        try:
            # Expect your class to support .connect(), .close(), and a method to read DI – adapt here:
            io = MT3A_CLASS(host=IO_HOST, port=IO_PORT)  # if your ctor differs, tweak here
            # Wrap a small proxy to normalize the API for Input10
            class _Proxy:
                def __init__(self, impl):
                    self.impl = impl
                    self.connected = False

                def connect(self):
                    ok = self.impl.connect()
                    self.connected = bool(ok)
                    return self.connected

                def close(self):
                    try:
                        self.impl.close()
                    except Exception:
                        pass
                    self.connected = False

                def get_input10(self):
                    # If your class has read_inputs() -> list/bits or get_di(idx)
                    if hasattr(self.impl, "get_di"):
                        return bool(self.impl.get_di(INPUT10_INDEX_1BASED))
                    elif hasattr(self.impl, "read_inputs"):
                        bits = self.impl.read_inputs()  # expect list[bool] 1-based or 0-based
                        if not bits:
                            return None
                        # prefer 1-based
                        if len(bits) >= INPUT10_INDEX_1BASED and isinstance(bits[INPUT10_BIT], (bool, int)):
                            return bool(bits[INPUT10_BIT])
                        # else assume packed int:
                        val = int(bits[0])
                        return bool((val >> INPUT10_BIT) & 1)
                    else:
                        # fall back to a discrete read if it exists
                        if hasattr(self.impl, "read_discrete_inputs"):
                            rr = self.impl.read_discrete_inputs(0, 16)
                            if rr is None or not hasattr(rr, "bits"):
                                return None
                            return bool(rr.bits[INPUT10_BIT])
                        return None

            return _Proxy(io)
        except Exception:
            pass
    # fallback
    return IoGeneric(IO_HOST, IO_PORT)

# =========================
# Motor Adapters
# =========================
class MotorGeneric:
    """Generic Modbus RTU driver containing the ops we need."""
    def __init__(self, port, baudrate, parity, stopbits, bytesize, slave):
        # pymodbus 3.x and 2.x compatible initialization
        try:
            self.client = ModbusSerialClient(
                port=port,
                baudrate=baudrate,
                parity=parity,
                stopbits=stopbits,
                bytesize=bytesize,
                timeout=0.3
            )
        except TypeError:
            # pymodbus 2.x requires method='rtu'
            from pymodbus.client.sync import ModbusSerialClient as _MSC  # type: ignore
            self.client = _MSC(
                method="rtu",
                port=port,
                baudrate=baudrate,
                parity=parity,
                stopbits=stopbits,
                bytesize=bytesize,
                timeout=0.3
            )
        self.slave = slave
        self.connected = False
        self.lock = threading.Lock()

    def connect(self):
        self.connected = self.client.connect()
        return self.connected

    def close(self):
        try:
            self.client.close()
        except Exception:
            pass
        self.connected = False

    # --- Commands ---
    def enable(self, on=True):
        return self._wcoil(REG_ENABLE_COIL, bool(on))

    def clear_alarm(self):
        return self._wcoil(REG_CLEAR_ALARM_COIL, True)

    def set_mode_velocity(self):
        return self._wreg(REG_MODE_HOLD, 1)

    def set_mode_position(self):
        return self._wreg(REG_MODE_HOLD, 0)

    def set_target_speed(self, speed):
        return self._wreg(REG_TARGET_SPEED_HOLD, speed & 0xFFFF)

    def set_target_pos_rel(self, steps):
        return self._wreg(REG_TARGET_POS_HOLD, steps & 0xFFFF)

    def run(self, on=True):
        return self._wcoil(REG_COMMAND_COIL_RUN, bool(on))

    def stop(self):
        return self._wcoil(REG_COMMAND_COIL_STOP, True)

    def read_position(self):
        rr = _safe(lambda: self.client.read_holding_registers(address=REG_ACTUAL_POS_HOLD, count=1,
                                                              slave=MOTOR_SLAVE_ID) if PM3 else
                   lambda: None)
        if rr is None and not PM3:
            # pymodbus 2.x uses unit=
            rr = _safe(lambda: self.client.read_holding_registers(address=REG_ACTUAL_POS_HOLD, count=1,
                                                                  unit=MOTOR_SLAVE_ID))
        if rr is None:
            return None
        return _to_s16(rr.registers[0])

    # --- low-level ---
    def _wreg(self, addr, val):
        if PM3:
            rr = _safe(lambda: self.client.write_register(addr, val, slave=MOTOR_SLAVE_ID))
        else:
            rr = _safe(lambda: self.client.write_register(addr, val, unit=MOTOR_SLAVE_ID))
        return rr is not None

    def _wcoil(self, addr, val):
        if PM3:
            rr = _safe(lambda: self.client.write_coil(addr, bool(val), slave=MOTOR_SLAVE_ID))
        else:
            rr = _safe(lambda: self.client.write_coil(addr, bool(val), unit=MOTOR_SLAVE_ID))
        return rr is not None

def build_motor():
    if HAS_MOTOR_LIB and MOTOR_CLASS is not None:
        try:
            m = MOTOR_CLASS(port=SERIAL_PORT, baudrate=BAUDRATE, parity=PARITY,
                            stopbits=STOPBITS, bytesize=BYTESIZE, slave=MOTOR_SLAVE_ID)
            # sanity: check it has required methods
            for fn in ("connect","close","enable","set_mode_velocity","set_mode_position",
                       "set_target_speed","set_target_pos_rel","run","stop","read_position"):
                if not hasattr(m, fn):
                    raise AttributeError(f"Motor lib missing method {fn}")
            return m
        except Exception:
            pass
    return MotorGeneric(SERIAL_PORT, BAUDRATE, PARITY, STOPBITS, BYTESIZE, MOTOR_SLAVE_ID)

# =========================
# GUI
# =========================
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Stacks – Table Home (uses Stacks station libs if present)")
        self.geometry("560x380")

        self.io = build_io()
        self.motor = build_motor()
        self.running = True
        self.homing = False

        self.io_status = tk.StringVar(value="IO: Disconnected")
        self.input10_str = tk.StringVar(value="Input10: ?")
        self.motor_status = tk.StringVar(value="Motor: Disconnected")
        self.pos_str = tk.StringVar(value="Pos: ?")

        self.rel_steps = tk.StringVar(value=str(REL_STEP_DEFAULT))
        self.center_offset = tk.StringVar(value=str(CENTER_OFFSET_DEFAULT))

        self._build_ui()

        self.t_io = threading.Thread(target=self._io_poll, daemon=True)
        self.t_mo = threading.Thread(target=self._motor_poll, daemon=True)
        self.t_io.start()
        self.t_mo.start()

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self):
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        # IO
        f_io = ttk.Labelframe(root, text="IO (MT3A-IO1632)", padding=10)
        f_io.pack(fill="x", pady=6)
        ttk.Label(f_io, textvariable=self.io_status).grid(row=0, column=0, sticky="w", padx=6)
        ttk.Label(f_io, textvariable=self.input10_str, font=("Segoe UI", 11, "bold")).grid(row=1, column=0, sticky="w", padx=6)
        ttk.Button(f_io, text="Connect IO", command=self.on_io_connect).grid(row=0, column=1, padx=6)
        ttk.Button(f_io, text="Disconnect", command=self.on_io_disconnect).grid(row=0, column=2, padx=6)

        # Motor
        f_m = ttk.Labelframe(root, text=f"Table Motor (addr {MOTOR_SLAVE_ID})", padding=10)
        f_m.pack(fill="x", pady=6)
        ttk.Label(f_m, textvariable=self.motor_status).grid(row=0, column=0, sticky="w", padx=6)
        ttk.Label(f_m, textvariable=self.pos_str, font=("Segoe UI", 11, "bold")).grid(row=1, column=0, sticky="w", padx=6)
        ttk.Button(f_m, text="Connect Motor", command=self.on_motor_connect).grid(row=0, column=1, padx=6)
        ttk.Button(f_m, text="Disconnect", command=self.on_motor_disconnect).grid(row=0, column=2, padx=6)
        ttk.Button(f_m, text="Enable", command=lambda: self._ok(self.motor.enable, True)).grid(row=0, column=3, padx=6)
        ttk.Button(f_m, text="Disable", command=lambda: self._ok(self.motor.enable, False)).grid(row=0, column=4, padx=6)

        # Relative moves
        f_rel = ttk.Labelframe(root, text="Relative Move", padding=10)
        f_rel.pack(fill="x", pady=6)
        ttk.Label(f_rel, text="Steps:").grid(row=0, column=0, sticky="e")
        ttk.Entry(f_rel, textvariable=self.rel_steps, width=10).grid(row=0, column=1, padx=6)
        ttk.Button(f_rel, text="Move +", command=lambda: self._rel(+1)).grid(row=0, column=2, padx=6)
        ttk.Button(f_rel, text="Move -", command=lambda: self._rel(-1)).grid(row=0, column=3, padx=6)

        # Home
        f_home = ttk.Labelframe(root, text="Home Process", padding=10)
        f_home.pack(fill="x", pady=6)
        ttk.Label(f_home, text="Center offset (pulses):").grid(row=0, column=0, sticky="e")
        ttk.Entry(f_home, textvariable=self.center_offset, width=10).grid(row=0, column=1, padx=6)
        ttk.Button(f_home, text="HOME", command=self.on_home).grid(row=0, column=2, padx=10)
        ttk.Button(f_home, text="STOP", command=self.on_stop).grid(row=0, column=3, padx=6)

        # Manual jog (optional)
        f_jog = ttk.Labelframe(root, text="Jog (manual)", padding=10)
        f_jog.pack(fill="x", pady=6)
        ttk.Button(f_jog, text="Jog + (fast)", command=lambda: self._jog(+FAST_JOG_SPEED)).grid(row=0, column=0, padx=6)
        ttk.Button(f_jog, text="Jog - (fast)", command=lambda: self._jog(-FAST_JOG_SPEED)).grid(row=0, column=1, padx=6)
        ttk.Button(f_jog, text="Stop", command=self.on_stop).grid(row=0, column=2, padx=6)

    # ========= IO =========
    def on_io_connect(self):
        ok = self.io.connect()
        self.io_status.set("IO: Connected" if ok else "IO: Failed")

    def on_io_disconnect(self):
        self.io.close()
        self.io_status.set("IO: Disconnected")

    def _io_poll(self):
        dt = 1.0 / IO_POLL_HZ
        while self.running:
            try:
                if getattr(self.io, "connected", False):
                    v = self.io.get_input10()
                    if v is None:
                        self.input10_str.set("Input10: ?")
                        self.io_status.set("IO: Connected (read error)")
                    else:
                        self.input10_str.set(f"Input10: {'ON' if v else 'OFF'}")
                        self.io_status.set("IO: Connected")
            except Exception:
                self.input10_str.set("Input10: ?")
            time.sleep(dt)

    # ======== Motor ========
    def on_motor_connect(self):
        ok = self.motor.connect()
        self.motor_status.set("Motor: Connected" if ok else "Motor: Failed")

    def on_motor_disconnect(self):
        self.motor.close()
        self.motor_status.set("Motor: Disconnected")

    def _motor_poll(self):
        dt = 1.0 / MOTOR_POLL_HZ
        while self.running:
            try:
                if getattr(self.motor, "connected", False):
                    p = self.motor.read_position()
                    if p is None:
                        self.pos_str.set("Pos: ?")
                        self.motor_status.set("Motor: Connected (read error)")
                    else:
                        self.pos_str.set(f"Pos: {p}")
                        self.motor_status.set("Motor: Connected")
            except Exception:
                self.pos_str.set("Pos: ?")
            time.sleep(dt)

    # ======== Actions ========
    def _ok(self, fn, *a, **k):
        ok = False
        try:
            ok = bool(fn(*a, **k))
        except Exception:
            ok = False
        if not ok:
            messagebox.showerror("Error", f"{getattr(fn, '__name__', 'Action')} failed")

    def _rel(self, sign):
        if not getattr(self.motor, "connected", False):
            messagebox.showerror("Error", "Motor not connected")
            return
        try:
            steps = int(self.rel_steps.get()) * sign
        except ValueError:
            messagebox.showerror("Error", "Invalid steps")
            return
        if not self.motor.set_mode_position():
            self._ui_error("Set position mode failed")
            return
        if not self.motor.set_target_pos_rel(steps):
            self._ui_error("Set target pos failed")
            return
        # Some drivers require RUN; others move automatically
        self.motor.run(True)
        time.sleep(0.05)
        self.motor.run(False)

    def _jog(self, speed):
        if not getattr(self.motor, "connected", False):
            messagebox.showerror("Error", "Motor not connected")
            return
        if not self.motor.set_mode_velocity():
            self._ui_error("Velocity mode failed")
            return
        if not self.motor.set_target_speed(speed):
            self._ui_error("Set speed failed")
            return
        if not self.motor.enable(True):
            self._ui_error("Enable failed")
            return
        if not self.motor.run(True):
            self._ui_error("RUN failed")

    def on_stop(self):
        try:
            self.motor.stop()
        except Exception:
            pass

    def on_home(self):
        if self.homing:
            return
        self.homing = True
        threading.Thread(target=self._home_thread, daemon=True).start()

    def _home_thread(self):
        try:
            # Ensure connections
            if not getattr(self.io, "connected", False):
                if not self.io.connect():
                    self._ui_error("IO connect failed")
                    return
            if not getattr(self.motor, "connected", False):
                if not self.motor.connect():
                    self._ui_error("Motor connect failed")
                    return

            # Enable + velocity mode
            if not self.motor.enable(True):
                self._ui_error("Enable failed")
                return
            if not self.motor.set_mode_velocity():
                self._ui_error("Velocity mode failed")
                return

            # 1) Jog forward fast until Input10 == OFF
            if not self.motor.set_target_speed(+FAST_JOG_SPEED):
                self._ui_error("Set fast speed failed")
                return
            if not self.motor.run(True):
                self._ui_error("RUN failed")
                return
            if not self._wait_input10(False):
                self.motor.stop()
                self._ui_error("Timeout waiting Input10 OFF")
                return

            # Stop, settle
            self.motor.stop()
            time.sleep(0.05)

            # 2) Jog reverse slow until Input10 == ON
            if not self.motor.set_target_speed(-SLOW_JOG_SPEED):
                self._ui_error("Set slow reverse speed failed")
                return
            if not self.motor.run(True):
                self._ui_error("RUN failed")
                return
            if not self._wait_input10(True):
                self.motor.stop()
                self._ui_error("Timeout waiting Input10 ON")
                return

            # Edge reached
            self.motor.stop()
            time.sleep(0.05)

            # 3) Read position at edge = "home detected"
            home_pos = self.motor.read_position()
            if home_pos is None:
                self._ui_error("Read pos at home failed")
                return
            print(f"[INFO] Home detected at pos {home_pos}")

            # 4) Move relative center offset
            try:
                center = int(self.center_offset.get())
            except ValueError:
                center = CENTER_OFFSET_DEFAULT
                self.center_offset.set(str(center))
            if not self.motor.set_mode_position():
                self._ui_error("Position mode failed")
                return
            if not self.motor.set_target_pos_rel(center):
                self._ui_error("Set relative center failed")
                return
            self.motor.run(True)
            time.sleep(0.05)
            self.motor.run(False)

            messagebox.showinfo("Home", "Home sequence complete.")
        finally:
            self.homing = False

    def _wait_input10(self, expected: bool, timeout=None):
        t0 = time.time()
        stable_since = None
        while self.running:
            v = None
            try:
                v = self.io.get_input10()
            except Exception:
                v = None
            if v is not None:
                if v == expected:
                    if stable_since is None:
                        stable_since = time.time()
                    elif (time.time() - stable_since) >= DEBOUNCE_SEC:
                        return True
                else:
                    stable_since = None
            if timeout and (time.time() - t0) > timeout:
                return False
            time.sleep(0.01)

    def _ui_error(self, msg):
        print("[ERROR]", msg)
        self.after(0, lambda: messagebox.showerror("Error", msg))

    def on_close(self):
        self.running = False
        try:
            self.motor.stop()
        except Exception:
            pass
        try:
            self.motor.close()
        except Exception:
            pass
        try:
            self.io.close()
        except Exception:
            pass
        self.destroy()

if __name__ == "__main__":
    App().mainloop()
