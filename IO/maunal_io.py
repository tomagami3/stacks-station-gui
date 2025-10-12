# mt3a_manual_ui.py
# Manual operations panel for MT3A-IO1632
# - Live DI monitor (DI05: hollow_stepper up, DI06: hollow_stepper down)
# - DO controls per your mapping
# - Password protection for DO12 (Setup cylinder)

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import time
from pymodbus.client import ModbusTcpClient

HOST_DEFAULT = "192.168.1.12"
UNIT_DEFAULT = 1
DI_COUNT = 16
DO_COUNT = 16
POLL_MS = 250  # refresh period (ms)

# --- Modbus helper that works across pymodbus 1.x / 2.x / 3.x ---
class MT3AClient:
    def __init__(self, host=HOST_DEFAULT, port=502, unit=UNIT_DEFAULT, timeout=2.0):
        self.host, self.port, self.unit, self.timeout = host, port, unit, timeout
        self.client = None

    def connect(self):
        self.client = ModbusTcpClient(host=self.host, port=self.port, timeout=self.timeout)
        if not self.client.connect():
            raise RuntimeError(f"Could not connect to {self.host}:{self.port}")

    def close(self):
        if self.client:
            try: self.client.close()
            except: pass
            self.client = None

    def _call(self, method_name, **kwargs):
        fn = getattr(self.client, method_name)
        # Try slave=..., then unit=..., then no kw (set unit_id)
        try:
            return fn(**kwargs, slave=self.unit)
        except TypeError:
            pass
        try:
            return fn(**kwargs, unit=self.unit)
        except TypeError:
            pass
        try:
            setattr(self.client, "unit_id", self.unit)
        except Exception:
            pass
        return fn(**kwargs)

    def _ok(self, resp):
        if hasattr(resp, "isError") and resp.isError():
            raise RuntimeError(f"Modbus error: {resp}")
        return resp

    # ---- I/O ----
    def read_di(self, count=DI_COUNT, address=0):
        rr = self._ok(self._call("read_discrete_inputs", address=address, count=count))
        return [bool(x) for x in list(rr.bits)[:count]]

    def read_do(self, count=DO_COUNT, address=0):
        rr = self._ok(self._call("read_coils", address=address, count=count))
        return [bool(x) for x in list(rr.bits)[:count]]

    def write_do(self, index, value, address_base=0):
        rq = self._ok(self._call("write_coil", address=address_base + index, value=bool(value)))
        return True


# ---- Mapping (with your new Torque station) ----
# Station shaft assembly
SHAFT_ASSEMBLY = [
    (0, "cylinder up"),
    (1, "cylinder down"),
    (2, "shaft bottom gripper"),
    (3, "shaft radial cyl"),
    (5, "shaft upper gripper"),
    (7, "wheel gripper"),
    (8, "wheel radial"),
]

# General station
GENERAL_STATION = [
    (4, "table grippers"),
    (6, "table radial cyls"),
    (12, "Setup cylinder (need password)"),
]

# Station stacks
STATION_STACKS = [
    (9, "stacks push"),
    (10, "slider down"),
]

# NEW: Torque station
TORQUE_STATION = [
    (11, "stepper cylinder down"),
    (14, "servo cylinder down"),
    (15, "picker vacuum"),
]

# Special DI labels
DI_LABELS = {
    5: "hollow_stepper up",
    6: "hollow_stepper down",
}

