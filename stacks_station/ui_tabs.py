import tkinter as tk
from tkinter import ttk, messagebox

import numpy as np
import cv2
from PIL import Image, ImageTk

from display_mapper import DispMapper
from io_worker import get_do_list
from positions_store import PositionsStore

# use your camera+lines helpers
import stacks as camst

# ... keep the previous content (MotorTab, IOAndStacksTab, StacksViewTab, AuxCamTab) ...

from settings_store import SettingsStore

class SetupTab(ttk.Frame):
    """
    Edit motion settings used by the phone web UI (Table Main buttons).
    """
    def __init__(self, master, settings: SettingsStore):
        super().__init__(master)
        self.s = settings

        lf = ttk.LabelFrame(self, text="Table station distances (counts)", padding=10)
        lf.pack(fill="x", padx=10, pady=10)

        self.var_half = tk.StringVar(value=str(self.s.get("half_step_counts", 5000)))
        self.var_full = tk.StringVar(value=str(self.s.get("full_step_counts", 10000)))
        self.var_speed = tk.StringVar(value=str(self.s.get("speed_rpm", 300)))
        self.var_acc = tk.StringVar(value=str(self.s.get("acc", 80)))
        self.var_slow_rpm = tk.StringVar(value=str(self.s.get("slow_jog_rpm", 10)))
        self.var_slow_step = tk.StringVar(value=str(self.s.get("slow_jog_step", 200)))

        def row(parent, r, label, var, unit_txt=""):
            ttk.Label(parent, text=label, width=24).grid(row=r, column=0, sticky="e", padx=6, pady=4)
            ttk.Entry(parent, textvariable=var, width=12).grid(row=r, column=1, padx=6, pady=4, sticky="w")
            ttk.Label(parent, text=unit_txt).grid(row=r, column=2, sticky="w")

        row(lf, 0, "Half-station distance", self.var_half, "counts")
        row(lf, 1, "Full-station distance", self.var_full, "counts")
        row(lf, 2, "Default speed (half/full)", self.var_speed, "RPM")
        row(lf, 3, "Default accel", self.var_acc, "")
        ttk.Separator(self).pack(fill="x", padx=10, pady=6)

        lf2 = ttk.LabelFrame(self, text="Slow jog", padding=10)
        lf2.pack(fill="x", padx=10, pady=10)
        row(lf2, 0, "Slow jog speed", self.var_slow_rpm, "RPM")
        row(lf2, 1, "Slow jog step", self.var_slow_step, "counts")

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(btns, text="Save", command=self._on_save).pack(side="left")
        ttk.Button(btns, text="Reload", command=self._on_reload).pack(side="left", padx=8)
        self.msg = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.msg).pack(anchor="w", padx=12)

    def _on_save(self):
        try:
            self.s.set("half_step_counts", int(self.var_half.get().strip()))
            self.s.set("full_step_counts", int(self.var_full.get().strip()))
            self.s.set("speed_rpm", int(self.var_speed.get().strip()))
            self.s.set("acc", int(self.var_acc.get().strip()))
            self.s.set("slow_jog_rpm", int(self.var_slow_rpm.get().strip()))
            self.s.set("slow_jog_step", int(self.var_slow_step.get().strip()))
            ok = self.s.save()
            self.msg.set("Saved." if ok else "Save failed.")
        except Exception as e:
            self.msg.set(str(e))

    def _on_reload(self):
        self.s.load()
        self.var_half.set(str(self.s.get("half_step_counts")))
        self.var_full.set(str(self.s.get("full_step_counts")))
        self.var_speed.set(str(self.s.get("speed_rpm")))
        self.var_acc.set(str(self.s.get("acc")))
        self.var_slow_rpm.set(str(self.s.get("slow_jog_rpm")))
        self.var_slow_step.set(str(self.s.get("slow_jog_step")))
        self.msg.set("Reloaded.")

