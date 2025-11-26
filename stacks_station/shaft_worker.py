# shaft_worker.py
# Worker for shaft assembly position reading via Modbus-RTU over COM15
# Reads position using Read Input Registers (0x04) from register 0

import threading
import time
from typing import Optional
from pymodbus.client import ModbusSerialClient


class ShaftAssemblyWorker:
    """
    Shaft assembly position reader via Modbus-RTU serial.
    - Port: COM15
    - Baud: 4800
    - Data: 8N1
    - Function: Read Input Registers (0x04)
    - Register: 0 (first register)
    """
    
    def __init__(self, port="COM15", baudrate=4800, unit=1, poll_hz=10):
        self.port = str(port)
        self.baudrate = int(baudrate)
        self.unit = int(unit)
        self.poll_period = 1.0 / max(1.0, poll_hz)
        
        self.client: Optional[ModbusSerialClient] = None
        self.connected = False
        self.status_text = "Disconnected"
        
        # Position value (ushort from register 0)
        self.position = 0
        
        self._run = False
        self._poll_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
    
    # ----- lifecycle -----
    def connect(self):
        """Connect to the shaft assembly serial port."""
        if self.connected:
            return
        
        try:
            self.client = ModbusSerialClient(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=8,
                parity='N',
                stopbits=1,
                timeout=1.0
            )
            
            if not self.client.connect():
                raise RuntimeError(f"Failed to open {self.port} @ {self.baudrate}")
            
            self.connected = True
            self.status_text = f"Connected {self.port}@{self.baudrate} unit={self.unit}"
            self.resume_poll()
            
        except Exception as e:
            self.connected = False
            self.status_text = f"Connect failed: {e}"
            raise
    
    def disconnect(self):
        """Disconnect from the shaft assembly."""
        self.pause_poll()
        
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None
        
        self.connected = False
        self.status_text = "Disconnected"
    
    # ----- polling control -----
    def resume_poll(self):
        """Start or resume polling in background thread."""
        if self._poll_thread and self._poll_thread.is_alive():
            self._run = True
            return
        
        self._run = True
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()
    
    def pause_poll(self):
        """Pause polling thread."""
        self._run = False
        if self._poll_thread:
            self._poll_thread.join(timeout=2.0)
        self._poll_thread = None
    
    # ----- polling -----
    def _poll_loop(self):
        """Background polling loop to read position."""
        while self._run and self.connected:
            try:
                self._read_position()
            except Exception as e:
                # Silent failure in poll loop - status remains last good value
                pass
            
            time.sleep(self.poll_period)
    
    def _read_position(self):
        """
        Read position value from register 0 using Read Input Registers (0x04).
        Stores result as unsigned 16-bit integer (ushort).
        """
        if not self.client:
            return
        
        try:
            # Read Input Registers (function 0x04), address 0, count 1
            # pymodbus auto-detects unit/slave parameter
            result = None
            try:
                result = self.client.read_input_registers(address=0, count=1, unit=self.unit)
            except TypeError:
                # Try with slave parameter for older pymodbus versions
                try:
                    result = self.client.read_input_registers(address=0, count=1, slave=self.unit)
                except TypeError:
                    # Try setting unit_id attribute
                    self.client.unit_id = self.unit
                    result = self.client.read_input_registers(address=0, count=1)
            
            if result and not result.isError():
                with self._lock:
                    # Store as ushort (0-65535)
                    self.position = result.registers[0] & 0xFFFF
            else:
                # Error reading - keep last value
                pass
                
        except Exception as e:
            # Error reading - keep last value
            pass
    
    def read_position_now(self) -> int:
        """Get the current position value (blocking read)."""
        if not self.connected:
            return self.position
        
        try:
            self._read_position()
        except Exception:
            pass
        
        with self._lock:
            return self.position
    
    def get_position(self) -> int:
        """Get the last polled position value (non-blocking)."""
        with self._lock:
            return self.position
