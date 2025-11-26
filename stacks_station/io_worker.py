import threading
from typing import List, Tuple

_io_import_err = None
try:
    import manual_io as io_mod
except Exception as e1:
    try:
        import maunal_io as io_mod
    except Exception as e2:
        _io_import_err = (e1, e2)
        io_mod = None


def get_do_list() -> List[Tuple[int, str]]:
    """Collect a de-duplicated (ch, name) list from your IO module mappings."""
    lst = []
    try:
        for group in [io_mod.SHAFT_ASSEMBLY, io_mod.GENERAL_STATION, io_mod.STATION_STACKS]:
            lst.extend(group)
        if hasattr(io_mod, "TORQUE_STATION"):
            lst.extend(io_mod.TORQUE_STATION)
    except Exception:
        pass
    seen = set()
    out = []
    for ch, name in lst:
        if ch in seen:
            continue
        seen.add(ch)
        out.append((ch, name))
    return out


class IOWorker:
    """
    Fast-scan IO using your MT3A client.
    
    Supports two IO cards:
    - Card 1: DI00-DI15, DO00-DO15 (addresses 0-15)
    - Card 2: DI16-DI31, DO16-DO31 (addresses 16-31)
    
    Hardware configuration for second card:
    -----------------------------------------
    To enable the second IO card, configure it with a different Modbus unit address:
    
    1. For MT3A-IO1632 cards, the unit address is typically set via DIP switches or
       configuration software. Consult the hardware manual for your specific model.
    
    2. Common configurations:
       - Card 1 (primary): Unit address = 1, handles DO00-DO15, DI00-DI15
       - Card 2 (secondary): Unit address = 2, handles DO16-DO31, DI16-DI31
    
    3. If using a single card with extended modules, the addressing may be contiguous
       (addresses 0-31) on the same unit. In this case, set both cards to the same
       unit address in Config.
    
    4. Network setup:
       - Both cards should be on the same Ethernet/Modbus network
       - Use the same host IP (e.g., 192.168.1.12) for both if on same network
       - Or configure Config.IO_HOST_CARD2 if cards are on different networks
    
    Software configuration:
    ------------------------
    Set in app_config.py:
    - Config.IO_UNIT: Unit address for card 1 (default: 1)
    - Config.IO_UNIT_CARD2: Unit address for card 2 (default: 2, or same as card 1)
    - Config.IO_HOST: IP address for primary card
    - Config.IO_HOST_CARD2: IP address for secondary card (optional, defaults to IO_HOST)
    """
    
    def __init__(self, host: str, unit: int, poll_hz: float = 25,
                 host_card2: str = None, unit_card2: int = None):
        if io_mod is None:
            raise RuntimeError(f"IO module not importable: {(_io_import_err or '')}")
        
        self.host = host
        self.unit = unit
        self.poll_period = 1.0 / max(1.0, poll_hz)
        
        # Second card configuration
        self.host_card2 = host_card2 or host  # Default to same host
        self.unit_card2 = unit_card2 if unit_card2 is not None else (unit + 1)  # Default to unit+1

        self.cli = io_mod.MT3AClient(host=host, unit=unit)
        # Second card client (if different unit or host)
        self.cli_card2 = None
        if self.unit_card2 != self.unit or self.host_card2 != self.host:
            self.cli_card2 = io_mod.MT3AClient(host=self.host_card2, unit=self.unit_card2)
        
        self.connected = False
        self.DO = {}  # Now supports 0-31
        self.DI = {}  # Now supports 0-31
        self.status_text = "Disconnected"

        self._run = False
        self._thread = None
        self.lock = threading.Lock()
        
        # Constraints engine (set externally)
        self.constraints_engine = None

    def connect(self):
        self.cli.host = self.host
        self.cli.unit = self.unit
        self.cli.connect()
        
        # Connect second card if configured
        if self.cli_card2:
            try:
                self.cli_card2.host = self.host_card2
                self.cli_card2.unit = self.unit_card2
                self.cli_card2.connect()
                self.status_text = f"Connected card1={self.host}:502/u{self.unit}, card2={self.host_card2}:502/u{self.unit_card2}"
            except Exception as e:
                self.status_text = f"Card1 OK, Card2 failed: {e}"
        else:
            self.status_text = f"Connected {self.host}:502 unit={self.unit}"
        
        self.connected = True
        import threading
        self._run = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def disconnect(self):
        self._run = False
        if self._thread:
            self._thread.join(timeout=1.0)
        try:
            self.cli.close()
        except Exception:
            pass
        if self.cli_card2:
            try:
                self.cli_card2.close()
            except Exception:
                pass
        self.connected = False
        self.status_text = "Disconnected"

    def _poll_loop(self):
        import time
        while self._run and self.connected:
            # Read card 1 (DO00-DO15, DI00-DI15)
            try:
                st = self.cli.read_do(count=16)
                with self.lock:
                    for i, v in enumerate(st):
                        self.DO[i] = bool(v)
                try:
                    di_st = self.cli.read_di(count=16)
                    with self.lock:
                        for i, v in enumerate(di_st):
                            self.DI[i] = bool(v)
                except Exception:
                    pass
            except Exception:
                pass
            
            # Read card 2 (DO16-DO31, DI16-DI31) if configured
            if self.cli_card2:
                try:
                    st2 = self.cli_card2.read_do(count=16)
                    with self.lock:
                        for i, v in enumerate(st2):
                            self.DO[16 + i] = bool(v)
                    try:
                        di_st2 = self.cli_card2.read_di(count=16)
                        with self.lock:
                            for i, v in enumerate(di_st2):
                                self.DI[16 + i] = bool(v)
                    except Exception:
                        pass
                except Exception:
                    pass
            
            time.sleep(self.poll_period)

    def write_do(self, ch: int, val: bool, skip_constraints: bool = False):
        """
        Write digital output.
        - ch 0-15: Card 1
        - ch 16-31: Card 2 (if configured)
        
        Args:
            ch: Channel number (0-31)
            val: Desired state (True=ON, False=OFF)
            skip_constraints: If True, skip constraint checking (for internal use)
        
        Raises:
            RuntimeError: If constraints are violated or card not configured
        """
        # Check constraints before writing (unless skipped)
        if not skip_constraints and self.constraints_engine:
            allowed, reason = self.constraints_engine.can_execute_operation(ch, bool(val))
            if not allowed:
                raise RuntimeError(f"Operation blocked by constraints: {reason}")
        
        # Perform the write
        if ch < 16:
            self.cli.write_do(ch, bool(val))
        elif self.cli_card2:
            # Write to card 2, adjusting address
            self.cli_card2.write_do(ch - 16, bool(val))
        else:
            raise RuntimeError(f"DO{ch:02d} requires second card, but card 2 not configured")
        
        with self.lock:
            self.DO[ch] = bool(val)

    def get_do(self, ch: int) -> bool:
        with self.lock:
            return bool(self.DO.get(ch, False))

    def get_di(self, ch: int) -> bool:
        with self.lock:
            return bool(self.DI.get(ch, False))

    @property
    def setup_password(self) -> str:
        return getattr(io_mod, "SETUP_PASSWORD", "2468")
