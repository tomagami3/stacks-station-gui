# alarm_engine.py
# CSV-driven alarm monitoring for IO operations
# Monitors DO activations and checks DI sensors within timeout periods

import csv
import time
import threading
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AlarmDefinition:
    """
    Definition of an alarm rule from CSV configuration.
    
    CSV format: DOxx,DIxx,DIxx,x
    - Column 0: DOxx - the output being monitored
    - Column 1: DIxx - sensor to check when output is OFF (or blank)
    - Column 2: DIxx - sensor to check when output is ON (or blank)
    - Column 3: x - timeout in seconds
    """
    do_channel: int  # Output channel to monitor
    di_off: Optional[int]  # DI to check when DO is OFF (None if not used)
    di_on: Optional[int]   # DI to check when DO is ON (None if not used)
    timeout: float  # Timeout in seconds
    
    def __str__(self):
        di_off_str = f"DI{self.di_off:02d}" if self.di_off is not None else "—"
        di_on_str = f"DI{self.di_on:02d}" if self.di_on is not None else "—"
        return f"DO{self.do_channel:02d}: OFF→{di_off_str}, ON→{di_on_str}, timeout={self.timeout}s"


@dataclass
class ActiveAlarm:
    """An active alarm that has been triggered."""
    definition: AlarmDefinition
    triggered_time: float
    do_state: bool  # State of DO when alarm triggered
    expected_di: int  # Which DI was expected to change
    message: str
    
    def elapsed_time(self) -> float:
        """Get elapsed time since alarm was triggered."""
        return time.time() - self.triggered_time


