from pymodbus.client import ModbusSerialClient
import inspect
import time

UNIT_ID = 1  # your motor's Modbus address

def _int32_to_regs_be(v: int):
    if v < 0:
        v = (1 << 32) + v
    return [(v >> 16) & 0xFFFF, v & 0xFFFF]

def _int32_to_regs_le(v: int):
    if v < 0:
        v = (1 << 32) + v
    return [v & 0xFFFF, (v >> 16) & 0xFFFF]

class Servo42DModbus:
    """
    Modbus RTU driver for MKS SERVO42D with self-calibration of:
      - position block base address (+0 or +1)
      - 32-bit word order (BE / LE)
      - semantics (ABS vs REL effect)
    Provides exact ABS and REL moves that converge to target within tolerance.
    """
    def __init__(self, port="COM9", baudrate=38400, parity="N", stopbits=1,
                 bytesize=8, timeout=0.3, unit=UNIT_ID):
        # Build kwargs based on what your pymodbus actually supports
        base_kwargs = dict(
            port=port, baudrate=baudrate, parity=parity,
            stopbits=stopbits, bytesize=bytesize, timeout=timeout
        )
        # Optional kwargs we add only if available
        optional = {
            "retries": 2,
            "retry_on_empty": True,
            "close_comm_on_error": True,
        }
        sig = inspect.signature(ModbusSerialClient.__init__).parameters
        for k, v in optional.items():
            if k in sig:
                base_kwargs[k] = v

        # Create client (RTU is default framing on recent pymodbus)
        self.client = ModbusSerialClient(**base_kwargs)

        self.unit = unit
        if not self.client.connect():
            raise RuntimeError("Failed to open Modbus serial port")

        # Decide whether methods want unit= or slave= (or positional-only)
        self._addr_kw = self._detect_addr_kw()
    # --------- pymodbus KW detection ----------
    def _detect_addr_kw(self):
        sample_fn = self.client.write_register  # bound method
        params = inspect.signature(sample_fn).parameters
        if "unit" in params:
            return "unit"
        if "slave" in params:
            return "slave"
        return None

    def _addr_kwargs(self):
        return {self._addr_kw: self.unit} if self._addr_kw else {}

    # --------- low-level wrappers ----------
    def close(self):
        self.client.close()

    def write_reg(self, addr, value):
        return self.client.write_register(address=addr, value=value, **self._addr_kwargs())

    def write_regs(self, addr, values):
        return self.client.write_registers(address=addr, values=values, **self._addr_kwargs())

    def read_input_regs(self, addr, count):
        return self.client.read_input_registers(address=addr, count=count, **self._addr_kwargs())

    def read_holding_regs(self, addr, count):
        return self.client.read_holding_registers(address=addr, count=count, **self._addr_kwargs())

    # --------- high-level device helpers ----------
    def set_work_mode(self, mode_code=5, reg_addr=0x0082):
        # SR_vFOC typically = 5
        res = self.write_reg(reg_addr, mode_code)
        if hasattr(res, "isError") and res.isError():
            raise RuntimeError(f"set_work_mode failed: {res}")
        return True

    def read_encoder(self):
        """Return (carry:int32, value:uint16) from input regs starting 0x0030."""
        rr = self.read_input_regs(0x0030, 3)
        if hasattr(rr, "isError") and rr.isError():
            raise RuntimeError(f"read_encoder failed: {rr}")
        r = rr.registers
        carry = (r[0] << 16) | r[1]
        if carry & 0x80000000:
            carry -= (1 << 32)
        value = r[2]
        return carry, value

    def read_axis_abs(self) -> int:
        """Absolute axis counts from encoder parts (no software zero)."""
        c, v = self.read_encoder()
        return c * 65536 + (v & 0xFFFF)

    # --------- calibrated write to position block ----------
    def _write_position_block(self, abs_axis_target: int, speed_rpm: int, acc: int):
        acc = max(0, min(255, int(acc)))
        speed_rpm = max(0, min(3000, int(speed_rpm)))
        regs32 = self._axis_to_regs(int(abs_axis_target))
        payload = [acc, speed_rpm] + regs32
        wr = self.write_regs(self.position_block_addr, payload)
        if hasattr(wr, "isError") and wr.isError():
            raise RuntimeError(f"position write failed: {wr}")
        return True

    # Simple direct move (uses calibrated address/word order; ABS semantics if FW supports it)
    def go_to_position(self, axis_counts: int, speed_rpm: int = 600, acc: int = 100):
        return self._write_position_block(axis_counts, speed_rpm, acc)

    # Robust exact ABS move (works even if FW acts REL)
    def go_to_absolute_exact(
        self, target_abs: int, speed_rpm: int = 600, acc: int = 100,
        tol_counts: int = 30, settle_samples: int = 3, max_step_counts: int = 20000,
        timeout_s: float = 20.0, sample_ms: int = 80,
    ):
        acc = max(0, min(255, int(acc)))
        speed_rpm = max(0, min(3000, int(speed_rpm)))
        tol_counts = max(0, int(tol_counts))
        max_step_counts = max(1, int(max_step_counts))
        settle_needed = max(1, int(settle_samples))
        dt = max(0.02, sample_ms / 1000.0)

        t0 = time.time()
        settled = 0
        target_abs = int(target_abs)

        while True:
            cur = self.read_axis_abs()
            err = target_abs - cur
            if abs(err) <= tol_counts:
                settled += 1
                if settled >= settle_needed:
                    return True
            else:
                settled = 0
                step = max(-max_step_counts, min(max_step_counts, err))
                payload_axis = cur + step  # this works for ABS and REL firmwares
                self._write_position_block(payload_axis, speed_rpm, acc)

            if (time.time() - t0) > float(timeout_s):
                raise TimeoutError(f"Timeout to {target_abs} (last {cur}, err {err}, tol {tol_counts})")
            time.sleep(dt)

    def go_to_relative_exact(self, delta_counts: int, speed_rpm=600, acc=100, **kw):
        return self.go_to_absolute_exact(self.read_axis_abs() + int(delta_counts),
                                         speed_rpm=speed_rpm, acc=acc, **kw)

    # --------- self calibration of position block ----------
    def self_calibrate_position_block(self, guess_addr=0x00F4, test_span=500, rpm=200, acc=80, tol=80):
        """
        Detect:
          - correct base address (guess_addr or guess_addr+1)
          - word order (BE vs LE) for the 32-bit axis
          - ABS vs REL effect
        """
        start = self.read_axis_abs()

        def write_block(addr, packer, axis):
            payload = [max(0, min(255, acc)), max(0, min(3000, rpm))] + packer(axis)
            wr = self.write_regs(addr, payload)
            return (not hasattr(wr, "isError")) or (not wr.isError())

        candidates = [
            (guess_addr, _int32_to_regs_be),
            (guess_addr + 1, _int32_to_regs_be),
            (guess_addr, _int32_to_regs_le),
            (guess_addr + 1, _int32_to_regs_le),
        ]

        best = None
        # First, try to find true ABS behavior
        for addr, packer in candidates:
            ok = write_block(addr, packer, start + test_span)
            if not ok:
                continue
            time.sleep(0.25)
            mid = self.read_axis_abs()
            ok2 = write_block(addr, packer, start)
            time.sleep(0.25)
            end = self.read_axis_abs()
            abs_ok = (abs(mid - (start + test_span)) <= tol) and (abs(end - start) <= tol)
            if abs_ok:
                best = (addr, packer, False)  # False => not REL
                break

        # If none looked ABS, look for REL behavior
        if best is None:
            for addr, packer in candidates:
                ok = write_block(addr, packer, start + test_span)
                time.sleep(0.25)
                mid = self.read_axis_abs()
                ok2 = write_block(addr, packer, start + test_span)
                time.sleep(0.25)
                end = self.read_axis_abs()
                rel_ok = (abs(mid - (start + test_span)) <= tol) and (abs(end - (start + 2 * test_span)) <= tol)
                if rel_ok:
                    best = (addr, packer, True)
                    break

        # Restore position if drifted
        if abs(self.read_axis_abs() - start) > tol:
            self.go_to_absolute_exact(start, speed_rpm=max(200, rpm), acc=max(60, acc),
                                      tol_counts=tol, timeout_s=20.0)

        if best is None:
            raise RuntimeError("Could not auto-detect position block (addr/word/semantics).")

        addr, packer, is_rel = best
        self.position_block_addr = addr
        self._axis_to_regs = packer
        self.block_is_relative = is_rel

        return {"address": hex(addr),
                "word_order": ("BE" if packer is _int32_to_regs_be else "LE"),
                "semantics": ("REL" if is_rel else "ABS")}

if __name__ == "__main__":
    drv = Servo42DModbus(port="COM9", baudrate=38400, unit=UNIT_ID)
    drv.set_work_mode(5)  # optional

    print("Calibrating…")
    print(drv.self_calibrate_position_block())

    print("Encoder:", drv.read_encoder())

    # Example exact move to +16000 counts
    drv.go_to_absolute_exact(16000, speed_rpm=500, acc=120, tol_counts=50)

    drv.close()
