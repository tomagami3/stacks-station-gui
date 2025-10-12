# Axis wrapper that uses the shared COM bus and per-call addressing

import threading
from typing import Optional
from shared_bus import get_bus

try:
    from actuator_ui_simpler import combine_axis
except Exception:
    def combine_axis(coarse: int, fine: int) -> int:
        return int(coarse)


class MotorWorker:
    """
    Logical axis on the shared COM port.
    - No re-connects needed per axis; the bus keeps a single open handle.
    - Each call forwards the specific unit (address) to the driver.
    """
    def __init__(self, port, baud, unit, poll_hz=25):
        self.port = str(port)
        self.baud = int(baud)
        self.unit = int(unit)

        self.poll_period = 1.0 / max(1.0, poll_hz)
        self.bus = get_bus(self.port, self.baud)

        self.connected = False
        self.status_text = "Disconnected"
        self.pos_counts = 0

        self._run = False
        self._poll_thread: Optional[threading.Thread] = None

    # ----- lifecycle -----
    def connect(self):
        if self.connected:
            return
        try:
            # The bus will open the COM on first call; nothing else to do here
            self.connected = True
            self.status_text = f"Connected {self.port}@{self.baud} unit={self.unit}"
            # Best-effort work mode (ignored if unsupported)
            try:
                self.bus.call("set_work_mode", self.unit, 5)
            except Exception:
                pass
            self.resume_poll()
        except Exception as e:
            self.connected = False
            self.status_text = f"Connect failed: {e}"
            raise

    def disconnect(self):
        self.pause_poll()
        self.connected = False
        self.status_text = "Disconnected"

    # ----- polling control -----
    def resume_poll(self):
        if self._poll_thread and self._poll_thread.is_alive():
            self._run = True
            return
        self._run = True
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def pause_poll(self):
        self._run = False
        if self._poll_thread:
            self._poll_thread.join(timeout=1.0)
        self._poll_thread = None

    # ----- polling -----
    def _poll_loop(self):
        import time
        while self._run and self.connected:
            try:
                c, v = self.bus.call("read_encoder", self.unit)
                self.pos_counts = combine_axis(c, v)
            except Exception:
                pass
            time.sleep(self.poll_period)

    def read_position_now(self) -> int:
        if not self.connected:
            return self.pos_counts
        try:
            c, v = self.bus.call("read_encoder", self.unit)
            self.pos_counts = combine_axis(c, v)
        except Exception:
            pass
        return self.pos_counts

    # ----- motion -----
    def move_to_abs_target(self, target_counts: int, speed_rpm: int, acc: int):
        cur = self.read_position_now()
        delta = int(target_counts) - int(cur)
        try:
            self.bus.call("go_to_position", self.unit,
                          axis_counts=int(delta), speed_rpm=int(speed_rpm), acc=int(acc))
        except Exception as e:
            self.status_text = f"ABS move failed: {e}"

    def go_rel(self, delta_counts: int, speed_rpm: int, acc: int):
        try:
            self.bus.call("go_to_position", self.unit,
                          axis_counts=int(delta_counts), speed_rpm=int(speed_rpm), acc=int(acc))
        except Exception as e:
            self.status_text = f"REL move failed: {e}"

    def estop(self):
        try:
            self.bus.call("estop", self.unit)
        except Exception:
            pass
