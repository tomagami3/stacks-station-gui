import tkinter as tk
from pymodbus.client.sync import ModbusSerialClient as ModbusClient

# Connection parameters
MODBUS_PORT = 'COM4'
MODBUS_BAUDRATE = 57600
MODBUS_STOPBITS = 1
MODBUS_PARITY = 'N'
MODBUS_UNIT_ID = 1

class ServoGUI:
    def __init__(self, master):
        self.master = master
        master.title("Servo Modbus GUI")

        self.client = ModbusClient(
            method='rtu',
            port=MODBUS_PORT,
            baudrate=MODBUS_BAUDRATE,
            stopbits=MODBUS_STOPBITS,
            parity=MODBUS_PARITY,
            timeout=1
        )

        self.label = tk.Label(master, text="Modbus Servo Reader", font=("Arial", 14))
        self.label.pack(pady=10)

        self.read_button = tk.Button(master, text="Read Registers", command=self.read_registers)
        self.read_button.pack(pady=5)

        # self.result_text = tk.Text(master, height=4, width=30)
        self.result_text = tk.Text(master, height=50, width=40)
        self.result_text.pack(pady=5)

        self.connect_status = tk.Label(master, text="Not connected", fg="red")
        self.connect_status.pack()

        self.connect_button = tk.Button(master, text="Connect", command=self.connect)
        self.connect_button.pack(pady=5)

        self.disconnect_button = tk.Button(master, text="Disconnect", command=self.disconnect)
        self.disconnect_button.pack(pady=5)

    def connect(self):
        if self.client.connect():
            self.connect_status.config(text="Connected", fg="green")
        else:
            self.connect_status.config(text="Connection Failed", fg="red")

    def disconnect(self):
        self.client.close()
        self.connect_status.config(text="Disconnected", fg="red")

    def read_registers(self):
        if not self.client.connect():
            self.connect_status.config(text="Connection Failed", fg="red")
            return

        address = 0x0000
        count = 50  # Read 10 registers
        result = self.client.read_holding_registers(address=address, count=count, unit=MODBUS_UNIT_ID)
        self.result_text.delete("1.0", tk.END)

        if result.isError():
            self.result_text.insert(tk.END, "Read failed.")
        else:
            for i, val in enumerate(result.registers):
                self.result_text.insert(tk.END, f"Register {address + i}: {val}\n")

        self.client.close()


if __name__ == "__main__":
    root = tk.Tk()
    gui = ServoGUI(root)
    root.mainloop()
