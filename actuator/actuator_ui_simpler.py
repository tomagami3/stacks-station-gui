# actuator_ui_simple.py
# Minimal 7-button ABS UI for MKS SERVO42D over serial/Modbus-style API.
# - 7 absolute target buttons (each has its own editable field)
# - One REL button (uses "Delta" field)
# - Speed / Acc / Decel fields
# - Connect / Disconnect
# - "Zero here" (0x92) button to set current position = 0 (no motion)
# - Save/Load settings (COM, baud, unit, speed/acc/decel, 7 targets)
#
# Requires: actuator_modbus.py providing Servo42DModbus with:
#   - __init__(port, baudrate, unit)
#   - close()
#   - read_encoder() -> (carry:int32, value:uint16)
#   - go_to_position(axis_counts:int, speed_rpm:int, acc:int)
#   - set_work_mode(mode:int)  # we'll try SR_vFOC (5) but ignore failures
#   - set_axis_zero()          # sends command 0x92 (or equivalent)
#   - estop()                  # optional; used to stop quick
#
# If you don't have set_axis_zero(), add one that sends FA 01 92 00 CRC.

import json
import os
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from actuator_modbus import Servo42DModbus

SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".servo42d_ui.json")
PER_REV = 0x4000            # 16384 counts per revolution
VALUE_MASK = 0x3FFF         # 14-bit value range

def combine_axis(carry: int, value: int) -> int:
    """Combine encoder carry + value into continuous absolute counts.
    Manual: value ∈ [0..0x3FFF], one full turn = 0x4000."""
    return carry * PER_REV + (value & VALUE_MASK)