# Password (change if you like)
SETUP_PASSWORD = "2468"   # <-- set your password here


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MT3A-IO1632 • Manual Operations")
        self.geometry("1000x640")
        self.minsize(900, 580)

        self.mt = MT3AClient()
        self.connected = False
        self.after_id = None

        self.do_state = [False] * DO_COUNT
        self.di_state = [False] * DI_COUNT
        self.authorized_setup = False

        self._build()

    def _build(self):
        # Connection bar
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")

        ttk.Label(top, text="IP:").pack(side="left")
        self.ip_var = tk.StringVar(value=HOST_DEFAULT)
        ttk.Entry(top, textvariable=self.ip_var, width=16).pack(side="left", padx=(4,12))

        ttk.Label(top, text="Unit:").pack(side="left")
        self.unit_var = tk.IntVar(value=UNIT_DEFAULT)
        ttk.Entry(top, textvariable=self.unit_var, width=5).pack(side="left", padx=(4,12))

        self.btn_conn = ttk.Button(top, text="Connect", command=self.on_connect)
        self.btn_disc = ttk.Button(top, text="Disconnect", command=self.on_disconnect, state="disabled")
        self.btn_conn.pack(side="left")
        self.btn_disc.pack(side="left", padx=(6,0))

        ttk.Separator(self).pack(fill="x", pady=6)

        # Main panes
        main = ttk.Frame(self, padding=8)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.columnconfigure(1, weight=2)
        main.rowconfigure(0, weight=1)

        # Left: DI panel
        di_frame = ttk.LabelFrame(main, text="Digital Inputs (live)", padding=8)
        di_frame.grid(row=0, column=0, sticky="nsew", padx=(0,8))

        self.di_rows = []
        di_grid = ttk.Frame(di_frame)
        di_grid.pack(fill="both", expand=True)

        for i in range(DI_COUNT):
            row = ttk.Frame(di_grid, padding=(0,2))
            row.pack(fill="x")
            label = f"DI{i:02d}"
            if i in DI_LABELS:
                label += f" – {DI_LABELS[i]}"
            ttk.Label(row, text=label, width=28).pack(side="left")
            led = tk.Canvas(row, width=18, height=18, highlightthickness=0)
            led.pack(side="left", padx=6)
            self.di_rows.append(led)
            self._set_led(led, False)

        # Right: DO groups (now includes Torque station)
        right = ttk.Frame(main)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)

        nb = ttk.Notebook(right)
        nb.pack(fill="both", expand=True)

        self.frames = {}
        self.buttons = {}  # do_index -> Checkbutton
        for title, mapping in [
            ("Station shaft assembly", SHAFT_ASSEMBLY),
            ("General station", GENERAL_STATION),
            ("Station stacks", STATION_STACKS),
            ("Torque station", TORQUE_STATION),  # <-- added tab
        ]:
            f = ttk.Frame(nb, padding=10)
            nb.add(f, text=title)
            self.frames[title] = f
            self._build_do_group(f, mapping)

        # Status
        ttk.Separator(self).pack(fill="x")
        self.status = tk.StringVar(value="Disconnected")
        ttk.Label(self, textvariable=self.status, anchor="w", padding=6).pack(fill="x")

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_do_group(self, parent, mapping):
        grid = ttk.Frame(parent)
        grid.pack(anchor="w")
        # 2 columns for readability
        left = ttk.Frame(grid, padding=(0,0,20,0)); left.grid(row=0, column=0, sticky="nw")
        right = ttk.Frame(grid); right.grid(row=0, column=1, sticky="nw")

        half = (len(mapping) + 1) // 2
        cols = [mapping[:half], mapping[half:]]

        for col_idx, col in enumerate(cols):
            tgt = left if col_idx == 0 else right
            for do_idx, name in col:
                row = ttk.Frame(tgt, padding=4)
                row.pack(anchor="w")
                ttk.Label(row, text=f"DO{do_idx:02d} – {name}", width=32).pack(side="left")
                btn = ttk.Checkbutton(row, command=lambda i=do_idx: self.on_toggle(i))
                btn.state(['!alternate'])
                btn.pack(side="left", padx=8)
                self.buttons[do_idx] = btn

        # convenience row
        ops = ttk.Frame(parent, padding=(0,8,0,0))
        ops.pack(anchor="w")
        ttk.Button(ops, text="All OFF (this tab)", command=lambda m=mapping: self.all_off(m)).pack(side="left", padx=4)
        ttk.Button(ops, text="Refresh Now", command=self.refresh_once).pack(side="left", padx=10)

    # ---- UI helpers ----
    def _set_led(self, canvas: tk.Canvas, on: bool):
        canvas.delete("all")
        x0, y0, x1, y1 = 2, 2, 16, 16
        color = "#33cc33" if on else "#777777"
        canvas.create_oval(x0, y0, x1, y1, fill=color, outline="")

    # ---- Connection / lifecycle ----
    def on_connect(self):
        if self.connected:
            return
        try:
            self.mt.host = self.ip_var.get().strip()
            self.mt.unit = int(self.unit_var.get())
            self.mt.connect()
            self.connected = True
            self.btn_conn.config(state="disabled")
            self.btn_disc.config(state="normal")
            self.status.set(f"Connected {self.mt.host}:502  unit={self.mt.unit}")
            self._schedule_poll()
        except Exception as e:
            messagebox.showerror("Connect failed", str(e))
            self.status.set(f"Connect failed: {e}")

    def on_disconnect(self):
        self.connected = False
        if self.after_id:
            try: self.after_cancel(self.after_id)
            except: pass
            self.after_id = None
        try: self.mt.close()
        except: pass
        self.btn_conn.config(state="normal")
        self.btn_disc.config(state="disabled")
        self.status.set("Disconnected")

    def on_close(self):
        try: self.on_disconnect()
        finally: self.destroy()

    # ---- Polling ----
    def _schedule_poll(self):
        self.after_id = self.after(POLL_MS, self._poll)

    def _poll(self):
        if not self.connected:
            return
        t0 = time.perf_counter()
        try:
            di = self.mt.read_di(count=DI_COUNT)
            do = self.mt.read_do(count=DO_COUNT)
            t1 = time.perf_counter()

            self.di_state = di
            for i, led in enumerate(self.di_rows):
                self._set_led(led, bool(di[i]))

            self.do_state = do
            for idx, btn in self.buttons.items():
                if idx < len(do):
                    if do[idx]:
                        btn.state(['selected'])
                    else:
                        btn.state(['!selected'])
            self.status.set(f"OK  |  Cycle: {(t1 - t0)*1000:.1f} ms")
        except Exception as e:
            self.status.set(f"I/O error: {e}")
        finally:
            self._schedule_poll()

    def refresh_once(self):
        if not self.connected:
            return
        t0 = time.perf_counter()
        try:
            di = self.mt.read_di(count=DI_COUNT)
            do = self.mt.read_do(count=DO_COUNT)
            t1 = time.perf_counter()
            self.di_state = di
            for i, led in enumerate(self.di_rows):
                self._set_led(led, bool(di[i]))
            self.do_state = do
            for idx, btn in self.buttons.items():
                if idx < len(do):
                    if do[idx]:
                        btn.state(['selected'])
                    else:
                        btn.state(['!selected'])
            self.status.set(f"Refreshed  |  Cycle: {(t1 - t0)*1000:.1f} ms")
        except Exception as e:
            messagebox.showerror("Refresh failed", str(e))
            self.status.set(f"Refresh error: {e}")

    # ---- DO control ----
    def on_toggle(self, do_index: int):
        if not self.connected:
            return

        # Gate DO12 by password
        if do_index == 12 and not self.authorized_setup:
            pwd = simpledialog.askstring("Authorization", "Enter password for Setup cylinder:", show="*")
            if pwd is None:
                self._revert_button(do_index)
                return
            if pwd != SETUP_PASSWORD:
                messagebox.showerror("Unauthorized", "Incorrect password.")
                self._revert_button(do_index)
                return
            self.authorized_setup = True  # keep for rest of session

        # Desired value from checkbox
        val = 'selected' in self.buttons[do_index].state()
        try:
            self.mt.write_do(do_index, val)
            self.status.set(f"Wrote DO{do_index:02d} = {int(val)}")
        except Exception as e:
            messagebox.showerror("Write failed", str(e))
            self.status.set(f"Write error: {e}")
            self._revert_button(do_index)

    def _revert_button(self, do_index: int):
        current = self.do_state[do_index] if 0 <= do_index < len(self.do_state) else False
        if current:
            self.buttons[do_index].state(['selected'])
        else:
            self.buttons[do_index].state(['!selected'])

    def all_off(self, mapping):
        if not self.connected:
            return
        try:
            for do_index, _ in mapping:
                self.mt.write_do(do_index, False)
            # reflect in UI (this tab only)
            for do_index, _ in mapping:
                if do_index in self.buttons:
                    self.buttons[do_index].state(['!selected'])
            self.status.set("All OFF (this tab)")
        except Exception as e:
            messagebox.showerror("Bulk write failed", str(e))
            self.status.set(f"Bulk write error: {e}")


if __name__ == "__main__":
    App().mainloop()
