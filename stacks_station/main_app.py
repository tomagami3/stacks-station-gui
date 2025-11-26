# main_app.py
import os, sys, tkinter as tk
from tkinter import ttk, messagebox

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "actuator"))
sys.path.insert(0, os.path.join(ROOT, "IO"))
sys.path.insert(0, os.path.join(ROOT, "cameras"))

from app_config import Config
from camera_snapshotter import CameraSnapshotter
from motor_worker import MotorWorker
from io_worker import IOWorker, get_do_list
from web_control import start_control_server, get_local_ip
from ui_tabs import MotorTab, IOAndStacksTab, StacksViewTab, AuxCamTab, SetupTab, ShaftAssemblyTab
from settings_store import SettingsStore
from shaft_worker import ShaftAssemblyWorker

# Optional camera URL from cameras/stacks.py
try:
    import stacks as camst
    if hasattr(camst, "STREAM_URL"):
        Config.CAM_MAIN_URL = camst.STREAM_URL
except Exception:
    pass

# IO defaults from manual_io
try:
    import manual_io as io_mod
except Exception:
    try:
        import maunal_io as io_mod
    except Exception:
        io_mod = None
if io_mod is not None:
    Config.IO_HOST = getattr(io_mod, "HOST_DEFAULT", Config.IO_HOST)
    Config.IO_UNIT = getattr(io_mod, "UNIT_DEFAULT", Config.IO_UNIT)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Habonim – Stacks Station")
        try:
            ttk.Style(self).theme_use(Config.THEME)
        except Exception:
            pass
        self.geometry(Config.WINDOW)

        # ---- settings ----
        self.settings = SettingsStore()
        self.leave_do_on_exit = tk.BooleanVar(value=bool(self.settings.get("leave_do_on_exit", True)))
        self.camera_enabled = tk.BooleanVar(value=bool(self.settings.get("camera_enabled", True)))

        # ---- workers ----
        self.iow = IOWorker(Config.IO_HOST, Config.IO_UNIT, poll_hz=Config.IO_POLL_HZ)
        try:
            self.iow.connect()
        except Exception as e:
            messagebox.showwarning("IO connect", f"{e}")

        self.motor_stacks = MotorWorker(Config.SERVO_PORT, Config.SERVO_BAUD, Config.SERVO_STACKS_UNIT, poll_hz=Config.MOTOR_POLL_HZ)
        self.motor_torque = MotorWorker(Config.SERVO_PORT, Config.SERVO_BAUD, Config.SERVO_TORQUE_UNIT, poll_hz=Config.MOTOR_POLL_HZ)
        self.motor_table  = MotorWorker(Config.SERVO_PORT, Config.SERVO_BAUD, Config.SERVO_TABLE_UNIT,  poll_hz=Config.MOTOR_POLL_HZ)
        
        # Shaft assembly worker (COM15, 4800-8N1)
        self.shaft_assembly = ShaftAssemblyWorker(port=Config.SHAFT_PORT, baudrate=Config.SHAFT_BAUD, 
                                                   unit=Config.SHAFT_UNIT, poll_hz=10)

        # ---- cameras (start only if enabled) ----
        self.cam_main = None
        self.cam_aux  = None
        self._ensure_cameras_running(self.camera_enabled.get())

        # Shared percentages for stacks view
        self.shared_pcts = {"pcts": [0]*6}

        # Phone control server: pass stacks & table motors + settings (for half-auto)
        self.server_port = Config.PHONE_PORT
        #self.httpd = start_control_server(self.iow, self.motor_stacks, self.motor_table, self.settings, port=self.server_port)
        # AFTER:
        self.httpd = start_control_server(
            self.iow,
            self.motor_stacks,
            self.motor_table,
            self.motor_torque,  # <-- NEW
            self.settings,
            port=self.server_port
        )
        self.server_ip = get_local_ip()

        # ---- top bar ----
        topbar = ttk.Frame(self, padding=(10, 8)); topbar.pack(fill="x")
        ttk.Label(topbar, text=f"Phone control: http://{self.server_ip}:{self.server_port}", foreground="#0a0").pack(side="left")
        ttk.Separator(topbar, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Checkbutton(topbar, text="Leave DO outputs ON at exit", variable=self.leave_do_on_exit,
                        command=self._persist_leave_do).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(topbar, text="Enable cameras (fast debug OFF)", variable=self.camera_enabled,
                        command=self._on_toggle_cameras).pack(side="left")

        # ---- tabs ----
        nb = ttk.Notebook(self); nb.pack(fill="both", expand=True)
        self.tab_motor_stacks = MotorTab(nb, self.motor_stacks); nb.add(self.tab_motor_stacks, text="Stacks Motor (addr 1)")
        self.tab_motor_torque = MotorTab(nb, self.motor_torque); nb.add(self.tab_motor_torque, text="Torque Motor (addr 2)")
        self.tab_motor_table  = MotorTab(nb, self.motor_table);  nb.add(self.tab_motor_table,  text="Table Motor (addr 3)")

        self.tab_setup = SetupTab(nb, self.settings); nb.add(self.tab_setup, text="Setup")
        
        # Shaft assembly tab
        self.tab_shaft = ShaftAssemblyTab(nb, self.shaft_assembly, self.iow)
        nb.add(self.tab_shaft, text="Shaft Assembly")

        # IO + cameras tabs (canvas will handle None frames gracefully when cameras disabled)
        self.tab_io   = IOAndStacksTab(nb, self.iow, self.shared_pcts, self._snapper_proxy("main"))
        self.tab_view = StacksViewTab(nb, self._snapper_proxy("main"), self.shared_pcts)
        self.tab_aux  = AuxCamTab(nb, self._snapper_proxy("aux"))
        nb.add(self.tab_io,   text="IO & Stacks %")
        nb.add(self.tab_view, text="Stacks View (lines)")
        nb.add(self.tab_aux,  text="Aux Camera (192.168.1.101)")

        def _on_tab_changed(event):
            tab_text = nb.tab(nb.select(), "text")
            # camera rate tweak only if cameras running
            if self.cam_main:
                if tab_text.startswith("Stacks View"):
                    self.cam_main.set_rate(10); self.cam_main.set_flush(0.5)
                else:
                    self.cam_main.set_rate(Config.CAMERA_SNAPSHOT_HZ); self.cam_main.set_flush(0.25)
            # pause other motors
            for label, mw in [("Stacks Motor", self.motor_stacks), ("Torque Motor", self.motor_torque), ("Table Motor", self.motor_table)]:
                try: mw.pause_poll()
                except Exception: pass
            try:
                if tab_text.startswith("Stacks Motor"): self.motor_stacks.resume_poll()
                elif tab_text.startswith("Torque Motor"): self.motor_torque.resume_poll()
                elif tab_text.startswith("Table Motor"):  self.motor_table.resume_poll()
            except Exception:
                pass

        nb.bind("<<NotebookTabChanged>>", _on_tab_changed)

        # Connect motors
        for mw in (self.motor_stacks, self.motor_torque, self.motor_table):
            try: mw.connect()
            except Exception as e: messagebox.showwarning("Motor connect", f"{e}")
        try:
            self.motor_torque.pause_poll(); self.motor_table.pause_poll()
        except Exception: pass

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ----- camera helpers -----
    def _ensure_cameras_running(self, enable: bool):
        if enable and (self.cam_main is None and self.cam_aux is None):
            try:
                self.cam_main = CameraSnapshotter(Config.CAM_MAIN_URL, hz=Config.CAMERA_SNAPSHOT_HZ); self.cam_main.start()
            except Exception:
                self.cam_main = None
            try:
                self.cam_aux  = CameraSnapshotter(Config.CAM_AUX_URL,  hz=Config.CAMERA_SNAPSHOT_HZ); self.cam_aux.start()
            except Exception:
                self.cam_aux = None
        if (not enable) and (self.cam_main or self.cam_aux):
            try:
                if self.cam_main: self.cam_main.stop()
            except Exception: pass
            try:
                if self.cam_aux: self.cam_aux.stop()
            except Exception: pass
            self.cam_main = None; self.cam_aux = None

    def _on_toggle_cameras(self):
        val = bool(self.camera_enabled.get())
        self.settings.set("camera_enabled", val); self.settings.save()
        self._ensure_cameras_running(val)

    def _snapper_proxy(self, which):
        """Provide a small object with get()/set_rate()/set_flush() even when cameras are disabled."""
        class Proxy:
            def __init__(self, outer, which):
                self.outer, self.which = outer, which
            def get(self):
                cam = self.outer.cam_main if self.which == "main" else self.outer.cam_aux
                return cam.get() if cam else None
            def set_rate(self, hz):
                cam = self.outer.cam_main if self.which == "main" else self.outer.cam_aux
                if cam: cam.set_rate(hz)
            def set_flush(self, sec):
                cam = self.outer.cam_main if self.which == "main" else self.outer.cam_aux
                if cam: cam.set_flush(sec)
        return Proxy(self, which)

    # ----- misc -----
    def _persist_leave_do(self):
        self.settings.set("leave_do_on_exit", bool(self.leave_do_on_exit.get())); self.settings.save()

    def _on_close(self):
        try: self.httpd.shutdown()
        except Exception: pass
        try:
            if self.cam_main: self.cam_main.stop()
        except Exception: pass
        try:
            if self.cam_aux: self.cam_aux.stop()
        except Exception: pass
        for mw in (self.motor_stacks, self.motor_torque, self.motor_table):
            try: mw.pause_poll()
            except Exception: pass
        try:
            self.shaft_assembly.disconnect()
        except Exception: pass
        try:
            if self.iow.connected and not self.settings.get("leave_do_on_exit", True):
                for ch, _ in get_do_list():
                    try: self.iow.write_do(ch, False)
                    except Exception: pass
        except Exception: pass
        try: self.iow.disconnect()
        except Exception: pass
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
