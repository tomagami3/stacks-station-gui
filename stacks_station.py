# ====================== PATH FIX (keep at top) ======================
import sys, os
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "actuator"))
sys.path.insert(0, os.path.join(ROOT, "IO"))
sys.path.insert(0, os.path.join(ROOT, "cameras"))
# ===================================================================

import time
import threading
from typing import Optional, List, Tuple

import tkinter as tk
from tkinter import ttk, messagebox

import cv2
import numpy as np
from PIL import Image, ImageTk

# ----- Your modules -----
# Motor
from actuator_modbus import Servo42DModbus
try:
    from actuator_ui_simpler import combine_axis  # your axis math
except Exception:
    def combine_axis(coarse: int, fine: int) -> int:
        return int(coarse)

# IO
_io_import_err = None
try:
    import manual_io as io_mod
except Exception as e1:
    try:
        import maunal_io as io_mod
    except Exception as e2:
        _io_import_err = (e1, e2)
        io_mod = None

# Cameras (lines, ROI/rotate helpers, settings)
import stacks as camst


# ====================== Configuration ======================
class Config:
    # Camera sources (main stacks + aux “what’s happening”)
    CAM_MAIN_URL = getattr(camst, "STREAM_URL", 0)  # reuse if set in your stacks.py
    CAM_AUX_URL  = "rtsp://192.168.1.101:554/stream1"

    # Update rates
    IO_POLL_HZ       = 25      # fast scan for IO
    MOTOR_POLL_HZ    = 25      # fast scan for motor position
    CAMERA_SNAPSHOT_HZ = 1     # slow camera (1 snapshot per second)

    # IO defaults
    IO_HOST = getattr(io_mod, "HOST_DEFAULT", "192.168.1.12") if io_mod else "192.168.1.12"
    IO_UNIT = getattr(io_mod, "UNIT_DEFAULT", 1) if io_mod else 1

    # Motor defaults
    SERVO_PORT = "COM10"
    SERVO_BAUD = 38400
    SERVO_UNIT = 1

    # UI
    WINDOW = "1500x920"
    THEME = "clam"


# ====================== Threaded workers ======================
class CameraSnapshotter:
    """
    Low-latency snapshotter:
      - FFMPEG backend + low-latency flags for RTSP/HTTP streams
      - buffersize=1
      - per-cycle flush to keep the freshest frame
      - adjustable snapshot rate (set_rate) and flush window (set_flush)
    """
    def __init__(self, src, hz=1, flush_sec=0.25):
        self.src = src
        self.period = max(0.05, 1.0 / max(0.1, hz))
        self.flush_sec = max(0.05, float(flush_sec))
        self.cap = None
        self.last = None
        self.running = False
        self.lock = threading.Lock()
        self.thread = None

    def set_rate(self, hz: float):
        with self.lock:
            self.period = max(0.05, 1.0 / max(0.1, hz))

    def set_flush(self, seconds: float):
        with self.lock:
            self.flush_sec = max(0.05, float(seconds))

    def _open(self):
        # Prefer FFMPEG for URLs, default for webcams/ints
        backend = cv2.CAP_FFMPEG if isinstance(self.src, str) else 0
        # Apply low-latency flags for FFMPEG
        if backend == cv2.CAP_FFMPEG:
            # One long string: key1;val1|key2;val2|...
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
                "rtsp_transport;udp|fflags;nobuffer|flags;low_delay|"
                "max_delay;0|reorder_queue_size;0|max_interleave_delta;0|buffer_size;1024"
            )
        cap = cv2.VideoCapture(self.src, backend)
        # Keep only the latest frame in buffer (if backend supports it)
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        return cap

    def start(self):
        if self.running:
            return
        self.cap = self._open()
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=1.0)
        if self.cap:
            try:
                self.cap.release()
            except Exception:
                pass
        self.cap = None

    def _reopen_if_needed(self):
        if self.cap is None or not self.cap.isOpened():
            time.sleep(0.1)
            try:
                if self.cap:
                    self.cap.release()
            except Exception:
                pass
            self.cap = self._open()

    def _loop(self):
        while self.running:
            self._reopen_if_needed()
            if self.cap is None or not self.cap.isOpened():
                time.sleep(0.25)
                continue

            # snapshot settings (read under lock for live changes)
            with self.lock:
                flush_sec = self.flush_sec
                period = self.period

            # FLUSH for flush_sec to drop stale frames; keep the last good one
            t_end = time.time() + flush_sec
            last_ok = None
            while time.time() < t_end:
                ok, frame = self.cap.read()
                if ok:
                    last_ok = frame
                else:
                    time.sleep(0.005)

            if last_ok is not None:
                with self.lock:
                    self.last = last_ok

            # sleep the remainder to match the desired period
            sleep_for = max(0.0, period - flush_sec)
            time.sleep(sleep_for)

    def get(self) -> Optional[np.ndarray]:
        with self.lock:
            return None if self.last is None else self.last.copy()




