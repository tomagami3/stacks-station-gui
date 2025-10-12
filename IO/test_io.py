# mt3a_do_panel.py
# Minimal GUI to read/toggle DO0..DO15 on MT3A-IO1632

import tkinter as tk
from tkinter import ttk, messagebox
import time
from pymodbus.client import ModbusTcpClient

HOST_DEFAULT = "192.168.1.12"
UNIT_DEFAULT = 1
DO_COUNT = 16
POLL_MS = 300  # auto-refresh interval

# ---- Modbus helper (works with pymodbus 1.x / 2.x / 3.x) ----
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

    def read_do(self, count=DO_COUNT, address=0):
        rr = self._ok(self._call("read_coils", address=address, count=count))
        return [bool(x) for x in list(rr.bits)[:count]]

    def write_do(self, index, value, address_base=0):
        rq = self._ok(self._call("write_coil", address=address_base + index, value=bool(value)))
        return True


# ---- GUI ----
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MT3A-IO1632  •  DO Panel (PNP/NPN outputs)")
        self.geometry("600x360")
        self.mt = MT3AClient()
        self.connected = False
        self.after_id = None
        self.do_state = [False] * DO_COUNT
        self._build()

    def _build(self):
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

        mid = ttk.Frame(self, padding=8)
        mid.pack(fill="both", expand=True)

        # DO grid
        do_frame = ttk.LabelFrame(mid, text="Digital Outputs (DO0..DO15)", padding=8)
        do_frame.pack(fill="both", expand=True)

        self.do_buttons = []
        grid = ttk.Frame(do_frame)
        grid.pack(anchor="w")

        for i in range(DO_COUNT):
            r, c = divmod(i, 8)
            cell = ttk.Frame(grid, padding=4)
            cell.grid(row=r, column=c, padx=4, pady=4, sticky="w")
            ttk.Label(cell, text=f"DO{i:02d}").pack(side="left")
            btn = ttk.Checkbutton(cell, command=lambda idx=i: self.on_toggle(idx))
            btn.state(['!alternate'])
            btn.pack(side="left", padx=6)
            self.do_buttons.append(btn)

        ops = ttk.Frame(do_frame, padding=(0,8,0,0))
        ops.pack(anchor="w")
        ttk.Button(ops, text="All ON", command=lambda: self.set_all(True)).pack(side="left", padx=4)
        ttk.Button(ops, text="All OFF", command=lambda: self.set_all(False)).pack(side="left", padx=4)
        ttk.Button(ops, text="Refresh Now", command=self.refresh_once).pack(side="left", padx=12)

        ttk.Separator(self).pack(fill="x")
        self.status = tk.StringVar(value="Disconnected")
        ttk.Label(self, textvariable=self.status, anchor="w", padding=6).pack(fill="x")

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---- connection ----
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

    # ---- I/O ----
    def _schedule_poll(self):
        self.after_id = self.after(POLL_MS, self._poll)

    def _poll(self):
        if not self.connected:
            return
        t0 = time.perf_counter()
        try:
            do = self.mt.read_do(count=DO_COUNT)
            t1 = time.perf_counter()
            self.do_state = do
            # Update UI without re-triggering commands
            for i, btn in enumerate(self.do_buttons):
                if do[i]:
                    btn.state(['selected'])
                else:
                    btn.state(['!selected'])
            self.status.set(f"OK  |  Cycle: {(t1 - t0)*1000:.1f} ms")
        except Exception as e:
            self.status.set(f"I/O error: {e}")
        finally:
            self._schedule_poll()

    def refresh_once(self):
        # One-shot read & UI update (also shows cycle time)
        if not self.connected:
            return
        t0 = time.perf_counter()
        try:
            do = self.mt.read_do(count=DO_COUNT)
            t1 = time.perf_counter()
            self.do_state = do
            for i, btn in enumerate(self.do_buttons):
                if do[i]:
                    btn.state(['selected'])
                else:
                    btn.state(['!selected'])
            self.status.set(f"Refreshed  |  Cycle: {(t1 - t0)*1000:.1f} ms")
        except Exception as e:
            messagebox.showerror("Refresh failed", str(e))
            self.status.set(f"Refresh error: {e}")

    def on_toggle(self, idx):
        if not self.connected:
            return
        # Use the current checkbox state to decide the write value
        val = 'selected' in self.do_buttons[idx].state()
        try:
            self.mt.write_do(idx, val)
            self.status.set(f"Wrote DO{idx}={int(val)}")
        except Exception as e:
            messagebox.showerror("Write failed", str(e))
            self.status.set(f"Write error: {e}")
            # revert UI on failure
            if val:
                self.do_buttons[idx].state(['!selected'])
            else:
                self.do_buttons[idx].state(['selected'])

    def set_all(self, value: bool):
        if not self.connected:
            return
        try:
            for i in range(DO_COUNT):
                self.mt.write_do(i, value)
            # reflect in UI
            for btn in self.do_buttons:
                if value:
                    btn.state(['selected'])
                else:
                    btn.state(['!selected'])
            self.status.set(f"All DO = {int(value)}")
        except Exception as e:
            messagebox.showerror("Bulk write failed", str(e))
            self.status.set(f"Bulk write error: {e}")

if __name__ == "__main__":
    App().mainloop()
