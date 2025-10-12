# actuator_ui.py
# Enhanced manual UI for MKS SERVO42D over Modbus:
# - Larger window
# - Accel + Decel controls
# - Go ABS / Go REL
# - Auto list of absolute positions (then return to 0)

import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

from actuator_modbus import Servo42DModbus, UNIT_ID

def combine_axis(carry: int, value: int) -> int:
    """Absolute axis counts from encoder parts."""
    return carry * 65536 + (value & 0xFFFF)

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SERVO42D Manual UI (Modbus)")
        self.geometry("760x560")
        self.minsize(720, 520)
        self.resizable(True, True)

        # Driver / comm state
        self.drv = None
        self.connected = False
        self.jog_thread = None
        self.jog_running = False
        self.auto_thread = None
        self.auto_running = False
        self._lock = threading.Lock()   # serialize Modbus writes

        # ===== Connection =====
        frm_conn = ttk.LabelFrame(self, text="Connection")
        frm_conn.pack(fill="x", padx=10, pady=8)

        ttk.Label(frm_conn, text="Port:").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        self.ent_port = ttk.Entry(frm_conn, width=12)
        self.ent_port.insert(0, "COM9")
        self.ent_port.grid(row=0, column=1, padx=4, pady=4, sticky="w")

        ttk.Label(frm_conn, text="Baud:").grid(row=0, column=2, padx=4, pady=4, sticky="e")
        self.ent_baud = ttk.Entry(frm_conn, width=10)
        self.ent_baud.insert(0, "38400")
        self.ent_baud.grid(row=0, column=3, padx=4, pady=4, sticky="w")

        ttk.Label(frm_conn, text="Unit ID:").grid(row=0, column=4, padx=4, pady=4, sticky="e")
        self.ent_unit = ttk.Entry(frm_conn, width=8)
        self.ent_unit.insert(0, str(UNIT_ID if isinstance(UNIT_ID, int) else 1))
        self.ent_unit.grid(row=0, column=5, padx=4, pady=4, sticky="w")

        self.btn_connect = ttk.Button(frm_conn, text="Connect", command=self.on_connect, width=14)
        self.btn_connect.grid(row=0, column=6, padx=6, pady=4)
        self.btn_disconnect = ttk.Button(frm_conn, text="Disconnect", command=self.on_disconnect, state="disabled", width=14)
        self.btn_disconnect.grid(row=0, column=7, padx=6, pady=4)

        # ===== Position =====
        frm_pos = ttk.LabelFrame(self, text="Position")
        frm_pos.pack(fill="x", padx=10, pady=8)

        self.lbl_pos = ttk.Label(frm_pos, text="Axis: —")
        self.lbl_pos.grid(row=0, column=0, padx=6, pady=6, sticky="w")

        self.chk_auto = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm_pos, text="Auto refresh", variable=self.chk_auto).grid(row=0, column=1, padx=6, pady=6)
        ttk.Button(frm_pos, text="Read Now", command=self.update_position_once).grid(row=0, column=2, padx=6, pady=6)

        ttk.Label(frm_pos, text="Tolerance (counts):").grid(row=0, column=3, padx=4, pady=6, sticky="e")
        self.ent_tol = ttk.Entry(frm_pos, width=10)
        self.ent_tol.insert(0, "50")
        self.ent_tol.grid(row=0, column=4, padx=4, pady=6, sticky="w")

        # ===== Go to Position =====
        frm_go = ttk.LabelFrame(self, text="Move Settings")
        frm_go.pack(fill="x", padx=10, pady=8)

        ttk.Label(frm_go, text="Target (counts):").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        self.ent_axis = ttk.Entry(frm_go, width=14)
        self.ent_axis.insert(0, "16000")
        self.ent_axis.grid(row=0, column=1, padx=4, pady=4, sticky="w")

        ttk.Label(frm_go, text="Speed (RPM):").grid(row=0, column=2, padx=4, pady=4, sticky="e")
        self.ent_speed = ttk.Entry(frm_go, width=10)
        self.ent_speed.insert(0, "500")
        self.ent_speed.grid(row=0, column=3, padx=4, pady=4, sticky="w")

        ttk.Label(frm_go, text="Accel (0–255):").grid(row=0, column=4, padx=4, pady=4, sticky="e")
        self.ent_acc = ttk.Entry(frm_go, width=8)
        self.ent_acc.insert(0, "120")
        self.ent_acc.grid(row=0, column=5, padx=4, pady=4, sticky="w")

        ttk.Label(frm_go, text="Decel (0–255):").grid(row=0, column=6, padx=4, pady=4, sticky="e")
        self.ent_decel = ttk.Entry(frm_go, width=8)
        self.ent_decel.insert(0, "120")
        self.ent_decel.grid(row=0, column=7, padx=4, pady=4, sticky="w")

        # Action buttons
        self.btn_go_abs = ttk.Button(frm_go, text="Go ABS", command=self.on_go_abs, width=14)
        self.btn_go_abs.grid(row=1, column=0, columnspan=2, padx=6, pady=6, sticky="we")

        self.btn_go_rel = ttk.Button(frm_go, text="Go REL", command=self.on_go_rel, width=14)
        self.btn_go_rel.grid(row=1, column=2, columnspan=2, padx=6, pady=6, sticky="we")

        self.btn_stop = ttk.Button(frm_go, text="STOP (E‑stop)", command=self.on_stop, width=14)
        self.btn_stop.grid(row=1, column=4, columnspan=2, padx=6, pady=6, sticky="we")

        # ===== Jog (press-and-hold) =====
        frm_jog = ttk.LabelFrame(self, text="Jog (press-and-hold; relative micro-steps)")
        frm_jog.pack(fill="x", padx=10, pady=8)

        ttk.Label(frm_jog, text="Step (counts):").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        self.ent_step = ttk.Entry(frm_jog, width=10)
        self.ent_step.insert(0, "400")
        self.ent_step.grid(row=0, column=1, padx=4, pady=4, sticky="w")

        ttk.Label(frm_jog, text="Interval (ms):").grid(row=0, column=2, padx=4, pady=4, sticky="e")
        self.ent_interval = ttk.Entry(frm_jog, width=10)
        self.ent_interval.insert(0, "60")
        self.ent_interval.grid(row=0, column=3, padx=4, pady=4, sticky="w")

        ttk.Label(frm_jog, text="Jog Speed (RPM):").grid(row=0, column=4, padx=4, pady=4, sticky="e")
        self.ent_jog_speed = ttk.Entry(frm_jog, width=10)
        self.ent_jog_speed.insert(0, "600")
        self.ent_jog_speed.grid(row=0, column=5, padx=4, pady=4, sticky="w")

        ttk.Label(frm_jog, text="Jog Accel (0–255):").grid(row=0, column=6, padx=4, pady=4, sticky="e")
        self.ent_jog_acc = ttk.Entry(frm_jog, width=8)
        self.ent_jog_acc.insert(0, "100")
        self.ent_jog_acc.grid(row=0, column=7, padx=4, pady=4, sticky="w")

        self.btn_jog_ccw = ttk.Button(frm_jog, text="◀ Jog CCW")
        self.btn_jog_ccw.grid(row=1, column=0, columnspan=2, padx=6, pady=6, sticky="we")
        self.btn_jog_cw = ttk.Button(frm_jog, text="Jog CW ▶")
        self.btn_jog_cw.grid(row=1, column=2, columnspan=2, padx=6, pady=6, sticky="we")

        self.btn_jog_ccw.bind("<ButtonPress-1>", lambda e: self.start_jog(direction=-1))
        self.btn_jog_ccw.bind("<ButtonRelease-1>", lambda e: self.stop_jog())
        self.btn_jog_cw.bind("<ButtonPress-1>", lambda e: self.start_jog(direction=+1))
        self.btn_jog_cw.bind("<ButtonRelease-1>", lambda e: self.stop_jog())

        # ===== Auto list runner =====
        frm_auto = ttk.LabelFrame(self, text="Auto Sequence (absolute positions)")
        frm_auto.pack(fill="x", padx=10, pady=8)

        ttk.Label(frm_auto, text="Positions (counts, comma-separated):").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        self.ent_pos_list = ttk.Entry(frm_auto)
        self.ent_pos_list.insert(0, "0, 8000, 16000, 24000, 16000, 8000, 0")
        self.ent_pos_list.grid(row=0, column=1, columnspan=5, padx=4, pady=4, sticky="we")

        ttk.Label(frm_auto, text="Speed:").grid(row=1, column=0, padx=4, pady=4, sticky="e")
        self.ent_auto_speed = ttk.Entry(frm_auto, width=10)
        self.ent_auto_speed.insert(0, "500")
        self.ent_auto_speed.grid(row=1, column=1, padx=4, pady=4, sticky="w")

        ttk.Label(frm_auto, text="Accel:").grid(row=1, column=2, padx=4, pady=4, sticky="e")
        self.ent_auto_acc = ttk.Entry(frm_auto, width=10)
        self.ent_auto_acc.insert(0, "120")
        self.ent_auto_acc.grid(row=1, column=3, padx=4, pady=4, sticky="w")

        ttk.Label(frm_auto, text="Decel:").grid(row=1, column=4, padx=4, pady=4, sticky="e")
        self.ent_auto_decel = ttk.Entry(frm_auto, width=10)
        self.ent_auto_decel.insert(0, "120")
        self.ent_auto_decel.grid(row=1, column=5, padx=4, pady=4, sticky="w")

        self.btn_auto_start = ttk.Button(frm_auto, text="Start Auto", command=self.on_auto_start, width=14)
        self.btn_auto_start.grid(row=2, column=0, columnspan=2, padx=6, pady=6, sticky="we")
        self.btn_auto_stop = ttk.Button(frm_auto, text="Stop Auto", command=self.on_auto_stop, width=14)
        self.btn_auto_stop.grid(row=2, column=2, columnspan=2, padx=6, pady=6, sticky="we")

        frm_auto.grid_columnconfigure(1, weight=1)
        frm_auto.grid_columnconfigure(3, weight=0)
        frm_auto.grid_columnconfigure(5, weight=0)

        # ===== Status line =====
        self.status = tk.StringVar(value="Disconnected")
        ttk.Label(self, textvariable=self.status, anchor="w").pack(fill="x", padx=10, pady=(6, 10))

        # Auto refresh loop
        self.after(150, self.auto_refresh_tick)

    # ---------------- Connection ----------------
    def on_connect(self):
        if self.connected:
            return
        try:
            result = self.drv.self_calibrate_position_block(guess_addr=0x00F4, test_span=500, rpm=200, acc=80, tol=80)
            self.status.set(
                f"Calibrated: addr={result['address']}, word={result['word_order']}, mode={result['semantics']}")
        except Exception as e:
            self.status.set(f"Calibration failed: {e}")
        try:
            port = self.ent_port.get().strip()
            baud = int(self.ent_baud.get().strip())
            unit = int(self.ent_unit.get().strip())
            self.drv = Servo42DModbus(port=port, baudrate=baud, unit=unit)
            self.connected = True
            self.btn_connect.config(state="disabled")
            self.btn_disconnect.config(state="normal")
            self.status.set(f"Connected: {port} @ {baud}, unit={unit}")
            try:
                self.drv.set_work_mode(5)  # SR_vFOC
            except Exception:
                pass
            self.update_position_once()
        except Exception as e:
            self.drv = None
            self.connected = False
            messagebox.showerror("Connection failed", str(e))
            self.status.set("Disconnected")

    def on_disconnect(self):
        self.stop_jog()
        self.on_auto_stop()
        if self.drv:
            try:
                self.drv.close()
            except Exception:
                pass
        self.drv = None
        self.connected = False
        self.btn_connect.config(state="normal")
        self.btn_disconnect.config(state="disabled")
        self.status.set("Disconnected")

    # ---------------- Position ----------------
    def update_position_once(self):
        if not self.connected or not self.drv:
            return
        try:
            carry, value = self.drv.read_encoder()
            axis = combine_axis(carry, value)
            self.lbl_pos.config(text=f"Axis: {axis}   (carry={carry}, value={value})")
        except Exception as e:
            self.lbl_pos.config(text=f"Axis: —")
            self.status.set(f"Read failed: {e}")

    def auto_refresh_tick(self):
        if self.connected and self.chk_auto.get():
            self.update_position_once()
        self.after(150, self.auto_refresh_tick)

    # ---------------- Moves ----------------
    def _read_move_params(self):
        speed = int(self.ent_speed.get().strip())
        acc = int(self.ent_acc.get().strip())
        decel = int(self.ent_decel.get().strip())
        tol = max(0, int(self.ent_tol.get().strip()))
        return speed, acc, decel, tol

    def on_go_abs(self):
        if not self.connected or not self.drv:
            return
        try:
            axis = int(self.ent_axis.get().strip())
            speed, acc, decel, _ = self._read_move_params()
            # NOTE: this drive uses one "acc" value for accel & decel. We'll
            # just use your decel value as the acc for this command (symmetric).
            with self._lock:
                self.drv.go_to_position(axis_counts=axis, speed_rpm=speed, acc=decel)
            self.status.set(f"Go ABS → {axis} @ {speed}rpm, acc/decel={decel}")
        except Exception as e:
            messagebox.showerror("Go ABS failed", str(e))

    def on_go_rel(self):
        if not self.connected or not self.drv:
            return
        try:
            delta = int(self.ent_axis.get().strip())
            speed, acc, decel, _ = self._read_move_params()
            carry, value = self.drv.read_encoder()
            cur = combine_axis(carry, value)
            target = cur + delta
            with self._lock:
                self.drv.go_to_position(axis_counts=target, speed_rpm=speed, acc=decel)
            self.status.set(f"Go REL {delta:+} → target {target} @ {speed}rpm, acc/decel={decel}")
        except Exception as e:
            messagebox.showerror("Go REL failed", str(e))

    # ---------------- Jog (press-and-hold via repeated relative nudges) ----------------
    def start_jog(self, direction: int):
        if self.jog_running or not self.connected or not self.drv:
            return
        try:
            step = int(self.ent_step.get().strip())
            interval_ms = int(self.ent_interval.get().strip())
            speed = int(self.ent_jog_speed.get().strip())
            acc = int(self.ent_jog_acc.get().strip())
        except Exception as e:
            messagebox.showerror("Jog config error", str(e))
            return

        self.jog_running = True

        def jog_loop():
            while self.jog_running and self.connected and self.drv:
                try:
                    carry, value = self.drv.read_encoder()
                    cur = combine_axis(carry, value)
                    target = cur + (direction * step)
                    with self._lock:
                        self.drv.go_to_position(axis_counts=target, speed_rpm=speed, acc=acc)
                    self.status.set(f"Jog {'CW' if direction>0 else 'CCW'} → target {target}")
                except Exception as e:
                    self.status.set(f"Jog error: {e}")
                    break
                time.sleep(max(0.02, interval_ms / 1000.0))
            self.jog_running = False

        self.jog_thread = threading.Thread(target=jog_loop, daemon=True)
        self.jog_thread.start()

    def stop_jog(self):
        self.jog_running = False

    def on_stop(self):
        self.stop_jog()
        self.on_auto_stop()
        if self.connected and self.drv:
            try:
                with self._lock:
                    self.drv.estop()
                self.status.set("E‑stop sent")
            except Exception as e:
                self.status.set(f"E‑stop failed: {e}")

    # ---------------- Auto sequence ----------------
    def on_auto_start(self):
        if self.auto_running or not self.connected or not self.drv:
            return
        try:
            # Parse list of absolute positions
            raw = self.ent_pos_list.get()
            targets = [int(x.strip()) for x in raw.split(",") if x.strip() != ""]
            if not targets:
                raise ValueError("No positions provided.")
            speed = int(self.ent_auto_speed.get().strip())
            acc = int(self.ent_auto_acc.get().strip())
            decel = int(self.ent_auto_decel.get().strip())
            tol = max(0, int(self.ent_tol.get().strip()))
        except Exception as e:
            messagebox.showerror("Auto config error", str(e))
            return

        self.auto_running = True

        def auto_loop():
            try:
                for i, tgt in enumerate(targets):
                    if not self.auto_running: break
                    self.status.set(f"Auto: moving to {tgt} ({i+1}/{len(targets)})")
                    with self._lock:
                        # Use decel value as the move "acc" (symmetric accel/decel in this drive)
                        self.drv.go_to_position(axis_counts=tgt, speed_rpm=speed, acc=decel)
                    # wait until within tolerance (or stop requested)
                    if not self._wait_until_reached(tgt, tol, timeout_s=None):
                        break
                if self.auto_running:
                    # return to zero at end
                    self.status.set("Auto: returning to 0")
                    with self._lock:
                        self.drv.go_to_position(axis_counts=0, speed_rpm=speed, acc=decel)
                    self._wait_until_reached(0, tol, timeout_s=None)
            finally:
                self.auto_running = False
                self.status.set("Auto: done" if self.connected else "Disconnected")

        self.auto_thread = threading.Thread(target=auto_loop, daemon=True)
        self.auto_thread.start()

    def on_auto_stop(self):
        self.auto_running = False

    def _wait_until_reached(self, target: int, tol: int, timeout_s=None, sample_ms=120):
        """Poll position until |actual - target| <= tol, or timeout/stop/disconnect."""
        t0 = time.time()
        while self.connected and self.auto_running:
            try:
                carry, value = self.drv.read_encoder()
                cur = combine_axis(carry, value)
                if abs(cur - target) <= tol:
                    return True
            except Exception as e:
                self.status.set(f"Auto wait read error: {e}")
                return False
            if timeout_s is not None and (time.time() - t0) > timeout_s:
                self.status.set("Auto: wait timeout")
                return False
            time.sleep(max(0.05, sample_ms / 1000.0))
        return False

if __name__ == "__main__":
    App().mainloop()
