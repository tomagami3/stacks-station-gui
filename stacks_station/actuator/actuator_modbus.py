# Robust, per-call addressing driver for MKS SERVO42/57D
# Works with pymodbus variants using device_id / unit / slave

import inspect
from pymodbus.client import ModbusSerialClient


def _int32_to_regs(val: int):
    if val < 0:
        val = (1 << 32) + val
    return [(val >> 16) & 0xFFFF, val & 0xFFFF]


def _regs_to_int32(hi: int, lo: int) -> int:
    x = ((hi & 0xFFFF) << 16) | (lo & 0xFFFF)
    if x & 0x80000000:
        x -= (1 << 32)
    return x


class Servo42DModbus:
    """
    Thin wrapper around ModbusSerialClient that:
      - opens the serial port once
      - auto-detects the address keyword (device_id / unit / slave)
      - ALWAYS passes the target address per call
    """
    # Register map (keep centralized)
    REG_ENCODER_BASE = 0x0030   # input: [carry_hi, carry_lo, value]
    REG_MOVE_BLOCK   = 0x00F4   # holding: [acc, speed_rpm, pos_hi, pos_lo]
    REG_MB_RTU       = 0x008E   # holding: 0/1 disable/enable Modbus-RTU
    REG_UART_ADDR    = 0x008B   # holding: set slave address

    def __init__(self, port="COM10", baudrate=38400, parity="N", stopbits=1, bytesize=8, timeout=0.3, unit=1):
        self.client = ModbusSerialClient(
            port=port, baudrate=baudrate, parity=parity, stopbits=stopbits, bytesize=bytesize, timeout=timeout
        )
        if not self.client.connect():
            raise RuntimeError(f"Failed to open serial {port} @ {baudrate}")

        # detect address keyword once
        self._addr_kw = self._detect_addr_kw()

    # ---------- internals ----------
    def _detect_addr_kw(self) -> str:
        for method in (self.client.read_input_registers, self.client.write_registers, self.client.write_register):
            params = inspect.signature(method).parameters
            for key in ("device_id", "unit", "slave"):
                if key in params:
                    return key
        raise RuntimeError("pymodbus client exposes none of: device_id / unit / slave")

    def _kw(self, unit: int):
        return {self._addr_kw: int(unit)}

    # ---------- lifecycle ----------
    def close(self):
        try:
            self.client.close()
        except Exception:
            pass

    # ---------- low-level helpers (ALWAYS pass unit) ----------
    def _read_input_regs(self, unit: int, addr: int, count: int):
        rr = self.client.read_input_registers(address=addr, count=count, **self._kw(unit))
        if hasattr(rr, "isError") and rr.isError():
            raise RuntimeError(rr)
        return rr.registers

    def _write_reg(self, unit: int, addr: int, value: int):
        r = self.client.write_register(address=addr, value=value, **self._kw(unit))
        if hasattr(r, "isError") and r.isError():
            raise RuntimeError(r)

    def _write_regs(self, unit: int, addr: int, values):
        r = self.client.write_registers(address=addr, values=values, **self._kw(unit))
        if hasattr(r, "isError") and r.isError():
            raise RuntimeError(r)

    # ---------- high-level API used by the app ----------
    def set_work_mode(self, mode_code=5, unit=1):
        # Safe to no-op if device ignores it
        try:
            self._write_reg(unit, 0x0082, int(mode_code))
        except Exception:
            pass
        return True

    def read_encoder(self, unit=1):
        r = self._read_input_regs(unit, self.REG_ENCODER_BASE, 3)
        carry = _regs_to_int32(r[0], r[1])
        value = r[2]
        return carry, value

    def go_to_position(self, axis_counts: int, speed_rpm: int = 600, acc: int = 100, unit=1):
        acc = max(0, min(255, int(acc)))
        speed_rpm = max(0, min(3000, int(speed_rpm)))
        payload = [acc, speed_rpm] + _int32_to_regs(int(axis_counts))
        self._write_regs(unit, self.REG_MOVE_BLOCK, payload)
        return True

    def estop(self, unit=1):
        # Many firmwares accept 0x00F5 accel tweak + same position to decelerate fast.
        try:
            self._write_reg(unit, 0x00F5, 8)
        except Exception:
            pass
        return True

    # Optional utilities
    def enable_modbus(self, unit=1, enable=True):
        self._write_reg(unit, self.REG_MB_RTU, 1 if enable else 0)

    def set_address(self, current_unit: int, new_unit: int):
        self._write_reg(current_unit, self.REG_UART_ADDR, int(new_unit))
