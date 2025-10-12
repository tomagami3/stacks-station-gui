# ND556 single-axis RS-485 demo (no CLI args) — auto-compatible with pymodbus 3.x keyword changes
import time
import inspect
from pymodbus.client import ModbusSerialClient

# -------- CONFIG --------
PORT = "COM11"
BAUD = 9600
PARITY = 'N'
STOPBITS = 1
BYTESIZE = 8
ADDR = 1  # your drive's Modbus address
TIMEOUT_S = 1.0

# Example register map (adjust to your ND556/DM556RS manual)
REG_ENABLE = 0x2000
REG_CMD = 0x2001  # 0=stop, 1=jog CW, 2=jog CCW  (example)
REG_SPEED_HZ = 0x2002
REG_ACC = 0x2003
REG_DEC = 0x2004
REG_STATUS = 0x2100


# --- figure out which kw the installed pymodbus expects: device_id / slave / unit ---
def _addr_kw_for(func):
    sig = inspect.signature(func)
    for name in ("device_id", "slave", "unit"):
        if name in sig.parameters:
            return name
    # fallback: try in that order at runtime
    return None


def _write_register(client, addr, value, addr_kw):
    if addr_kw:
        return client.write_register(address=addr, value=value, **{addr_kw: ADDR})
    # brute-force fallback
    for key in ("device_id", "slave", "unit"):
        try:
            return client.write_register(address=addr, value=value, **{key: ADDR})
        except TypeError:
            continue
    raise TypeError("write_register: no compatible address keyword (device_id/slave/unit)")


def _read_holding(client, addr, count, addr_kw):
    if addr_kw:
        return client.read_holding_registers(address=addr, count=count, **{addr_kw: ADDR})

