import threading
from typing import List, Tuple

_io_import_err = None
try:
    import manual_io as io_mod
except Exception as e1:
    try:
        import maunal_io as io_mod
    except Exception as e2:
        _io_import_err = (e1, e2)
        io_mod = None


def get_do_list() -> List[Tuple[int, str]]:
    """Collect a de-duplicated (ch, name) list from your IO module mappings."""
    lst = []
    try:
        for group in [io_mod.SHAFT_ASSEMBLY, io_mod.GENERAL_STATION, io_mod.STATION_STACKS]:
            lst.extend(group)
        if hasattr(io_mod, "TORQUE_STATION"):
            lst.extend(io_mod.TORQUE_STATION)
    except Exception:
        pass
    seen = set()
    out = []
    for ch, name in lst:
        if ch in seen:
            continue
        seen.add(ch)
        out.append((ch, name))
    return out


class IOWorker:
    """ Fast-scan IO using your MT3A client. """
    def __init__(self, host: str, unit: int, poll_hz: float = 25):
        if io_mod is None:
            raise RuntimeError(f"IO module not importable: {(_io_import_err or '')}")
        self.host = host
        self.unit = unit
        self.poll_period = 1.0 / max(1.0, poll_hz)

        self.cli = io_mod.MT3AClient(host=host, unit=unit)
        self.connected = False
        self.DO = {}
        self.DI = {}
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
        import threading
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
        import time
        while self._run and self.connected:
            try:
                st = self.cli.read_do(count=16)
                with self.lock:
                    for i, v in enumerate(st):
                        self.DO[i] = bool(v)
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

    @property
    def setup_password(self) -> str:
        return getattr(io_mod, "SETUP_PASSWORD", "2468")