class MotorWorker:
    """
    Wrap your Servo42DModbus with a fast poll for position/status.
    """
    def __init__(self, port, baud, unit, poll_hz=25):
        self.port = port
        self.baud = baud
        self.unit = unit
        self.poll_period = 1.0 / max(1.0, poll_hz)

        self.drv: Optional[Servo42DModbus] = None
        self.connected = False
        self.lock = threading.Lock()
        self.pos_counts = 0
        self.status_text = "Disconnected"

        self._run = False
        self._thread = None

    def connect(self):
        with self.lock:
            if self.connected:
                return
            self.drv = Servo42DModbus(port=self.port, baudrate=self.baud, unit=self.unit)
            try:
                # optional: set a reasonable mode
                try:
                    self.drv.set_work_mode(5)  # SR_vFOC (if supported)
                except Exception:
                    pass
                self.connected = True
                self.status_text = f"Connected {self.port}@{self.baud} unit={self.unit}"
                self._run = True
                self._thread = threading.Thread(target=self._poll_loop, daemon=True)
                self._thread.start()
            except Exception as e:
                self.drv = None
                self.connected = False
                self.status_text = f"Connect failed: {e}"
                raise

    def disconnect(self):
        with self.lock:
            self._run = False
        if self._thread:
            self._thread.join(timeout=1.0)
        with self.lock:
            if self.drv:
                try:
                    self.drv.close()
                except Exception:
                    pass
            self.drv = None
            self.connected = False
            self.status_text = "Disconnected"


    def read_position_now(self) -> int:
        """Immediate read for freshest position."""
        with self.lock:
            if not (self.connected and self.drv):
                return self.pos_counts
            try:
                c, v = self.drv.read_encoder()
                self.pos_counts = combine_axis(c, v)
            except Exception:
                pass
            return self.pos_counts

    def _poll_loop(self):
        while True:
            with self.lock:
                if not (self._run and self.connected and self.drv):
                    break
            try:
                c, v = self.drv.read_encoder()
                self.pos_counts = combine_axis(c, v)
            except Exception:
                # keep last; connection might be flaky
                pass
            time.sleep(self.poll_period)

    # motion commands
    def go_abs(self, target_counts: int, speed_rpm: int, acc: int):
        with self.lock:
            if not (self.connected and self.drv):
                return
            self.drv.go_to_position(axis_counts=int(target_counts),
                                    speed_rpm=int(speed_rpm),
                                    acc=int(acc))

    def move_to_abs_target(self, target_counts: int, speed_rpm: int, acc: int):
        """
        There is no absolute move in the drive → compute REL delta like ui_simpler:
        delta = target - current; then go_to_position(delta).
        """
        cur = self.read_position_now()
        delta = int(target_counts) - int(cur)
        with self.lock:
            if self.connected and self.drv:
                self.drv.go_to_position(axis_counts=delta, speed_rpm=int(speed_rpm), acc=int(acc))

    def go_rel_old(self, delta_counts: int, speed_rpm: int, acc: int):
        self.go_abs(self.pos_counts + int(delta_counts), speed_rpm, acc)

    def go_rel(self, delta_counts: int, speed_rpm: int, acc: int):
        """True relative jog: send delta directly."""
        with self.lock:
            if self.connected and self.drv:
                self.drv.go_to_position(axis_counts=int(delta_counts),
                                        speed_rpm=int(speed_rpm), acc=int(acc))

    def estop(self):
        with self.lock:
            if self.connected and self.drv:
                self.drv.estop()