class MotorTab(ttk.Frame):
    def __init__(self, master, motor_worker):
        super().__init__(master)
        self.worker = motor_worker
        self.store = PositionsStore()  # load/save positions on disk

        # ----- Connection -----
        fr0 = ttk.LabelFrame(self, text="Connection")
        fr0.pack(fill="x", padx=10, pady=8)
        ttk.Label(fr0, text="Port").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        self.e_port = ttk.Entry(fr0, width=12)
        self.e_port.insert(0, self.worker.port)
        self.e_port.grid(row=0, column=1)
        ttk.Label(fr0, text="Baud").grid(row=0, column=2, padx=4, pady=4, sticky="e")
        self.e_baud = ttk.Entry(fr0, width=10)
        self.e_baud.insert(0, str(self.worker.baud))
        self.e_baud.grid(row=0, column=3)
        ttk.Label(fr0, text="Unit").grid(row=0, column=4, padx=4, pady=4, sticky="e")
        self.e_unit = ttk.Entry(fr0, width=6)
        self.e_unit.insert(0, str(self.worker.unit))
        self.e_unit.grid(row=0, column=5)
        ttk.Button(fr0, text="Connect", command=self.on_connect).grid(row=0, column=6, padx=6)
        ttk.Button(fr0, text="Disconnect", command=self.on_disconnect).grid(row=0, column=7, padx=6)

        # ----- Motion -----
        fr1 = ttk.LabelFrame(self, text="Motion")
        fr1.pack(fill="x", padx=10, pady=8)
        ttk.Label(fr1, text="Speed (RPM)").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        self.e_speed = ttk.Entry(fr1, width=10)
        self.e_speed.insert(0, "300")
        self.e_speed.grid(row=0, column=1)
        ttk.Label(fr1, text="Acc/Dec").grid(row=0, column=2, padx=4, pady=4, sticky="e")
        self.e_acc = ttk.Entry(fr1, width=10)
        self.e_acc.insert(0, "80")
        self.e_acc.grid(row=0, column=3)
        ttk.Button(fr1, text="E-STOP", command=self.on_estop).grid(row=0, column=4, padx=8)

        # ----- Absolute targets quick -----
        fr2 = ttk.LabelFrame(self, text="Absolute targets (counts)")
        fr2.pack(fill="x", padx=10, pady=8)
        self.abs_vars = []
        defaults = [0, 2000, 4000, 8000, 12000, 16000, 24000]
        for i, v in enumerate(defaults):
            sv = tk.StringVar(value=str(v))
            self.abs_vars.append(sv)
            ttk.Entry(fr2, textvariable=sv, width=12).grid(row=i, column=0, padx=6, pady=3, sticky="w")
            ttk.Button(fr2, text=f"Go {i+1}", command=lambda idx=i: self.on_go_abs_idx(idx), width=10).grid(row=i, column=1, padx=6, pady=3)

        # ----- Relative -----
        fr3 = ttk.LabelFrame(self, text="Relative")
        fr3.pack(fill="x", padx=10, pady=8)
        ttk.Label(fr3, text="Δ (counts)").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        self.e_rel = ttk.Entry(fr3, width=12)
        self.e_rel.insert(0, "0")
        self.e_rel.grid(row=0, column=1)
        ttk.Button(fr3, text="Go REL", command=self.on_go_rel, width=10).grid(row=0, column=2, padx=6, pady=4)

        # ----- Saved positions (persist on disk) -----
        fr4 = ttk.LabelFrame(self, text="Saved positions (disk)")
        fr4.pack(fill="x", padx=10, pady=8)

        self.slot_names = ["Table", "1", "2", "3", "4", "5", "6"]
        self.slot_vars = {}
        for r, name in enumerate(self.slot_names):
            tk.Label(fr4, text=f"{name:>5}:", width=6, anchor="e").grid(row=r, column=0, padx=4, pady=2)
            sv = tk.StringVar(value=str(self.store.get(name, 0)))
            self.slot_vars[name] = sv
            ttk.Entry(fr4, textvariable=sv, width=14).grid(row=r, column=1, padx=4, pady=2)
            ttk.Button(fr4, text="Set = Here", command=lambda n=name: self.on_slot_set_here(n), width=10)\
                .grid(row=r, column=2, padx=4, pady=2)
            ttk.Button(fr4, text="Go", command=lambda n=name: self.on_slot_go(n), width=6)\
                .grid(row=r, column=3, padx=4, pady=2)

        btns = ttk.Frame(fr4)
        btns.grid(row=len(self.slot_names), column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Button(btns, text="Save to disk", command=self.on_slots_save).pack(side="left", padx=4)
        ttk.Button(btns, text="Reload from disk", command=self.on_slots_reload).pack(side="left", padx=4)

        self.lbl_pos = ttk.Label(self, text="Pos: —", font=("Consolas", 16))
        self.lbl_pos.pack(anchor="w", padx=12, pady=(6, 4))
        self.svar = tk.StringVar(value="Disconnected")
        ttk.Label(self, textvariable=self.svar).pack(anchor="w", padx=12, pady=(0, 8))

        self.after(50, self._tick)

    # ---- connection & motion handlers ----
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

    # ---- saved positions handlers ----
    def on_slot_set_here(self, name: str):
        pos = self.worker.read_position_now()
        self.slot_vars[name].set(str(pos))

    def on_slot_go(self, name: str):
        if not self.worker.connected:
            return
        try:
            tgt = int(self.slot_vars[name].get().strip())
            spd = int(self.e_speed.get().strip())
            acc = int(self.e_acc.get().strip())
            self.worker.move_to_abs_target(tgt, spd, acc)
            self.svar.set(f"GO {name} ({tgt})")
        except Exception as e:
            messagebox.showerror("Go slot", str(e))

    def on_slots_save(self):
        try:
            for n in self.slot_names:
                self.store.set(n, int(self.slot_vars[n].get().strip()))
            ok = self.store.save()
            self.svar.set("Positions saved" if ok else "Save failed")
        except Exception as e:
            messagebox.showerror("Save positions", str(e))

    def on_slots_reload(self):
        self.store.load()
        for n in self.slot_names:
            self.slot_vars[n].set(str(self.store.get(n, 0)))
        self.svar.set("Positions reloaded")

    def _tick(self):
        if self.worker.connected:
            self.lbl_pos.config(text=f"Pos: {self.worker.pos_counts}")
        self.after(40, self._tick)


class IOAndStacksTab(ttk.Frame):
    def __init__(self, master, io_worker, perc_ref, main_snapper):
        super().__init__(master)
        self.io = io_worker
        self.perc_ref = perc_ref
        self.snapper = main_snapper

        left = ttk.Frame(self)
        left.pack(side="left", fill="y", padx=10, pady=10)
        ttk.Label(left, text="Cylinders", font=("Segoe UI", 12, "bold")).pack(anchor="w")

        self.btns = {}

        def add_group(title, mapping):
            lf = ttk.LabelFrame(left, text=title, padding=6)
            lf.pack(anchor="w", fill="x", pady=6)
            for ch, name in mapping:
                row = ttk.Frame(lf)
                row.pack(anchor="w")
                ttk.Label(row, text=f"DO{ch:02d} – {name}", width=30).pack(side="left")
                btn = ttk.Checkbutton(row, command=lambda i=ch: self._toggle(i))
                btn.state(["!alternate"])
                btn.pack(side="left", padx=8)
                self.btns[ch] = btn

        do_list = get_do_list()
        add_group("All cylinders", do_list)

        ttk.Separator(left, orient="horizontal").pack(fill="x", pady=8)
        ttk.Label(left, text="Stacks %", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        self.pbars = []
        for i in range(6):
            row = ttk.Frame(left)
            row.pack(anchor="w", fill="x", pady=2)
            ttk.Label(row, text=f"S{i+1}", width=4).pack(side="left")
            bar = ttk.Progressbar(row, length=220, mode="determinate", maximum=100)
            bar.pack(side="left", padx=6)
            lab = ttk.Label(row, text="0 %", width=6)
            lab.pack(side="left")
            self.pbars.append((bar, lab))

        right = ttk.Frame(self)
        right.pack(side="left", fill="both", expand=True, padx=10, pady=10)
        ttk.Label(right, text="Main Camera (1 Hz)", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        self.canvas = tk.Canvas(right, background="#111")
        self.canvas.pack(fill="both", expand=True, pady=(6, 0))
        self._imgtk = None

        self.status = tk.StringVar(value=self.io.status_text)
        ttk.Label(self, textvariable=self.status, anchor="w", padding=6).pack(fill="x")
        self.after(40, self._tick)

    def _toggle(self, ch: int):
        if not self.io.connected:
            return
        try:
            val = "selected" in self.btns[ch].state()
            self.io.write_do(ch, val)
            self.status.set(f"Wrote DO{ch:02d} = {int(val)}")
        except Exception as e:
            messagebox.showerror("IO write", str(e))

    def _tick(self):
        for ch, btn in self.btns.items():
            cur = self.io.get_do(ch)
            btn.state(["selected"] if cur else ["!selected"])

        pcts = self.perc_ref.get("pcts", [0] * 6)
        for i in range(6):
            v = int(np.clip(round((pcts[i] if i < len(pcts) else 0) * 100), 0, 100))
            self.pbars[i][0]["value"] = v
            self.pbars[i][1]["text"] = f"{v:3d} %"

        frame = self.snapper.get()
        if frame is not None:
            disp = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w = disp.shape[:2]
            cw = max(1, self.canvas.winfo_width() or w)
            ch = max(1, self.canvas.winfo_height() or h)
            scale = min(cw / w, ch / h)
            nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
            disp = cv2.resize(disp, (nw, nh), interpolation=cv2.INTER_AREA)
            im = Image.fromarray(disp)
            self._imgtk = ImageTk.PhotoImage(im)
            self.canvas.delete("all")
            self.canvas.create_image(cw // 2, ch // 2, image=self._imgtk, anchor="center")

        self.status.set(self.io.status_text)
        self.after(40, self._tick)


class StacksViewTab(ttk.Frame):
    def __init__(self, master, snapper, perc_ref):
        super().__init__(master)
        self.snapper = snapper
        self.perc_ref = perc_ref

        self.cfg = camst.load_settings()
        self.editor = camst.Editor()
        self.editor.lines_norm = self.cfg.get("lines_norm", [])
        self.rotate_ccw = bool(self.cfg.get("rotate_ccw90", True))
        self.roi = self.cfg.get("roi")
        self.mapper = DispMapper()
        self._imgtk = None
        self._disp_shape = (0, 0)

        tb = ttk.Frame(self)
        tb.pack(fill="x", padx=8, pady=6)
        ttk.Button(tb, text="Edit lines (E)", command=self._toggle_edit).pack(side="left")
        ttk.Button(tb, text="Rotate 90° CCW (R)", command=self._toggle_rotate).pack(side="left", padx=6)
        ttk.Button(tb, text="Crop ROI (C)", command=self._select_roi).pack(side="left", padx=6)
        ttk.Button(tb, text="Save settings (S)", command=self._save).pack(side="left", padx=6)
        self.canvas = tk.Canvas(self, background="#111")
        self.canvas.pack(fill="both", expand=True, padx=8, pady=8)

        self.bind_all("<Key-e>", lambda e: self._toggle_edit())
        self.bind_all("<Key-r>", lambda e: self._toggle_rotate())
        self.bind_all("<Key-s>", lambda e: self._save())
        self.bind_all("<Key-c>", lambda e: self._select_roi())
        self.canvas.bind("<Button-1>", self._on_left)
        self.canvas.bind("<Button-3>", self._on_right)

        self.after(250, self._tick)

    def _toggle_edit(self):
        self.editor.edit_mode = not self.editor.edit_mode
        self.editor.pending = []

    def _toggle_rotate(self):
        self.rotate_ccw = not self.rotate_ccw

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
        for (x0, y0, x1, y1), hit in zip(lines_px, hits):
            if hit is None:
                out.append(0.0)
                continue
            (xh, yh), idx, grad = hit
            xb, yb = (x1, y1) if y1 >= y0 else (x0, y0)
            dist_from_bottom = float(((xb - xh) ** 2 + (yb - yh) ** 2) ** 0.5)
            total_len = float(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5)
            out.append(float(max(0.0, min(1.0, dist_from_bottom / max(1.0, total_len)))))
        if len(out) < 6:
            out += [0.0] * (6 - len(out))
        return out[:6]

    def _on_left(self, e):
        if not self.editor.edit_mode or self._disp_shape == (0, 0):
            return
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
        if not self.editor.edit_mode:
            return
        if self.editor.pending:
            self.editor.pending = []
        elif self.editor.lines_norm:
            self.editor.lines_norm.pop()

    def _tick(self):
        frame = self.snapper.get()
        if frame is None:
            self.after(200, self._tick)
            return

        view = camst.get_cropped_rotated(frame, self.roi, self.rotate_ccw)
        H, W = view.shape[:2]
        self._disp_shape = (H, W)
        lines_px = [camst.denorm_line(ln, W, H) for ln in self.editor.lines_norm]

        hits = []
        if lines_px:
            gray = cv2.cvtColor(view, cv2.COLOR_BGR2GRAY)
            for (x0, y0, x1, y1) in lines_px:
                x0, y0, x1, y1 = camst.order_top_to_bottom(x0, y0, x1, y1)
                prof, pts = camst.sample_line_profile(gray, x0, y0, x1, y1)
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
        self.canvas.create_rectangle(0, 0, cw, ch, fill="#111", outline="")
        self.canvas.create_image(self.mapper.xoff, self.mapper.yoff, image=self._imgtk, anchor="nw")

        if self.editor.edit_mode and self.editor.pending:
            for (cx, cy) in self.editor.pending:
                self.canvas.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, fill="#f00", outline="")

        self.after(250, self._tick)


class AuxCamTab(ttk.Frame):
    def __init__(self, master, snapper):
        super().__init__(master)
        ttk.Label(self, text="Aux Camera (1 Hz)", font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=8, pady=(8, 0))
        self.canvas = tk.Canvas(self, background="#111")
        self.canvas.pack(fill="both", expand=True, padx=8, pady=8)
        self.snapper = snapper
        self._imgtk = None
        self.after(500, self._tick)

    def _tick(self):
        fr = self.snapper.get()
        if fr is not None:
            disp = cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)
            h, w = disp.shape[:2]
            cw = max(1, self.canvas.winfo_width() or w)
            ch = max(1, self.canvas.winfo_height() or h)
            scale = min(cw / w, ch / h)
            nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
            disp = cv2.resize(disp, (nw, nh), interpolation=cv2.INTER_AREA)
            im = Image.fromarray(disp)
            self._imgtk = ImageTk.PhotoImage(im)
            self.canvas.delete("all")
            self.canvas.create_image(cw // 2, ch // 2, image=self._imgtk, anchor="center")
        self.after(1000, self._tick)


class ShaftAssemblyTab(ttk.Frame):
    """
    Shaft assembly control tab with:
    - Live position reading from COM15 (Modbus 0x04)
    - Cylinder controls DO00 (down) and DO01 (up) with momentary buttons
    - Automatic "Go to position" feature
    """
    
    def __init__(self, master, shaft_worker, io_worker):
        super().__init__(master)
        self.shaft = shaft_worker
        self.io = io_worker
        
        # State for go-to-position
        self._going_to_position = False
        self._target_position = 0
        self._go_thread = None
        
        # ----- Connection -----
        frm_conn = ttk.LabelFrame(self, text="Connection (COM15)")
        frm_conn.pack(fill="x", padx=10, pady=8)
        
        ttk.Label(frm_conn, text="Port").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        self.ent_port = ttk.Entry(frm_conn, width=12)
        self.ent_port.insert(0, self.shaft.port)
        self.ent_port.grid(row=0, column=1, padx=4, pady=4)
        
        ttk.Label(frm_conn, text="Baud").grid(row=0, column=2, padx=4, pady=4, sticky="e")
        self.ent_baud = ttk.Entry(frm_conn, width=8)
        self.ent_baud.insert(0, str(self.shaft.baudrate))
        self.ent_baud.grid(row=0, column=3, padx=4, pady=4)
        
        ttk.Label(frm_conn, text="Unit").grid(row=0, column=4, padx=4, pady=4, sticky="e")
        self.ent_unit = ttk.Entry(frm_conn, width=6)
        self.ent_unit.insert(0, str(self.shaft.unit))
        self.ent_unit.grid(row=0, column=5, padx=4, pady=4)
        
        self.btn_connect = ttk.Button(frm_conn, text="Connect", command=self.on_connect, width=12)
        self.btn_connect.grid(row=0, column=6, padx=6, pady=4)
        self.btn_disconnect = ttk.Button(frm_conn, text="Disconnect", command=self.on_disconnect, width=12)
        self.btn_disconnect.grid(row=0, column=7, padx=6, pady=4)
        
        # ----- Position Display -----
        frm_pos = ttk.LabelFrame(self, text="Shaft Position")
        frm_pos.pack(fill="x", padx=10, pady=8)
        
        self.lbl_position = ttk.Label(frm_pos, text="Position: —", font=("Consolas", 18, "bold"))
        self.lbl_position.pack(pady=10)
        
        # ----- Cylinder Controls (Momentary Buttons) -----
        # NOTE: DO00 and DO01 are SWAPPED per requirements:
        # - DO00 now controls DOWN valve
        # - DO01 now controls UP valve
        frm_cyl = ttk.LabelFrame(self, text="Cylinder Control (Momentary)")
        frm_cyl.pack(fill="x", padx=10, pady=8)
        
        cyl_frame = ttk.Frame(frm_cyl)
        cyl_frame.pack(pady=10)
        
        # UP button (DO01)
        self.btn_up = tk.Button(cyl_frame, text="▲ UP (DO01)", width=20, height=3,
                                bg="#4CAF50", fg="white", font=("Arial", 12, "bold"))
        self.btn_up.grid(row=0, column=0, padx=10, pady=5)
        self.btn_up.bind("<ButtonPress-1>", lambda e: self._on_valve_press(1, True))
        self.btn_up.bind("<ButtonRelease-1>", lambda e: self._on_valve_release(1, False))
        
        # DOWN button (DO00)
        self.btn_down = tk.Button(cyl_frame, text="▼ DOWN (DO00)", width=20, height=3,
                                  bg="#2196F3", fg="white", font=("Arial", 12, "bold"))
        self.btn_down.grid(row=1, column=0, padx=10, pady=5)
        self.btn_down.bind("<ButtonPress-1>", lambda e: self._on_valve_press(0, True))
        self.btn_down.bind("<ButtonRelease-1>", lambda e: self._on_valve_release(0, False))
        
        ttk.Label(frm_cyl, text="Hold button to activate valve. Release to stop.",
                  foreground="#666").pack(pady=(0, 10))
        
        # ----- Go to Position -----
        frm_goto = ttk.LabelFrame(self, text="Go to Position (Automatic)")
        frm_goto.pack(fill="x", padx=10, pady=8)
        
        row1 = ttk.Frame(frm_goto)
        row1.pack(pady=8)
        ttk.Label(row1, text="Target Position:").pack(side="left", padx=4)
        self.ent_target = ttk.Entry(row1, width=12)
        self.ent_target.insert(0, "0")
        self.ent_target.pack(side="left", padx=4)
        
        self.btn_goto = ttk.Button(row1, text="Go to Position", command=self.on_go_to_position, width=16)
        self.btn_goto.pack(side="left", padx=10)
        
        self.btn_stop = ttk.Button(row1, text="STOP", command=self.on_stop_goto, width=10)
        self.btn_stop.pack(side="left", padx=4)
        self.btn_stop.config(state="disabled")
        
        self.lbl_goto_status = ttk.Label(frm_goto, text="Status: Idle", foreground="#666")
        self.lbl_goto_status.pack(pady=(0, 8))
        
        # ----- Status -----
        self.svar = tk.StringVar(value=self.shaft.status_text)
        ttk.Label(self, textvariable=self.svar).pack(anchor="w", padx=12, pady=(0, 8))
        
        self.after(50, self._tick)
    
    # ----- Connection handlers -----
    def on_connect(self):
        try:
            self.shaft.port = self.ent_port.get().strip()
            self.shaft.baudrate = int(self.ent_baud.get().strip())
            self.shaft.unit = int(self.ent_unit.get().strip())
            self.shaft.connect()
            self.svar.set(self.shaft.status_text)
        except Exception as e:
            messagebox.showerror("Shaft connect", str(e))
            self.svar.set(str(e))
    
    def on_disconnect(self):
        self.shaft.disconnect()
        self.svar.set(self.shaft.status_text)
    
    # ----- Valve control handlers (momentary) -----
    def _on_valve_press(self, do_channel, state):
        """Called when button is pressed - turn valve ON."""
        if not self.io.connected:
            return
        try:
            self.io.write_do(do_channel, state)
        except Exception as e:
            messagebox.showerror("IO write", str(e))
    
    def _on_valve_release(self, do_channel, state):
        """Called when button is released - turn valve OFF."""
        if not self.io.connected:
            return
        try:
            self.io.write_do(do_channel, state)
        except Exception as e:
            messagebox.showerror("IO write", str(e))
    
    # ----- Go to position handlers -----
    def on_go_to_position(self):
        """Start automatic movement to target position."""
        if self._going_to_position:
            messagebox.showwarning("Go to position", "Already moving to position!")
            return
        
        if not self.shaft.connected:
            messagebox.showerror("Go to position", "Shaft assembly not connected!")
            return
        
        if not self.io.connected:
            messagebox.showerror("Go to position", "IO not connected!")
            return
        
        try:
            self._target_position = int(self.ent_target.get().strip())
        except ValueError:
            messagebox.showerror("Go to position", "Invalid target position!")
            return
        
        self._going_to_position = True
        self.btn_goto.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.btn_up.config(state="disabled")
        self.btn_down.config(state="disabled")
        
        # Start movement in background thread
        import threading
        self._go_thread = threading.Thread(target=self._go_to_position_worker, daemon=True)
        self._go_thread.start()
    
    def on_stop_goto(self):
        """Stop automatic movement."""
        self._going_to_position = False
        self._cleanup_goto()
    
    def _go_to_position_worker(self):
        """Background worker to control valves until target is reached."""
        import time
        
        try:
            # Tolerance for position matching (±2 counts)
            tolerance = 2
            # Timeout in seconds
            timeout = 30.0
            start_time = time.time()
            
            while self._going_to_position:
                # Check timeout
                if time.time() - start_time > timeout:
                    self.after(0, lambda: self.lbl_goto_status.config(
                        text="Status: Timeout!", foreground="red"))
                    break
                
                # Read current position
                current_pos = self.shaft.get_position()
                diff = self._target_position - current_pos
                
                # Update status
                self.after(0, lambda d=diff, c=current_pos: self.lbl_goto_status.config(
                    text=f"Status: Moving (current={c}, diff={d:+d})", foreground="blue"))
                
                # Check if we've reached target
                if abs(diff) <= tolerance:
                    self.after(0, lambda: self.lbl_goto_status.config(
                        text="Status: Target reached!", foreground="green"))
                    break
                
                # Control valves based on position difference
                # NOTE: Swapped mapping - DO00=down, DO01=up
                if diff > 0:
                    # Need to go UP - activate DO01 (up valve)
                    self.io.write_do(1, True)
                    self.io.write_do(0, False)
                else:
                    # Need to go DOWN - activate DO00 (down valve)
                    self.io.write_do(0, True)
                    self.io.write_do(1, False)
                
                time.sleep(0.1)  # Poll every 100ms
            
        except Exception as e:
            self.after(0, lambda: self.lbl_goto_status.config(
                text=f"Status: Error - {e}", foreground="red"))
        
        finally:
            # Always turn off both valves when done
            try:
                self.io.write_do(0, False)
                self.io.write_do(1, False)
            except Exception:
                pass
            
            self._going_to_position = False
            self.after(0, self._cleanup_goto)
    
    def _cleanup_goto(self):
        """Re-enable controls after go-to-position completes."""
        self.btn_goto.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.btn_up.config(state="normal")
        self.btn_down.config(state="normal")
        if not self._going_to_position and self.lbl_goto_status.cget("text").startswith("Status: Moving"):
            self.lbl_goto_status.config(text="Status: Stopped", foreground="orange")
    
    # ----- UI update -----
    def _tick(self):
        if self.shaft.connected:
            pos = self.shaft.get_position()
            self.lbl_position.config(text=f"Position: {pos}")
        else:
            self.lbl_position.config(text="Position: —")
        
        self.svar.set(self.shaft.status_text)
        self.after(50, self._tick)
