import serial
import time

def checksum8(bytes_list):
    """Simple 8‑bit sum & 0xFF (manual Part4)."""
    return sum(bytes_list) & 0xFF

class Servo42D:
    def __init__(self, port, baud=38400, addr=0x01, timeout=0.1):
        self.addr = addr
        self.ser = serial.Serial(port=port, baudrate=baud, bytesize=8, parity='N',
                                 stopbits=1, timeout=timeout)

    def _txrx(self, payload, expect=True):
        """
        Send frame: FA [addr] ... [CRC]
        If expect=True, read a single reply frame (starts with FB).
        """
        frame = [0xFA, self.addr] + payload
        frame.append(checksum8(frame))
        self.ser.write(bytes(frame))
        if not expect:
            return None
        # read a small reply; most cmd replies are short
        hdr = self.ser.read(1)
        if hdr != b'\xFB':
            return None
        # read next 2 bytes: addr, func
        rest = self.ser.read(2)
        if len(rest) < 2:
            return None
        addr, func = rest[0], rest[1]
        # read until timeout (small replies), then validate CRC
        # small helper: read up to 8 bytes more
        data = bytearray()
        # try a few bytes; protocol is short
        for _ in range(16):
            b = self.ser.read(1)
            if not b:
                break
            data.extend(b)
        # last byte should be CRC
        if not data:
            return (addr, func, b"", None)
        crc = data[-1]
        body = bytes([0xFB, addr, func]) + data[:-1]
        ok = (checksum8(list(body)) == crc)
        return (addr, func, bytes(data[:-1]), ok)

    # ---- Core commands ----
    def enable(self, on=True):
        # F3 en (00 disable, 01 enable)
        return self._txrx([0xF3, 0x01 if on else 0x00])

    def query_status(self):
        # F1 -> status byte (0 fail, 1 stop, 2 speed up, 3 speed down, 4 full, 5 homing/Cal)
        return self._txrx([0xF1])

    def emergency_stop(self):
        return self._txrx([0xF7])

    def set_zero_here(self):
        # 92 = Set Current Axis to zero (no motion)
        return self._txrx([0x92])

    # ---- Jog (continuous speed) with F6 ----
    def jog(self, rpm=200, acc=50, direction=1):
        """
        Run continuously at rpm (0..3000), acc (0..255).
        direction: 0=CCW, 1=CW (per manual)
        F6 packs dir in MSB of Byte4; lower nibbles of Byte4/Byte5 form speed.
        """
        rpm = max(0, min(3000, int(rpm)))
        acc = max(0, min(255, int(acc)))
        direction = 1 if direction else 0

        # Pack speed across two nibbles as manual describes (dir in bit7 of byte4).
        # Speed is 12-bit: high 4 bits in low nibble of byte4; low 8 bits in byte5.
        hi4 = (rpm >> 8) & 0x0F
        lo8 = rpm & 0xFF
        byte4 = (direction << 7) | hi4
        byte5 = lo8
        return self._txrx([0xF6, byte4, byte5, acc])

    def stop_jog(self, decel_acc=100):
        """
        Stop F6: send speed=0 and an acceleration.
        acc=0 -> immediate; acc>0 -> decelerate to stop.
        """
        acc = max(0, min(255, int(decel_acc)))
        # dir bit doesn't matter, set 0; speed=0
        return self._txrx([0xF6, 0x00, 0x00, acc])

    # ---- Absolute move by axis (encoder counts) with F5 ----
    def go_to_position(self, axis_counts, rpm=300, acc=80):
        """
        Absolute motion by axis counts (int32). Supports real‑time update.
        rpm: 0..3000, acc: 0..255
        """
        rpm = max(0, min(3000, int(rpm)))
        acc = max(0, min(255, int(acc)))
        # F5 frame: [F5, acc_hi, acc_lo, rpm_hi, rpm_lo, axis_31..24, axis_23..16, axis_15..8, axis_7..0]
        acc_hi, acc_lo = (acc >> 8) & 0xFF, acc & 0xFF
        sp_hi, sp_lo = (rpm >> 8) & 0xFF, rpm & 0xFF
        axis = int(axis_counts) & 0xFFFFFFFF
        b4 = (axis >> 24) & 0xFF
        b5 = (axis >> 16) & 0xFF
        b6 = (axis >> 8) & 0xFF
        b7 = axis & 0xFF
        return self._txrx([0xF5, acc_hi, acc_lo, sp_hi, sp_lo, b4, b5, b6, b7])

if __name__ == "__main__":
    # Example usage
    drv = Servo42D(port="COM9", baud=38400, addr=0x01)

    # Safety: enable driver
    print("Enable:", drv.enable(True))

    # Optional: zero current position as home
    print("Zero here:", drv.set_zero_here())

    # Jog CW at 200 rpm, acc=50 for 2 seconds, then decel stop
    print("Jog start:", drv.jog(rpm=200, acc=50, direction=1))
    time.sleep(2.0)
    print("Jog stop:", drv.stop_jog(decel_acc=120))

    # Absolute move to axis = +16000 counts (~1 rev if 16k counts per rev in your gearing)
    print("Move abs:", drv.go_to_position(axis_counts=16000, rpm=300, acc=100))

    # Read status
    print("Status:", drv.query_status())

    # Disable when done
    print("Disable:", drv.enable(False))