class IOWorker:
    """
    Fast-scan IO using your MT3A client.
    """
    def __init__(self, host, unit, poll_hz=25):
        if io_mod is None:
            raise RuntimeError(f"IO module not importable: {(_io_import_err or '')}")
        self.host = host
        self.unit = unit
        self.poll_period = 1.0 / max(1.0, poll_hz)

        self.cli = io_mod.MT3AClient(host=host, unit=unit)
        self.connected = False
        self.DO = {}   # mirror
        self.DI = {}   # (optional, if you read inputs)
        self.status_text = "Disconnected"

        self._run = False
        self._thread = None
        self.lock = threading.Lock()

    def connect(self):
        self.cli.host = self.host
        self.cli.unit = self.unit
        self.cli.connect()
        self.connected = True
        self.status_text = f"Connected {self.host}:502 unit={self.unit}"
        self._run = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def disconnect(self):
        self._run = False
        if self._thread:
            self._thread.join(timeout=1.0)
        try:
            self.cli.close()
        except Exception:
            pass
        self.connected = False
        self.status_text = "Disconnected"

    def _poll_loop(self):
        while self._run and self.connected:
            try:
                # read back DO states if your API supports it
                # If not available, you can skip this and only mirror what we write.
                st = self.cli.read_do(count=16)
                with self.lock:
                    for i, v in enumerate(st):
                        self.DO[i] = bool(v)
                # (optional) read DI:
                try:
                    di_st = self.cli.read_di(count=16)
                    with self.lock:
                        for i, v in enumerate(di_st):
                            self.DI[i] = bool(v)
                except Exception:
                    pass
            except Exception:
                pass
            time.sleep(self.poll_period)

    def write_do(self, ch: int, val: bool):
        self.cli.write_do(ch, bool(val))
        with self.lock:
            self.DO[ch] = bool(val)

    def get_do(self, ch: int) -> bool:
        with self.lock:
            return bool(self.DO.get(ch, False))

    def get_di(self, ch: int) -> bool:
        with self.lock:
            return bool(self.DI.get(ch, False))


# ====================== Small helper for canvas mapping ======================
class DispMapper:
    def __init__(self):
        self.scale = 1.0
        self.xoff = 0
        self.yoff = 0
        self.W = 1
        self.H = 1

    def update(self, W, H, cw, ch):
        # guard: canvas might report 0 during layout
        cw = max(1, int(cw))
        ch = max(1, int(ch))
        self.W, self.H = max(1, int(W)), max(1, int(H))
        # avoid zero scale
        self.scale = max(1e-6, min(cw / float(self.W), ch / float(self.H)))
        nw, nh = int(self.W * self.scale), int(self.H * self.scale)
        self.xoff = (cw - nw) // 2
        self.yoff = (ch - nh) // 2

    def c2i(self, xc, yc):
        xi = (xc - self.xoff) / (self.scale if self.scale else 1.0)
        yi = (yc - self.yoff) / (self.scale if self.scale else 1.0)
        xi = int(max(0, min(self.W - 1, xi)))
        yi = int(max(0, min(self.H - 1, yi)))
        return xi, yi

    def i2c(self, xi, yi):
        xc = int(self.xoff + xi * self.scale)
        yc = int(self.yoff + yi * self.scale)
        return xc, yc