DEFAULTS = {
    "port": "COM9",
    "baud": 38400,
    "unit": 1,
    "speed": 300,
    "acc": 80,
    "decel": 80,
    "targets": [0, 2000, 4000, 8000, 12000, 16000, 24000],
}

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SERVO42D – Simple ABS UI")
        self.geometry("620x520")
        self.minsize(600, 480)

        self.drv = None
        self.connected = False
        self._lock = threading.Lock()
        self.auto_refresh = True

        self.state = self._load_settings()

        # --- Connection ---
        frm_conn = ttk.LabelFrame(self, text="Connection")
        frm_conn.pack(fill="x", padx=10, pady=8)

        ttk.Label(frm_conn, text="Port").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        self.ent_port = ttk.Entry(frm_conn, width=12)
        self.ent_port.insert(0, self.state["port"])
        self.ent_port.grid(row=0, column=1, padx=4, pady=4)

        ttk.Label(frm_conn, text="Baud").grid(row=0, column=2, padx=4, pady=4, sticky="e")
        self.ent_baud = ttk.Entry(frm_conn, width=8)
        self.ent_baud.insert(0, str(self.state["baud"]))
        self.ent_baud.grid(row=0, column=3, padx=4, pady=4)

        ttk.Label(frm_conn, text="Unit").grid(row=0, column=4, padx=4, pady=4, sticky="e")
        self.ent_unit = ttk.Entry(frm_conn, width=6)
        self.ent_unit.insert(0, str(self.state["unit"]))
        self.ent_unit.grid(row=0, column=5, padx=4, pady=4)

        self.btn_connect = ttk.Button(frm_conn, text="Connect", command=self.on_connect, width=12)
        self.btn_connect.grid(row=0, column=6, padx=6, pady=4)
        self.btn_disconnect = ttk.Button(frm_conn, text="Disconnect", command=self.on_disconnect, width=12, state="disabled")
        self.btn_disconnect.grid(row=0, column=7, padx=6, pady=4)

        # --- Motion params ---
        frm_mv = ttk.LabelFrame(self, text="Motion")
        frm_mv.pack(fill="x", padx=10, pady=8)

        ttk.Label(frm_mv, text="Speed (RPM)").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        self.ent_speed = ttk.Entry(frm_mv, width=8)
        self.ent_speed.insert(0, str(self.state["speed"]))
        self.ent_speed.grid(row=0, column=1, padx=4, pady=4)

        ttk.Label(frm_mv, text="Acc").grid(row=0, column=2, padx=4, pady=4, sticky="e")
        self.ent_acc = ttk.Entry(frm_mv, width=8)
        self.ent_acc.insert(0, str(self.state["acc"]))
        self.ent_acc.grid(row=0, column=3, padx=4, pady=4)

        ttk.Label(frm_mv, text="Decel").grid(row=0, column=4, padx=4, pady=4, sticky="e")
        self.ent_decel = ttk.Entry(frm_mv, width=8)
        self.ent_decel.insert(0, str(self.state["decel"]))
        self.ent_decel.grid(row=0, column=5, padx=4, pady=4)

        self.btn_save = ttk.Button(frm_mv, text="Save settings", command=self.on_save, width=14)
        self.btn_save.grid(row=0, column=6, padx=6, pady=4)

        # --- Current position + zero ---
        frm_pos = ttk.LabelFrame(self, text="Position")
        frm_pos.pack(fill="x", padx=10, pady=8)

        self.lbl_pos = ttk.Label(frm_pos, text="Axis: —")
        self.lbl_pos.grid(row=0, column=0, padx=6, pady=6, sticky="w")

        self.btn_zero = ttk.Button(frm_pos, text="Zero here (0x92)", command=self.on_zero_here, width=16, state="disabled")
        self.btn_zero.grid(row=0, column=1, padx=6, pady=6, sticky="w")

        # --- 7 absolute target buttons ---
        frm_buttons = ttk.LabelFrame(self, text="Targets (ABS counts)")
        frm_buttons.pack(fill="x", padx=10, pady=8)

        self.target_vars = []
        self.target_entries = []
        for i in range(7):
            row = i // 1
            col = 0
            v = tk.StringVar(value=str(self.state["targets"][i]))
            self.target_vars.append(v)
            ent = ttk.Entry(frm_buttons, textvariable=v, width=14)
            ent.grid(row=row, column=col, padx=6, pady=6, sticky="w")
            self.target_entries.append(ent)

            btn = ttk.Button(frm_buttons, text=f"Go {i+1}", command=lambda idx=i: self.on_go_abs_idx(idx), width=10)
            btn.grid(row=row, column=col+1, padx=6, pady=6, sticky="w")

        # --- Relative move ---
        frm_rel = ttk.LabelFrame(self, text="Relative move")
        frm_rel.pack(fill="x", padx=10, pady=8)
        ttk.Label(frm_rel, text="Delta (counts)").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        self.ent_delta = ttk.Entry(frm_rel, width=12)
        self.ent_delta.insert(0, "0")
        self.ent_delta.grid(row=0, column=1, padx=4, pady=4)
        ttk.Button(frm_rel, text="Go REL", command=self.on_go_rel, width=10).grid(row=0, column=2, padx=6, pady=4)

        # --- Stop + status ---
        frm_stop = ttk.Frame(self)
        frm_stop.pack(fill="x", padx=10, pady=6)
        ttk.Button(frm_stop, text="E-STOP", command=self.on_stop, width=10).pack(side="right")
        self.status = tk.StringVar(value="Disconnected")
        ttk.Label(self, textvariable=self.status, anchor="w").pack(fill="x", padx=10, pady=(4, 10))

        # periodic refresh
        self.after(150, self._tick)

    # ---------- helpers ----------
    def _load_settings(self):
        s = DEFAULTS.copy()
        try:
            if os.path.isfile(SETTINGS_PATH):
                with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                    s.update(json.load(f))
        except Exception:
            pass
        # validate targets
        t = s.get("targets", [])
        if not isinstance(t, list) or len(t) != 7:
            s["targets"] = DEFAULTS["targets"]
        return s

    def _read_motion(self):
        speed = int(self.ent_speed.get().strip())
        acc = int(self.ent_acc.get().strip())
        decel = int(self.ent_decel.get().strip())
        # The drive uses a single "acc" parameter; we treat it as decel too.
        acc_used = decel if decel > 0 else acc
        return speed, acc_used

    def _read_position_once(self):
        if not (self.connected and self.drv):
            return None
        try:
            carry, value = self.drv.read_encoder()
            return combine_axis(carry, value)
        except Exception as e:
            self.status.set(f"Read failed: {e}")
            return None

    # ---------- UI actions ----------
    def on_connect(self):
        if self.connected:
            return
        try:
            port = self.ent_port.get().strip()
            baud = int(self.ent_baud.get().strip())
            unit = int(self.ent_unit.get().strip())
            self.drv = Servo42DModbus(port=port, baudrate=baud, unit=unit)
            self.connected = True
            self.btn_connect.config(state="disabled")
            self.btn_disconnect.config(state="normal")
            self.btn_zero.config(state="normal")
            self.status.set(f"Connected: {port}@{baud} unit={unit}")
            # Try SR_vFOC (5). If your firmware only supports SR_CLOSE (4), change here.
            try:
                self.drv.set_work_mode(5)
            except Exception:
                pass
        except Exception as e:
            self.connected = False
            self.drv = None
            messagebox.showerror("Connect failed", str(e))
            self.status.set("Disconnected")

    def on_disconnect(self):
        if self.drv:
            try:
                self.drv.close()
            except Exception:
                pass
        self.drv = None
        self.connected = False
        self.btn_connect.config(state="normal")
        self.btn_disconnect.config(state="disabled")
        self.btn_zero.config(state="disabled")
        self.status.set("Disconnected")

    def on_zero_here(self):
        if not (self.connected and self.drv):
            return
        try:
            with self._lock:
                # Must map to FA 01 92 ... (Set current axis to zero)
                self.drv.set_axis_zero()
            self.status.set("Axis zeroed here (0x92)")
        except Exception as e:
            messagebox.showerror("Zero failed", str(e))

    def on_go_abs_idx(self, idx: int):
        if not (self.connected and self.drv):
            return
        try:
            tgt1 = int(self.target_vars[idx].get().strip())
            cur = self._read_position_once()
            tgt=tgt1-cur
            speed, acc_used = self._read_motion()
            with self._lock:
                self.drv.go_to_position(axis_counts=tgt, speed_rpm=speed, acc=acc_used)
            self.status.set(f"ABS → {tgt} @ {speed} rpm, acc={acc_used}")
        except Exception as e:
            messagebox.showerror(f"ABS {idx+1} failed", str(e))
    def on_go_abs(self):
        """Make ABS truly absolute by reading the current axis, computing delta,
        and sending the relevant command (prefer relative if driver exposes it)."""
        if not self.connected or not self.drv:
            return
        try:
            desired = int(self.ent_axis.get().strip())
            speed, acc, decel, _ = self._read_move_params()

            # 1) Read real position (your combine_axis/read_encoder is now OK)
            carry, value = self.drv.read_encoder()
            current = combine_axis(carry, value)
            delta = desired - current
            if delta == 0:
                self.status.set(f"Already at {desired}")
                return

            with self._lock:
                # 2) If driver exposes an explicit relative move, use it
                if hasattr(self.drv, "go_relative"):
                    # expected signature: go_relative(delta_counts, speed_rpm, acc)
                    self.drv.go_relative(delta_counts=delta, speed_rpm=speed, acc=decel)
                    self.status.set(f"ABS→REL delta {delta:+} to reach {desired} @ {speed}rpm, acc={decel}")
                else:
                    # 3) Fallback: emulate relative by commanding the computed absolute target
                    # (this works if go_to_position expects an absolute setpoint)
                    absolute_target = current + delta  # == desired
                    self.drv.go_to_position(axis_counts=absolute_target, speed_rpm=speed, acc=decel)
                    self.status.set(f"ABS→ABS target {absolute_target} (from {current}) @ {speed}rpm, acc={decel}")

        except Exception as e:
            messagebox.showerror("Go ABS failed", str(e))


    def on_go_rel(self):
        if not (self.connected and self.drv):
            return
        try:
            delta = int(self.ent_delta.get().strip())
            cur = self._read_position_once()
            if cur is None:
                return
            tgt = cur + delta
            speed, acc_used = self._read_motion()
            with self._lock:
                self.drv.go_to_position(axis_counts=tgt, speed_rpm=speed, acc=acc_used)
            self.status.set(f"REL {delta:+} → {tgt} @ {speed} rpm, acc={acc_used}")
        except Exception as e:
            messagebox.showerror("REL failed", str(e))

    def on_stop(self):
        if self.connected and self.drv:
            try:
                with self._lock:
                    self.drv.estop()
                self.status.set("E-STOP sent")
            except Exception as e:
                self.status.set(f"E-STOP failed: {e}")

    def on_save(self):
        try:
            st = {
                "port": self.ent_port.get().strip(),
                "baud": int(self.ent_baud.get().strip()),
                "unit": int(self.ent_unit.get().strip()),
                "speed": int(self.ent_speed.get().strip()),
                "acc": int(self.ent_acc.get().strip()),
                "decel": int(self.ent_decel.get().strip()),
                "targets": [int(v.get().strip()) for v in self.target_vars],
            }
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(st, f, indent=2)
            self.state = st
            self.status.set(f"Saved settings → {SETTINGS_PATH}")
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    # ---------- periodic ----------
    def _tick(self):
        if self.connected and self.auto_refresh:
            cur = self._read_position_once()
            if cur is not None:
                self.lbl_pos.config(text=f"Axis: {cur}")
        self.after(150, self._tick)

if __name__ == "__main__":
    App().mainloop()
