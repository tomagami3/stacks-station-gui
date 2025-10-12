# mt3a_gui.py
import tkinter as tk
from tkinter import ttk, messagebox
from inspect import signature
from pymodbus.client import ModbusTcpClient
import time

POLL_MS = 1  # DI/DO refresh period

from pymodbus.client import ModbusTcpClient

class MT3AClient:
    """
    Version-tolerant Modbus TCP client for MT3A that works with pymodbus 1.x / 2.x / 3.x.
    Strategy order per call:
      1) try slave=...
      2) try unit=...
      3) set self.client.unit_id then call with no unit kw
    """
    def __init__(self, host="192.168.1.12", port=502, unit=1, timeout=2.0):
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

    # --- internal helpers ---
    def _call(self, method_name, **kwargs):
        fn = getattr(self.client, method_name)
        # 1) try slave=
        try:
            return fn(**kwargs, slave=self.unit)
        except TypeError:
            pass
        # 2) try unit=
        try:
            return fn(**kwargs, unit=self.unit)
        except TypeError:
            pass
        # 3) set unit_id on client and call without kw
        try:
            setattr(self.client, "unit_id", self.unit)
        except Exception:
            pass
        return fn(**kwargs)

    def _raise_if_err(self, resp):
        if hasattr(resp, "isError") and resp.isError():
            raise RuntimeError(resp)
        return resp

    # --- Digital I/O ---
    def read_di(self, count=16, address=0):
        rr = self._call("read_discrete_inputs", address=address, count=count)
        self._raise_if_err(rr)
        return list(rr.bits)[:count]

    def read_do(self, count=16, address=0):
        rr = self._call("read_coils", address=address, count=count)
        self._raise_if_err(rr)
        return list(rr.bits)[:count]

    def write_do(self, index, value, address_base=0):
        rq = self._call("write_coil", address=address_base + index, value=bool(value))
        self._raise_if_err(rq)
        return True

    def write_dos(self, values, address=0):
        rq = self._call("write_coils", address=address, values=[bool(v) for v in values])
        self._raise_if_err(rq)
        return True

    # --- Analog (if you add AI/AO modules later) ---
    def read_ai(self, start=0, count=8):
        rr = self._call("read_input_registers", address=start, count=count)
        self._raise_if_err(rr)
        return rr.registers

    def read_ao(self, start=0, count=8):
        rr = self._call("read_holding_registers", address=start, count=count)
        self._raise_if_err(rr)
        return rr.registers

    def write_ao(self, start, values):
        rq = self._call("write_registers", address=start, values=values)
        self._raise_if_err(rq)
        return True


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MT3A‑IO1632 I/O Panel")
        self.geometry("880x520")
        self.resizable(True, True)

        self.mt = MT3AClient()
        self.connected = False
        self._after_id = None
        self.do_state = [False]*16
        self.blinker = False
        self._build_ui()
        self.start = time.perf_counter()

    def _build_ui(self):
        # Top: connection bar
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")

        ttk.Label(top, text="IP:").pack(side="left")
        self.ip_var = tk.StringVar(value="192.168.1.12")
        ttk.Entry(top, textvariable=self.ip_var, width=16).pack(side="left", padx=(2,10))

        ttk.Label(top, text="Unit:").pack(side="left")
        self.unit_var = tk.IntVar(value=1)
        ttk.Entry(top, textvariable=self.unit_var, width=5).pack(side="left", padx=(2,10))

        self.btn_connect = ttk.Button(top, text="Connect", command=self.on_connect)
        self.btn_connect.pack(side="left", padx=(0,6))
        self.btn_disc = ttk.Button(top, text="Disconnect", command=self.on_disconnect, state="disabled")
        self.btn_disc.pack(side="left")

        ttk.Separator(self).pack(fill="x", pady=6)

        # Middle: DI + DO frames
        mid = ttk.Frame(self, padding=8)
        mid.pack(fill="both", expand=True)

        di_frame = ttk.LabelFrame(mid, text="Digital Inputs (DI 0..15)", padding=8)
        di_frame.grid(row=0, column=0, sticky="nsew", padx=(0,8))
        do_frame = ttk.LabelFrame(mid, text="Digital Outputs (DO 0..16)", padding=8)
        do_frame.grid(row=0, column=1, sticky="nsew")

        mid.columnconfigure(0, weight=1)
        mid.columnconfigure(1, weight=2)
        mid.rowconfigure(0, weight=1)

        # DI LEDs (16)
        self.di_leds = []
        for i in range(16):
            r, c = divmod(i, 8)
            f = ttk.Frame(di_frame, padding=4)
            f.grid(row=r, column=c, padx=6, pady=6, sticky="w")
            ttk.Label(f, text=f"DI{i:02d}").pack(side="left")
            led = tk.Canvas(f, width=18, height=18, highlightthickness=0)
            led.pack(side="left", padx=6)
            self.di_leds.append(led)
            self._set_led(led, False)

        # DO toggles (32)
        self.do_buttons = []
        grid = ttk.Frame(do_frame)
        grid.pack(fill="both", expand=True)
        for i in range(16):
            r, c = divmod(i, 8)
            cell = ttk.Frame(grid, padding=4)
            cell.grid(row=r, column=c, padx=4, pady=4, sticky="w")
            ttk.Label(cell, text=f"DO{i:02d}").pack(side="left")
            btn = ttk.Checkbutton(cell, command=lambda idx=i: self.on_do_toggle(idx))
            btn.state(['!alternate'])
            btn.pack(side="left", padx=6)
            self.do_buttons.append(btn)

        # DO bulk ops
        ops = ttk.Frame(do_frame, padding=(0,8,0,0))
        ops.pack(anchor="w")
        ttk.Button(ops, text="All ON", command=lambda: self.bulk_do(True)).pack(side="left", padx=4)
        ttk.Button(ops, text="All OFF", command=lambda: self.bulk_do(False)).pack(side="left", padx=4)
        ttk.Button(ops, text="Blink", command=lambda: self.blink()).pack(side="left", padx=4)

        # Status bar
        self.status = tk.StringVar(value="Disconnected")
        ttk.Separator(self).pack(fill="x")
        ttk.Label(self, textvariable=self.status, anchor="w", padding=6).pack(fill="x")

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _set_led(self, canvas: tk.Canvas, on: bool):
        canvas.delete("all")
        x0, y0, x1, y1 = 2, 2, 16, 16
        color = "#33cc33" if on else "#777777"
        canvas.create_oval(x0, y0, x1, y1, fill=color, outline="")

    # ---- Connection handlers ----
    def on_connect(self):
        if self.connected:
            return
        try:
            self.mt.host = self.ip_var.get().strip()
            self.mt.unit = int(self.unit_var.get())
            self.mt.connect()
            self.connected = True
            self.status.set(f"Connected to {self.mt.host}:502, unit={self.mt.unit}")
            self.btn_connect.config(state="disabled")
            self.btn_disc.config(state="normal")
            self.poll_io()
        except Exception as e:
            messagebox.showerror("Connect failed", str(e))
            self.status.set(f"Connect failed: {e}")

    def on_disconnect(self):
        self.connected = False
        if self._after_id:
            self.after_cancel(self._after_id); self._after_id = None
        self.mt.close()
        self.status.set("Disconnected")
        self.btn_connect.config(state="normal")
        self.btn_disc.config(state="disabled")

    def on_close(self):
        try: self.on_disconnect()
        except: pass
        self.destroy()

    # ---- I/O logic ----
    def poll_io(self):
        if not self.connected:
            return
        try:
            end = time.perf_counter()
            print(f"Read time: {(end - self.start) * 1000:.2f} ms")
            self.start = time.perf_counter()
            di = self.mt.read_di(count=16)
            for i, v in enumerate(di):
                self._set_led(self.di_leds[i], bool(v))

            do = self.mt.read_do(count=16)
            self.do_state = [bool(x) for x in do]
            # Update checkbuttons without triggering command
            for i, btn in enumerate(self.do_buttons):
                if self.do_state[i]:
                    btn.state(['selected'])
                else:
                    btn.state(['!selected'])
            self.status.set(f"OK  DI0..15={''.join('1' if x else '0' for x in di)}")
            if self.blinker:
                self.mt.write_do(5, not self.do_state[5] )
        except Exception as e:
            self.status.set(f"I/O error: {e}")
        finally:
            self._after_id = self.after(POLL_MS, self.poll_io)

    def on_do_toggle(self, idx):
        if not self.connected:
            return
        try:
            # Use current button state to decide the value
            selected = 'selected' in self.do_buttons[idx].state()
            self.mt.write_do(idx, selected)
            self.status.set(f"Wrote DO{idx:02d}={int(selected)}")
        except Exception as e:
            messagebox.showerror("Write failed", str(e))
            self.status.set(f"Write error: {e}")

    def bulk_do(self, value: bool):
        if not self.connected:
            return
        try:
            for i in range(16):
                self.mt.write_do(i, value)
            self.status.set(f"All DO set to {int(value)}")
        except Exception as e:
            messagebox.showerror("Bulk write failed", str(e))
            self.status.set(f"Bulk write error: {e}")

    def blink(self):
            self.blinker = not self.blinker
            # self.mt.write_do(5, self.blinker)



if __name__ == "__main__":
    App().mainloop()
