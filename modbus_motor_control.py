from pymodbus.client.sync import ModbusSerialClient
from pymodbus.payload import BinaryPayloadDecoder, BinaryPayloadBuilder
from pymodbus.constants import Endian

class ModbusMotorController:
    def __init__(self, port, baudrate=9600, stopbits=1, bytesize=8, parity='N', timeout=1):
        self.client = ModbusSerialClient(
            method="rtu",
            port=port,
            baudrate=baudrate,
            stopbits=stopbits,
            bytesize=bytesize,
            parity=parity,
            timeout=timeout
        )

    def connect(self):
        return self.client.connect()

    def close(self):
        self.client.close()

    def decode_registers(self, registers):
        """Decode the received registers into meaningful values."""
        decoder = BinaryPayloadDecoder.fromRegisters(registers, byteorder=Endian.Big)
        carry_value = decoder.decode_32bit_int()  # Decode first two registers as 32-bit integer
        current_value = decoder.decode_16bit_uint()  # Decode third register as 16-bit unsigned integer
        return carry_value, current_value

    def read_input_registers(self):
        """Read input registers from the motor."""
        response = self.client.read_input_registers(address=0x30, count=3, unit=1)  # Replace 0x30 with the correct address
        if response.isError():
            print(f"Read Error: {response}")
            return None
        else:
            carry_value, current_value = self.decode_registers(response.registers)
            print(f"Carry Value: {carry_value}, Current Value: {current_value}")
            return carry_value, current_value

    def write_motor_speed(self, speed_high, speed_low):
        """Write speed command to the motor."""
        builder = BinaryPayloadBuilder(byteorder=Endian.Big)
        builder.add_16bit_uint(speed_high)  # High byte of speed
        builder.add_16bit_uint(speed_low)   # Low byte of speed
        payload = builder.to_registers()

        response = self.client.write_registers(address=0xF6, values=payload, unit=1)
        if response.isError():
            print(f"Write Error: {response}")
        else:
            print("Motor speed command written successfully.")

    def enable_motor_mode(self, mode):
        """Enable a specific motion mode."""
        response = self.client.write_register(address=0x82, value=mode, unit=1)
        if response.isError():
            print(f"Error enabling mode: {response}")
        else:
            print(f"Motor mode {mode} enabled successfully.")

    def move_motor_distance(self, speed, acc, pulses, direction=1):
        """
        Move the motor a specific distance in position mode (relative motion by pulses).
        :param speed: Motor speed (0-3000 RPM).
        :param acc: Acceleration (0-255).
        :param pulses: Number of pulses to move (int, 0-0xFFFFFFFF).
        :param direction: Direction of motion (1 for CW, 0 for CCW).
        """
        builder = BinaryPayloadBuilder(byteorder=Endian.Big)
        direction_bit = (direction << 7)  # MSB of the direction byte
        builder.add_8bit_uint(direction_bit)  # Direction
        builder.add_8bit_uint(acc)  # Acceleration
        builder.add_16bit_uint(speed)  # Speed
        builder.add_32bit_uint(pulses)  # Number of pulses
        payload = builder.to_registers()

        response = self.client.write_registers(address=0xFD, values=payload, unit=1)
        if response.isError():
            print(f"Move Error: {response}")
        else:
            print(f"Motor moving {pulses} pulses at speed {speed} and acceleration {acc}.")

    def stop_motor(self, acc=0):
        """
        Stop the motor in position mode.
        :param acc: Acceleration for stopping (0 for immediate stop, >0 for gradual stop).
        """
        builder = BinaryPayloadBuilder(byteorder=Endian.Big)
        builder.add_8bit_uint(0x00)  # Stop command
        builder.add_8bit_uint(acc)  # Acceleration
        builder.add_16bit_uint(0x0000)  # Speed set to 0
        builder.add_32bit_uint(0xFFFFFFFF)  # Special stop signal
        payload = builder.to_registers()

        response = self.client.write_registers(address=0xFD, values=payload, unit=1)
        if response.isError():
            print(f"Stop Error: {response}")
        else:
            print("Motor stop command sent.")

    def read_register(self, address, count):
        """Read a specific register for debugging."""
        response = self.client.read_holding_registers(address=address, count=count, unit=1)
        if response.isError():
            print(f"Error reading register {address}: {response}")
        else:
            print(f"Register {address}: {response.registers}")

if __name__ == "__main__":
    motor = ModbusMotorController(port="COM4")  # Replace "COM3" with your COM port
    if motor.connect():
        print("Connected to motor.")
        try:
            # Read input registers to verify communication
            motor.read_input_registers()

            # Enable motor mode for distance control (replace with appropriate mode)
            motor.enable_motor_mode(4)

            # Move motor specific distance
            motor.move_motor_distance(speed=600, acc=0, pulses=10000, direction=1)

            # Stop motor after some time
            import time
            time.sleep(2)  # Allow the motor to run for 2 seconds
            motor.stop_motor(acc=2)  # Stop motor gradually

        except Exception as e:
            print(f"An error occurred: {e}")
        finally:
            motor.close()
            print("Connection closed.")
    else:
        print("Failed to connect to motor.")
