"""
MKS SERVO RS485 quick test (auto-detects address keyword: device_id / unit / slave)

Edit CONFIG below and run. It will:
  1) read encoder registers of UNIT
  2) optionally jog that UNIT by DELTA counts

Requires: pip install pymodbus
"""

import inspect
from time import sleep
from pymodbus.client import ModbusSerialClient

# ===================== CONFIG =====================
PORT  = "COM10"   # serial port
BAUD  = 38400     # baud
UNIT  = 2         # which motor address to target (1/2/3)
DELTA = -2000      # relative jog counts (+ forward, - reverse)
SPEED = 300       # rpm (0..3000)
ACC   = 80        # accel (0..255)
DO_JOG = True     # set False to only read
TIMEOUT = 0.3     # seconds
# ==================================================

# Device registers (per MKS docs)
REG_ENCODER_BASE = 0x0030   # input regs: [carry_hi, carry_lo, value]
REG_MOVE_BLOCK   = 0x00F4   # write [acc, speed_rpm, pos_hi, pos_lo]

def int32_to_regs(val: int):
    if val < 0:
        val = (1 << 32) + val
    return [(val >> 16) & 0xFFFF, val & 0xFFFF]

def regs_to_int32(hi: int, lo: int) -> int:
    x = ((hi & 0xFFFF) << 16) | (lo & 0xFFFF)
    if x & 0x80000000:
        x -= (1 << 32)
    return x

def detect_addr_kw(client: ModbusSerialClient) -> str:
    """
    Return the keyword used for the Modbus device address on this pymodbus build.
    Newer versions use 'device_id' (keyword-only), older use 'unit' or 'slave'.
    """
    for method in (client.read_input_registers, client.write_registers, client.write_register):
        params = inspect.signature(method).parameters
        for key in ("device_id", "unit", "slave"):
            if key in params:
                return key
    raise RuntimeError("This pymodbus client exposes none of: device_id / unit / slave")

def open_client():
    cli = ModbusSerialClient(port=PORT, baudrate=BAUD, parity="N", stopbits=1, bytesize=8, timeout=TIMEOUT)
    if not cli.connect():
        raise RuntimeError(f"❌ Failed opening {PORT} @ {BAUD}")
    print(f"✅ Connected to {PORT} @ {BAUD}")
    return cli

def read_encoder(cli, addr_kw: str, unit: int):
    kw = {addr_kw: unit}
    rr = cli.read_input_registers(address=REG_ENCODER_BASE, count=3, **kw)
    if rr.isError():
        raise RuntimeError(f"Unit {unit}: read_encoder failed ({rr})")
    regs = rr.registers
    carry = regs_to_int32(regs[0], regs[1])
    fine  = regs[2]
    return carry, fine, regs

def jog(cli, addr_kw: str, unit: int, delta: int, speed_rpm: int, acc: int):
    acc = max(0, min(255, int(acc)))
    speed_rpm = max(0, min(3000, int(speed_rpm)))
    payload = [acc, speed_rpm] + int32_to_regs(int(delta))
    kw = {addr_kw: unit}
    wr = cli.write_registers(address=REG_MOVE_BLOCK, values=payload, **kw)
    if wr.isError():
        raise RuntimeError(f"Unit {unit}: jog failed ({wr})")

def main():
    cli = open_client()
    try:
        addr_kw = detect_addr_kw(cli)  # 'device_id' | 'unit' | 'slave'
        # --- read before move ---
        c, f, regs = read_encoder(cli, addr_kw, UNIT)
        print(f"Unit {UNIT}: encoder={regs}  carry={c}  fine={f}")

        if DO_JOG:
            print(f"➡️  Jogging unit {UNIT} by {DELTA} counts @ {SPEED}rpm acc={ACC} ...")
            jog(cli, addr_kw, UNIT, DELTA, SPEED, ACC)
            sleep(0.3)
            c2, f2, regs2 = read_encoder(cli, addr_kw, UNIT)
            print(f"After jog: encoder={regs2}  carry={c2}  fine={f2}")
        else:
            print("Skipping jog (DO_JOG=False).")
    except Exception as e:
        print("❌ Error:", e)
    finally:
        cli.close()
        print("🔌 Closed connection.")

if __name__ == "__main__":
    main()
