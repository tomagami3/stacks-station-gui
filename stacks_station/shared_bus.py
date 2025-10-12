# One shared serial handle per (port, baud) that forwards EVERY call with unit=<addr>

import threading
from typing import Optional, Tuple
from actuator_modbus import Servo42DModbus

_BUSES = {}
_BUSES_GUARD = threading.Lock()


class SharedComBus:
    def __init__(self, port: str, baud: int):
        self.port = str(port)
        self.baud = int(baud)
        self.lock = threading.Lock()
        self.drv: Optional[Servo42DModbus] = None

    def _ensure_open(self):
        if self.drv is None:
            # initial unit is irrelevant; every call passes unit explicitly
            self.drv = Servo42DModbus(port=self.port, baudrate=self.baud, unit=1)

    def call(self, fn_name: str, unit: int, *args, **kwargs):
        with self.lock:
            self._ensure_open()
            fn = getattr(self.drv, fn_name)
            # driver methods accept unit=... and internally map to device_id/unit/slave
            return fn(*args, unit=int(unit), **kwargs)

    def close(self):
        with self.lock:
            if self.drv:
                try:
                    self.drv.close()
                except Exception:
                    pass
                self.drv = None


def get_bus(port: str, baud: int) -> SharedComBus:
    key: Tuple[str, int] = (str(port), int(baud))
    with _BUSES_GUARD:
        bus = _BUSES.get(key)
        if bus is None:
            bus = SharedComBus(*key)
            _BUSES[key] = bus
        return bus