class AlarmEngine:
    """
    CSV-driven alarm monitoring engine.
    
    Monitors DO state changes and verifies that corresponding DI sensors
    respond within specified timeout periods.
    """
    
    def __init__(self, csv_path: str = None):
        """
        Initialize alarm engine.
        
        Args:
            csv_path: Path to alarm configuration CSV file.
                     If None, looks for 'alarms.csv' in current directory.
        """
        if csv_path is None:
            csv_path = Path(__file__).parent / "alarms.csv"
        
        self.csv_path = Path(csv_path)
        self.definitions: List[AlarmDefinition] = []
        self.active_alarms: List[ActiveAlarm] = []
        
        # Track DO state changes for timing
        self._do_states: Dict[int, bool] = {}
        self._do_change_times: Dict[int, float] = {}
        
        self._lock = threading.Lock()
        self._running = False
        self._thread = None
        
        # Reference to IO worker (set externally)
        self.io_worker = None
        
        # Load definitions
        self.reload_config()
    
    def reload_config(self):
        """Load or reload alarm definitions from CSV file."""
        with self._lock:
            self.definitions.clear()
            
            if not self.csv_path.exists():
                print(f"Warning: Alarm config not found: {self.csv_path}")
                return
            
            try:
                with open(self.csv_path, 'r', newline='') as f:
                    reader = csv.reader(f)
                    for line_num, row in enumerate(reader, start=1):
                        # Skip empty lines and comments
                        if not row or (row[0].strip().startswith('#')):
                            continue
                        
                        try:
                            alarm_def = self._parse_alarm_row(row)
                            if alarm_def:
                                self.definitions.append(alarm_def)
                        except Exception as e:
                            print(f"Warning: Failed to parse alarm line {line_num}: {e}")
                
                print(f"Loaded {len(self.definitions)} alarm definitions from {self.csv_path}")
                
            except Exception as e:
                print(f"Error loading alarm config: {e}")
    
    def _parse_alarm_row(self, row: List[str]) -> Optional[AlarmDefinition]:
        """
        Parse a CSV row into AlarmDefinition.
        
        Format: DOxx,DIxx,DIxx,timeout
        - DOxx: output channel (required)
        - DIxx: DI when OFF (optional, blank = not checked)
        - DIxx: DI when ON (optional, blank = not checked)
        - timeout: seconds (required)
        """
        if len(row) < 4:
            return None
        
        # Parse DO channel
        do_str = row[0].strip().upper()
        if not do_str.startswith("DO"):
            return None
        do_ch = int(do_str[2:])
        
        # Parse DI when OFF (optional)
        di_off = None
        if row[1].strip():
            di_str = row[1].strip().upper()
            if di_str.startswith("DI"):
                di_off = int(di_str[2:])
        
        # Parse DI when ON (optional)
        di_on = None
        if row[2].strip():
            di_str = row[2].strip().upper()
            if di_str.startswith("DI"):
                di_on = int(di_str[2:])
        
        # Parse timeout
        timeout = float(row[3].strip())
        
        return AlarmDefinition(do_ch, di_off, di_on, timeout)
    
    def start(self, io_worker):
        """Start the alarm monitoring engine."""
        self.io_worker = io_worker
        
        if self._running:
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        print("Alarm engine started")
    
    def stop(self):
        """Stop the alarm monitoring engine."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = None
        print("Alarm engine stopped")
    
    def _monitor_loop(self):
        """Background monitoring loop."""
        while self._running:
            try:
                self._check_alarms()
            except Exception as e:
                print(f"Alarm check error: {e}")
            
            time.sleep(0.1)  # Check every 100ms
    
    def _check_alarms(self):
        """Check all alarm conditions."""
        if not self.io_worker or not self.io_worker.connected:
            return
        
        current_time = time.time()
        
        with self._lock:
            # Check each alarm definition
            for alarm_def in self.definitions:
                do_ch = alarm_def.do_channel
                
                # Get current DO state
                current_state = self.io_worker.get_do(do_ch)
                previous_state = self._do_states.get(do_ch, False)
                
                # Detect state change
                if current_state != previous_state:
                    self._do_states[do_ch] = current_state
                    self._do_change_times[do_ch] = current_time
                    continue  # Just record the change, check on next iteration
                
                # If state hasn't changed, check if we need to alarm
                if do_ch not in self._do_change_times:
                    continue
                
                elapsed = current_time - self._do_change_times[do_ch]
                
                # Check if timeout exceeded
                if elapsed >= alarm_def.timeout:
                    # Determine which DI to check based on DO state
                    if current_state and alarm_def.di_on is not None:
                        # DO is ON, check if DI_ON sensor is active
                        expected_di = alarm_def.di_on
                        di_state = self.io_worker.get_di(expected_di)
                        if not di_state:
                            # Alarm: DO is ON but sensor didn't respond
                            self._trigger_alarm(alarm_def, current_state, expected_di,
                                              f"DO{do_ch:02d} ON but DI{expected_di:02d} not active after {alarm_def.timeout}s")
                    
                    elif not current_state and alarm_def.di_off is not None:
                        # DO is OFF, check if DI_OFF sensor is inactive
                        expected_di = alarm_def.di_off
                        di_state = self.io_worker.get_di(expected_di)
                        if di_state:
                            # Alarm: DO is OFF but sensor still active
                            self._trigger_alarm(alarm_def, current_state, expected_di,
                                              f"DO{do_ch:02d} OFF but DI{expected_di:02d} still active after {alarm_def.timeout}s")
                    
                    # Clear the change time so we don't repeatedly alarm
                    del self._do_change_times[do_ch]
    
    def _trigger_alarm(self, definition: AlarmDefinition, do_state: bool, 
                      expected_di: int, message: str):
        """Trigger a new alarm."""
        # Check if this alarm is already active
        for alarm in self.active_alarms:
            if (alarm.definition.do_channel == definition.do_channel and
                alarm.do_state == do_state):
                return  # Already alarming
        
        alarm = ActiveAlarm(
            definition=definition,
            triggered_time=time.time(),
            do_state=do_state,
            expected_di=expected_di,
            message=message
        )
        
        self.active_alarms.append(alarm)
        print(f"ALARM TRIGGERED: {message}")
    
    def acknowledge_alarm(self, alarm: ActiveAlarm):
        """Acknowledge and remove an alarm."""
        with self._lock:
            if alarm in self.active_alarms:
                self.active_alarms.remove(alarm)
    
    def clear_all_alarms(self):
        """Clear all active alarms."""
        with self._lock:
            self.active_alarms.clear()
    
    def get_active_alarms(self) -> List[ActiveAlarm]:
        """Get list of currently active alarms."""
        with self._lock:
            return self.active_alarms.copy()


# Example alarm configuration CSV format:
"""
# Alarm configuration
# Format: DOxx,DIxx_when_off,DIxx_when_on,timeout_seconds
# Leave DI columns blank if not checking that state

# Shaft assembly cylinder
DO00,,DI05,2.0
DO01,DI06,,2.0

# Grippers
DO02,DI07,DI08,1.5
DO03,DI09,DI10,1.5

# Example: DO04 should cause DI11 to turn OFF within 3 seconds
DO04,DI11,,3.0
"""