# ====================== UI Tabs ======================
class MotorTab(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.worker = MotorWorker(Config.SERVO_PORT, Config.SERVO_BAUD, Config.SERVO_UNIT,
                                  poll_hz=Config.MOTOR_POLL_HZ)

        # Connection row
        fr0 = ttk.LabelFrame(self, text="Connection"); fr0.pack(fill="x", padx=10, pady=8)
        ttk.Label(fr0, text="Port").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        self.e_port = ttk.Entry(fr0, width=12); self.e_port.insert(0, Config.SERVO_PORT); self.e_port.grid(row=0, column=1)
        ttk.Label(fr0, text="Baud").grid(row=0, column=2, padx=4, pady=4, sticky="e")
        self.e_baud = ttk.Entry(fr0, width=10); self.e_baud.insert(0, str(Config.SERVO_BAUD)); self.e_baud.grid(row=0, column=3)
        ttk.Label(fr0, text="Unit").grid(row=0, column=4, padx=4, pady=4, sticky="e")
        self.e_unit = ttk.Entry(fr0, width=6); self.e_unit.insert(0, str(Config.SERVO_UNIT)); self.e_unit.grid(row=0, column=5)
        ttk.Button(fr0, text="Connect", command=self.on_connect).grid(row=0, column=6, padx=6)
        ttk.Button(fr0, text="Disconnect", command=self.on_disconnect).grid(row=0, column=7, padx=6)

        # Motion row
        fr1 = ttk.LabelFrame(self, text="Motion"); fr1.pack(fill="x", padx=10, pady=8)
        ttk.Label(fr1, text="Speed (RPM)").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        self.e_speed = ttk.Entry(fr1, width=10); self.e_speed.insert(0, "300"); self.e_speed.grid(row=0, column=1)
        ttk.Label(fr1, text="Acc/Dec").grid(row=0, column=2, padx=4, pady=4, sticky="e")
        self.e_acc = ttk.Entry(fr1, width=10); self.e_acc.insert(0, "80"); self.e_acc.grid(row=0, column=3)
        ttk.Button(fr1, text="E-STOP", command=self.on_estop).grid(row=0, column=4, padx=8)

        # ABS targets
        fr2 = ttk.LabelFrame(self, text="Absolute targets (counts)"); fr2.pack(fill="x", padx=10, pady=8)
        self.abs_vars = []
        defaults = [0, 2000, 4000, 8000, 12000, 16000, 24000]
        for i, v in enumerate(defaults):
            sv = tk.StringVar(value=str(v)); self.abs_vars.append(sv)
            ttk.Entry(fr2, textvariable=sv, width=12).grid(row=i, column=0, padx=6, pady=3, sticky="w")
            ttk.Button(fr2, text=f"Go {i+1}", command=lambda idx=i: self.on_go_abs_idx(idx), width=10)\
               .grid(row=i, column=1, padx=6, pady=3)

        # REL
        fr3 = ttk.LabelFrame(self, text="Relative"); fr3.pack(fill="x", padx=10, pady=8)
        ttk.Label(fr3, text="Δ (counts)").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        self.e_rel = ttk.Entry(fr3, width=12); self.e_rel.insert(0, "0"); self.e_rel.grid(row=0, column=1)
        ttk.Button(fr3, text="Go REL", command=self.on_go_rel, width=10).grid(row=0, column=2, padx=6, pady=4)

        # Status
        self.lbl_pos = ttk.Label(self, text="Pos: —", font=("Consolas", 16)); self.lbl_pos.pack(anchor="w", padx=12, pady=(6,4))
        self.svar = tk.StringVar(value="Disconnected"); ttk.Label(self, textvariable=self.svar).pack(anchor="w", padx=12, pady=(0,8))

        self.after(50, self._tick)

    def on_connect(self):
        try:
            self.worker.port = self.e_port.get().strip()
            self.worker.baud = int(self.e_baud.get().strip())
            self.worker.unit = int(self.e_unit.get().strip())
            self.worker.connect()
            self.svar.set(self.worker.status_text)
        except Exception as e:
            messagebox.showerror("Motor connect", str(e))
            self.svar.set(str(e))

    def on_disconnect(self):
        self.worker.disconnect()
        self.svar.set(self.worker.status_text)

    def on_estop(self):
        self.worker.estop()
        self.svar.set("E-STOP sent")

    def _get_motion(self):
        return int(self.e_speed.get().strip()), int(self.e_acc.get().strip())

    def on_go_abs_idx(self, idx: int):
        if not self.worker.connected:
            return
        try:
            tgt = int(self.abs_vars[idx].get().strip())
            spd = int(self.e_speed.get().strip())
            acc = int(self.e_acc.get().strip())
            self.worker.move_to_abs_target(tgt, spd, acc)
            self.svar.set(f"ABS→REL Δ to {tgt} @ {spd}rpm acc={acc}")
        except Exception as e:
            messagebox.showerror("ABS move", str(e))

    def on_go_rel(self):
        if not self.worker.connected:
            return
        try:
            delta = int(self.e_rel.get().strip())
            spd = int(self.e_speed.get().strip())
            acc = int(self.e_acc.get().strip())
            self.worker.go_rel(delta, spd, acc)
            self.svar.set(f"REL {delta:+} @ {spd}rpm acc={acc}")
        except Exception as e:
            messagebox.showerror("REL move", str(e))

    def _tick(self):
        if self.worker.connected:
            self.lbl_pos.config(text=f"Pos: {self.worker.pos_counts}")
        self.after(int(1000 / max(1, Config.MOTOR_POLL_HZ)), self._tick)


class IOAndStacksTab(ttk.Frame):
    def __init__(self, master, io_worker: IOWorker, percentages_ref: dict, main_snapper: CameraSnapshotter):
        super().__init__(master)
        self.io = io_worker
        self.perc_ref = percentages_ref  # {"pcts":[...]}
        self.snapper = main_snapper

        # Left: IO groups
        left = ttk.Frame(self); left.pack(side="left", fill="y", padx=10, pady=10)
        ttk.Label(left, text="Cylinders", font=("Segoe UI", 12, "bold")).pack(anchor="w")

        self.btns = {}
        def add_group(title, mapping):
            lf = ttk.LabelFrame(left, text=title, padding=6); lf.pack(anchor="w", fill="x", pady=6)
            for ch, name in mapping:
                row = ttk.Frame(lf); row.pack(anchor="w")
                ttk.Label(row, text=f"DO{ch:02d} – {name}", width=30).pack(side="left")
                btn = ttk.Checkbutton(row, command=lambda i=ch: self._toggle(i))
                btn.state(['!alternate'])
                btn.pack(side="left", padx=8)
                self.btns[ch] = btn

        if io_mod:
            add_group("Station shaft assembly", io_mod.SHAFT_ASSEMBLY)
            add_group("General station", io_mod.GENERAL_STATION)
            add_group("Station stacks", io_mod.STATION_STACKS)
            if hasattr(io_mod, "TORQUE_STATION"):
                add_group("Torque station", io_mod.TORQUE_STATION)

        ttk.Separator(left, orient="horizontal").pack(fill="x", pady=8)
        ttk.Label(left, text="Stacks %", font=("Segoe UI", 12, "bold")).pack(anchor="w")

        self.pbars = []
        for i in range(6):
            row = ttk.Frame(left); row.pack(anchor="w", fill="x", pady=2)
            ttk.Label(row, text=f"S{i+1}", width=4).pack(side="left")
            bar = ttk.Progressbar(row, length=220, mode="determinate", maximum=100); bar.pack(side="left", padx=6)
            lab = ttk.Label(row, text="0 %", width=6); lab.pack(side="left")
            self.pbars.append((bar, lab))

        # Right: Main camera (no overlays, slow snapshot)
        right = ttk.Frame(self); right.pack(side="left", fill="both", expand=True, padx=10, pady=10)
        ttk.Label(right, text="Main Camera (live, 1 Hz)", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        self.canvas = tk.Canvas(right, background="#111")
        self.canvas.pack(fill="both", expand=True, pady=(6,0))
        self._imgtk = None

        self.status = tk.StringVar(value=self.io.status_text)
        ttk.Label(self, textvariable=self.status, anchor="w", padding=6).pack(fill="x")

        self.after(40, self._tick)

    def _toggle(self, ch: int):
        if not self.io.connected: return
        # password for setup DO12 if present
        if io_mod and ch == 12:
            from tkinter import simpledialog
            pwd = simpledialog.askstring("Authorization", "Enter password for Setup cylinder:", show="*")
            if pwd != getattr(io_mod, "SETUP_PASSWORD", "2468"):
                btn = self.btns.get(ch)
                if btn: btn.state(['!selected'])
                messagebox.showerror("Unauthorized", "Wrong password.")
                return
        try:
            val = 'selected' in self.btns[ch].state()
            self.io.write_do(ch, val)
            self.status.set(f"Wrote DO{ch:02d} = {int(val)}")
        except Exception as e:
            messagebox.showerror("IO write", str(e))

    def _tick(self):
        # update DO mirror in UI
        for ch, btn in self.btns.items():
            cur = self.io.get_do(ch)
            if cur: btn.state(['selected'])
            else:   btn.state(['!selected'])

        # update percentages
        pcts = self.perc_ref.get("pcts", [0]*6)
        for i in range(6):
            v = int(np.clip(round((pcts[i] if i < len(pcts) else 0)*100), 0, 100))
            self.pbars[i][0]["value"] = v
            self.pbars[i][1]["text"] = f"{v:3d} %"

        # draw camera snapshot (1 Hz handled by worker; we just display last)
        frame = self.snapper.get()
        if frame is not None:
            disp = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w = disp.shape[:2]
            cw = self.canvas.winfo_width() or w
            ch = self.canvas.winfo_height() or h
            scale = min(cw / w, ch / h)
            nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
            disp = cv2.resize(disp, (nw, nh), interpolation=cv2.INTER_AREA)
            im = Image.fromarray(disp)
            self._imgtk = ImageTk.PhotoImage(im)
            self.canvas.delete("all")
            self.canvas.create_image(cw//2, ch//2, image=self._imgtk, anchor="center")

        self.status.set(self.io.status_text)
        self.after(int(1000 / max(1, Config.IO_POLL_HZ)), self._tick)


class StacksViewTab(ttk.Frame):
    """
    Slow 1 Hz camera, but accurate line selection & percent calc.
    Percentages pushed to shared ref: {"pcts":[...]}.
    """
    def __init__(self, master, snapper: CameraSnapshotter, perc_ref: dict):
        super().__init__(master)
        self.snapper = snapper
        self.perc_ref = perc_ref

        # Settings from your stacks.py
        self.cfg = camst.load_settings()
        self.editor = camst.Editor()
        self.editor.lines_norm = self.cfg.get("lines_norm", [])
        self.rotate_ccw = bool(self.cfg.get("rotate_ccw90", True))
        self.roi = self.cfg.get("roi")
        self.mapper = DispMapper()
        self._imgtk = None
        self._disp_shape = (0, 0)

        # UI
        tb = ttk.Frame(self); tb.pack(fill="x", padx=8, pady=6)
        ttk.Button(tb, text="Edit lines (E)", command=self._toggle_edit).pack(side="left")
        ttk.Button(tb, text="Rotate 90° CCW (R)", command=self._toggle_rotate).pack(side="left", padx=6)
        ttk.Button(tb, text="Crop ROI (C)", command=self._select_roi).pack(side="left", padx=6)
        ttk.Button(tb, text="Save settings (S)", command=self._save).pack(side="left", padx=6)
        self.canvas = tk.Canvas(self, background="#111"); self.canvas.pack(fill="both", expand=True, padx=8, pady=8)

        # keybinds
        self.bind_all("<Key-e>", lambda e: self._toggle_edit())
        self.bind_all("<Key-r>", lambda e: self._toggle_rotate())
        self.bind_all("<Key-s>", lambda e: self._save())
        self.bind_all("<Key-c>", lambda e: self._select_roi())
        self.canvas.bind("<Button-1>", self._on_left)
        self.canvas.bind("<Button-3>", self._on_right)

        self.after(250, self._tick)

    def _toggle_edit(self): self.editor.edit_mode = not self.editor.edit_mode; self.editor.pending = []
    def _toggle_rotate(self): self.rotate_ccw = not self.rotate_ccw

    def _select_roi(self):
        fr = self.snapper.get()
        if fr is None:
            messagebox.showinfo("Camera", "No frame yet.")
            return
        roi = camst.select_roi_interactive(fr, self.roi)
        if roi is not None:
            self.roi = [int(v) for v in roi]

    def _save(self):
        self.cfg["rotate_ccw90"] = bool(self.rotate_ccw)
        self.cfg["roi"] = self.roi
        self.cfg["lines_norm"] = self.editor.lines_norm
        camst.save_settings(self.cfg)

    def _compute_percentages(self, view, lines_px, hits):
        out = []
        for (x0,y0,x1,y1), hit in zip(lines_px, hits):
            if hit is None:
                out.append(0.0); continue
            (xh, yh), idx, grad = hit
            xb, yb = (x1,y1) if y1 >= y0 else (x0,y0)
            dist_from_bottom = float(np.hypot(xb - xh, yb - yh))
            total_len = float(np.hypot(x1 - x0, y1 - y0))
            out.append( float(np.clip(dist_from_bottom / max(1.0, total_len), 0.0, 1.0)) )
        if len(out) < 6: out += [0.0]*(6-len(out))
        return out[:6]

    def _on_left(self, e):
        if not self.editor.edit_mode: return
        if self._disp_shape == (0,0): return
        xi, yi = self.mapper.c2i(e.x, e.y)
        # Keep pending for visual feedback in canvas coords
        self.editor.pending.append((e.x, e.y))
        if len(self.editor.pending) == 2:
            (cx0, cy0), (cx1, cy1) = self.editor.pending
            x0i, y0i = self.mapper.c2i(cx0, cy0)
            x1i, y1i = self.mapper.c2i(cx1, cy1)
            W, H = self.mapper.W, self.mapper.H
            self.editor.lines_norm.append(camst.norm_line((x0i, y0i, x1i, y1i), W, H))
            self.editor.lines_norm = self.editor.lines_norm[:camst.MAX_LINES]
            self.editor.pending = []

    def _on_right(self, e):
        if not self.editor.edit_mode: return
        if self.editor.pending: self.editor.pending = []
        elif self.editor.lines_norm: self.editor.lines_norm.pop()

    def _tick(self):
        frame = self.snapper.get()
        if frame is None:
            self.after(200, self._tick); return

        view = camst.get_cropped_rotated(frame, self.roi, self.rotate_ccw)
        H, W = view.shape[:2]
        self._disp_shape = (H, W)
        lines_px = [camst.denorm_line(ln, W, H) for ln in self.editor.lines_norm]

        hits = []
        if lines_px:
            gray = cv2.cvtColor(view, cv2.COLOR_BGR2GRAY)
            for (x0,y0,x1,y1) in lines_px:
                x0,y0,x1,y1 = camst.order_top_to_bottom(x0,y0,x1,y1)
                prof, pts = camst.sample_line_profile(gray, x0,y0,x1,y1)
                idx, val, grad = camst.strongest_gradient_idx(prof)
                xh, yh = pts[idx]
                hits.append(((xh, yh), idx, grad))

        pcts = self._compute_percentages(view, lines_px, hits)
        self.perc_ref["pcts"] = pcts

        overlay = camst.draw_lines_and_hits(view.copy(), lines_px, hits)
        rgb = cv2.cvtColor(overlay, cv2.COLOR_BGR2RGB)

        cw = self.canvas.winfo_width() or W
        ch = self.canvas.winfo_height() or H
        self.mapper.update(W, H, cw, ch)

        nw = max(1, int(round(W * self.mapper.scale)))
        nh = max(1, int(round(H * self.mapper.scale)))
        rgb_s = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_AREA)

        im = Image.fromarray(rgb_s)
        self._imgtk = ImageTk.PhotoImage(im)

        self.canvas.delete("all")
        self.canvas.create_rectangle(0,0,cw,ch,fill="#111", outline="")
        self.canvas.create_image(self.mapper.xoff, self.mapper.yoff, image=self._imgtk, anchor="nw")

        if self.editor.edit_mode and self.editor.pending:
            for (cx, cy) in self.editor.pending:
                self.canvas.create_oval(cx-4, cy-4, cx+4, cy+4, fill="#f00", outline="")

        # update ~4 Hz UI; camera frames themselves refresh at 1 Hz
        self.after(250, self._tick)


class AuxCamTab(ttk.Frame):
    def __init__(self, master, snapper: CameraSnapshotter):
        super().__init__(master)
        ttk.Label(self, text="Aux Camera (1 Hz)", font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=8, pady=(8,0))
        self.canvas = tk.Canvas(self, background="#111"); self.canvas.pack(fill="both", expand=True, padx=8, pady=8)
        self.snapper = snapper
        self._imgtk = None
        self.after(500, self._tick)

    def _tick(self):
        fr = self.snapper.get()
        if fr is not None:
            disp = cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)
            h, w = disp.shape[:2]
            cw = self.canvas.winfo_width() or w
            ch = self.canvas.winfo_height() or h
            scale = min(cw / w, ch / h)
            nw, nh = max(1, int(w*scale)), max(1, int(h*scale))
            disp = cv2.resize(disp, (nw, nh), interpolation=cv2.INTER_AREA)
            im = Image.fromarray(disp)
            self._imgtk = ImageTk.PhotoImage(im)
            self.canvas.delete("all")
            self.canvas.create_image(cw//2, ch//2, image=self._imgtk, anchor="center")
        self.after(1000, self._tick)


# ====================== Main App ======================
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Stacks Station – Fast IO/Motor, Slow Cameras")
        try:
            style = ttk.Style(self)
            style.theme_use(Config.THEME)
        except Exception:
            pass
        self.geometry(Config.WINDOW)

        # Workers
        # IO
        if io_mod is None:
            raise RuntimeError("IO module not found (manual_io/maunal_io).")
        self.iow = IOWorker(Config.IO_HOST, Config.IO_UNIT, poll_hz=Config.IO_POLL_HZ)
        try:
            self.iow.connect()
        except Exception as e:
            messagebox.showwarning("IO connect", f"{e}")

        # Motor: connect on demand via tab
        # Cameras (1 Hz)
        self.cam_main = CameraSnapshotter(Config.CAM_MAIN_URL, hz=Config.CAMERA_SNAPSHOT_HZ); self.cam_main.start()
        self.cam_aux  = CameraSnapshotter(Config.CAM_AUX_URL, hz=Config.CAMERA_SNAPSHOT_HZ);  self.cam_aux.start()

        self.shared_pcts = {"pcts": [0]*6}

        nb = ttk.Notebook(self); nb.pack(fill="both", expand=True)

        self.tab_motor = MotorTab(nb)
        self.tab_io    = IOAndStacksTab(nb, self.iow, self.shared_pcts, self.cam_main)
        self.tab_view  = StacksViewTab(nb, self.cam_main, self.shared_pcts)
        self.tab_aux   = AuxCamTab(nb, self.cam_aux)

        nb.add(self.tab_motor, text="Motor (MKS)")
        nb.add(self.tab_io,    text="IO & Stacks %")
        nb.add(self.tab_view,  text="Stacks View (lines)")
        nb.add(self.tab_aux,   text="Aux Camera (192.168.1.101)")

        # ... after creating nb and adding tabs:
        def _on_tab_changed(event):
            tab_text = nb.tab(nb.select(), "text")
            if tab_text.startswith("Stacks View"):
                # fast & fresh while editing/observing
                self.cam_main.set_rate(10)  # 10 Hz when on the tab
                self.cam_main.set_flush(0.5)  # flush a bit longer to ensure newest
            else:
                # back to light load elsewhere
                self.cam_main.set_rate(Config.CAMERA_SNAPSHOT_HZ)
                self.cam_main.set_flush(0.25)

        nb.bind("<<NotebookTabChanged>>", _on_tab_changed)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        try: self.cam_main.stop()
        except Exception: pass
        try: self.cam_aux.stop()
        except Exception: pass
        try: self.iow.disconnect()
        except Exception: pass
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
