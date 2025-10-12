from pymodbus.client import ModbusTcpClient
from inspect import signature

class MT3A:
    def __init__(self, host="192.168.1.12", port=502, unit=1, timeout=2.0):
        self.client = ModbusTcpClient(host=host, port=port, timeout=timeout)
        self.unit = unit
        # Detect whether methods expect 'unit' (2.x) or 'slave' (3.x)
        self._param = "unit"
        try:
            params = signature(self.client.read_coils).parameters
            if "slave" in params:
                self._param = "slave"
        except Exception:
            pass

    def _kw(self):
        return {self._param: self.unit}

    def connect(self):
        if not self.client.connect():
            raise RuntimeError(f"Could not connect to {self.client.host}:{self.client.port}")

    def close(self):
        self.client.close()

    # Digital I/O
    def read_di(self, count=16, address=0):
        rr = self.client.read_discrete_inputs(address=address, count=count, **self._kw())
        if rr.isError(): raise RuntimeError(rr)
        return list(rr.bits)[:count]

    def read_do(self, count=32, address=0):
        rr = self.client.read_coils(address=address, count=count, **self._kw())
        if rr.isError(): raise RuntimeError(rr)
        return list(rr.bits)[:count]

    def write_do(self, index, value, address_base=0):
        rq = self.client.write_coil(address=address_base + index, value=bool(value), **self._kw())
        if rq.isError(): raise RuntimeError(rq)
        return True

    def write_dos(self, values, address=0):
        rq = self.client.write_coils(address=address, values=[bool(v) for v in values], **self._kw())
        if rq.isError(): raise RuntimeError(rq)
        return True

    # Analog (if you add AI/AO modules later)
    def read_ai(self, start=0, count=8):
        rr = self.client.read_input_registers(address=start, count=count, **self._kw())
        if rr.isError(): raise RuntimeError(rr)
        return rr.registers

    def read_ao(self, start=0, count=8):
        rr = self.client.read_holding_registers(address=start, count=count, **self._kw())
        if rr.isError(): raise RuntimeError(rr)
        return rr.registers

    def write_ao(self, start, values):
        rq = self.client.write_registers(address=start, values=values, **self._kw())
        if rq.isError(): raise RuntimeError(rq)
        return True
