from pymodbus.client import ModbusSerialClient
import inspect
import time

UNIT_ID = 1  # your motor's Modbus address

def int32_to_regs(val: int):
    if val < 0:
        val = (1 << 32) + val
    return [(val >> 16) & 0xFFFF, val & 0xFFFF]

class Servo42DModbus:
    def __init__(self, port="COM9", baudrate=38400, parity="N", stopbits=1, bytesize=8, timeout=0.2, unit=UNIT_ID):
        # On new pymodbus, RTU is default; no 'method' arg.
        self.client = ModbusSerialClient(
            port=port, baudrate=baudrate, parity=parity, stopbits=stopbits, bytesize=bytesize, timeout=timeout
        )
        self.unit = unit
        if not self.client.connect():
            raise RuntimeError("Failed to open Modbus serial port")

        # Decide which keyword (if any) the installed pymodbus expects for unit id
        self._addr_kw = self._detect_addr_kw()

    def _detect_addr_kw(self):
        # Inspect one of the bound methods to see its parameter names
        sample_fn = self.client.write_register  # any client method will do
        params = inspect.signature(sample_fn).parameters
        if "unit" in params:
            return "unit"
        if "slave" in params:
            return "slave"
        # Neither present => pass no keyword (some builds use positional-only or global default)
        return None

    def _addr_kwargs(self):
        return {self._addr_kw: self.unit} if self._addr_kw else {}

    def close(self):
        self.client.close()

    # Thin wrappers that always pass the right addressing kw (or none)
    def write_reg(self, addr, value):
        return self.client.write_register(address=addr, value=value, **self._addr_kwargs())

    def write_regs(self, addr, values):
        return self.client.write_registers(address=addr, values=values, **self._addr_kwargs())

    def read_input_regs(self, addr, count):
        return self.client.read_input_registers(address=addr, count=count, **self._addr_kwargs())

    # ---- High-level helpers ----
    def set_work_mode(self, mode_code=5, reg_addr=0x0082):
        # SR_vFOC is typically 5
        res = self.write_reg(reg_addr, mode_code)
        if hasattr(res, "isError") and res.isError():
            raise RuntimeError(f"set_work_mode failed: {res}")
        return True

    def read_encoder(self):
        # carry (int32) + value (uint16) at 0x0030
        rr = self.read_input_regs(0x0030, 3)
        if hasattr(rr, "isError") and rr.isError():
            raise RuntimeError(f"read_encoder failed: {rr}")
        r = rr.registers
        carry = (r[0] << 16) | r[1]
        if carry & 0x80000000:
            carry -= (1 << 32)
        value = r[2]
        return carry, value

    def go_to_position(self, axis_counts: int, speed_rpm: int = 600, acc: int = 100):
        # Position mode 4 block @ 0x00F4: [acc(uint16), speed(uint16), axis(int32)]
        acc = max(0, min(255, int(acc)))
        speed_rpm = max(0, min(3000, int(speed_rpm)))
        payload = [acc, speed_rpm] + int32_to_regs(int(axis_counts))
        wr = self.write_regs(0x00F4, payload)
        if hasattr(wr, "isError") and wr.isError():
            raise RuntimeError(f"go_to_position failed: {wr}")
        return True

    def estop(self):
        wr = self.write_reg(0x00F7, 1)
        if hasattr(wr, "isError") and wr.isError():
            raise RuntimeError(f"estop failed: {wr}")
        return True

    def _read_hold(self, addr, count):
        return self.client.read_holding_registers(address=addr, count=count, **self._addr_kwargs())

    def _write_hold(self, addr, values):
        return self.client.write_registers(address=addr, values=values, **self._addr_kwargs())

    def _to_regs_be(self, val32):
        if val32 < 0:
            val32 = (1 << 32) + val32
        return [(val32 >> 16) & 0xFFFF, val32 & 0xFFFF]

    def _to_regs_le(self, val32):
        if val32 < 0:
            val32 = (1 << 32) + val32
        return [val32 & 0xFFFF, (val32 >> 16) & 0xFFFF]

    def self_calibrate_position_block(self, guess_addr=0x00F4, test_span=500, rpm=200, acc=80, tol=80):
        """
        Auto-detect:
          - correct base address (guess_addr or guess_addr+1)
          - word order (BE vs LE)
          - ABS vs REL effect
        It moves a small amount safely, then restores the original position.
        """
        start = self.read_axis_abs()

        candidates = [
            ("addr=guess, BE", guess_addr, self._to_regs_be),
            ("addr=guess+1, BE", guess_addr + 1, self._to_regs_be),
            ("addr=guess, LE", guess_addr, self._to_regs_le),
            ("addr=guess+1, LE", guess_addr + 1, self._to_regs_le),
        ]

        def write_block(addr, to_regs, axis):
            payload = [max(0, min(255, acc)), max(0, min(3000, rpm))] + to_regs(axis)
            wr = self._write_hold(addr, payload)
            return (not hasattr(wr, "isError")) or (not wr.isError())

        # try each candidate by commanding a small ABS jump from current
        # Then see how the axis changed (ABS vs REL)
        best = None
        for name, addr, to_regs in candidates:
            # Move to start + test_span
            target = start + test_span
            ok = write_block(addr, to_regs, target)
            if not ok:
                continue
            time.sleep(0.25)
            mid = self.read_axis_abs()
            # Move back to start
            ok2 = write_block(addr, to_regs, start)
            time.sleep(0.25)
            end = self.read_axis_abs()

            # Evaluate behavior
            # If ABS correct: mid ~= start+span and end ~= start
            abs_ok = (abs(mid - (start + test_span)) <= tol) and (abs(end - start) <= tol)
            # If REL: mid ~= start+span (because it added span), but the second command tries ABS=start
            # which REL interprets as delta=start (huge), so end will be far off. So abs_ok will be False.
            if abs_ok:
                best = (addr, to_regs, False)  # block acts ABS
                break

        if best is None:
            # Try detecting REL semantics explicitly using the *same* candidate grid
            for name, addr, to_regs in candidates:
                # Command "target = start + test_span" twice; REL will add twice, ABS will go there once.
                ok = write_block(addr, to_regs, start + test_span)
                time.sleep(0.25)
                mid = self.read_axis_abs()
                ok2 = write_block(addr, to_regs, start + test_span)
                time.sleep(0.25)
                end = self.read_axis_abs()

                # REL: mid ≈ start+span, end ≈ start+2*span
                rel_ok = (abs(mid - (start + test_span)) <= tol) and (abs(end - (start + 2 * test_span)) <= tol)
                if rel_ok:
                    best = (addr, to_regs, True)  # block acts REL
                    break

        # Restore position if we drifted
        if abs(self.read_axis_abs() - start) > tol:
            # use a precise absolute correction (works even for REL blocks)
            self.go_to_absolute_exact(start, speed_rpm=max(200, rpm), acc=max(60, acc), tol_counts=tol, timeout_s=20.0)

        if best is None:
            raise RuntimeError("Could not auto-detect position block. Check wiring/firmware map.")

        addr, to_regs, is_rel = best
        # lock in the findings
        self.position_block_addr = addr
        self.block_is_relative = is_rel
        self._axis_to_regs = to_regs  # store the chosen packer

        return {"address": hex(addr), "word_order": ("BE" if to_regs == self._to_regs_be else "LE"),
                "semantics": ("REL" if is_rel else "ABS")}


if __name__ == "__main__":
    drv = Servo42DModbus(port="COM9", baudrate=38400, unit=UNIT_ID)

    # Optional: ensure serial-control mode (SR_vFOC = 5)
    drv.set_work_mode(5)

    # Sanity check
    print("Encoder:", drv.read_encoder())

    # Example: move ~1 rev if your encoder-addition scale is 16000 counts/rev
    drv.go_to_position(axis_counts=16000, speed_rpm=500, acc=120)

    # drv.estop()
    drv.close()
