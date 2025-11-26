# constraints_engine.py
# Configuration-driven constraints engine for IO and motor operations
# Validates requested operations against safety and logical constraints

import csv
import threading
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ConstraintRule:
    """
    A constraint rule that must be satisfied before an operation can execute.
    
    CSV format: DOxx,0/1,DOxx/DIxx,0/1,DOxx/DIxx,0/1,...
    - First DOxx: target output
    - First 0/1: desired state for target (0=OFF, 1=ON)
    - Remaining pairs: conditions that must all be satisfied
      - DOxx/DIxx: signal to check
      - 0/1: required state (0=OFF/inactive, 1=ON/active)
    
    Example: DO05,1,DI08,1,DO12,0
    Means: "To turn ON DO05, DI08 must be ON and DO12 must be OFF"
    """
    target_do: int  # The DO being controlled
    target_state: bool  # The desired state (True=ON, False=OFF)
    conditions: List[Tuple[str, int, bool]]  # List of (type, channel, required_state)
    # type is "DO" or "DI", channel is the number, required_state is True/False
    
    def __str__(self):
        state_str = "ON" if self.target_state else "OFF"
        cond_strs = []
        for sig_type, ch, req_state in self.conditions:
            state = "ON" if req_state else "OFF"
            cond_strs.append(f"{sig_type}{ch:02d}={state}")
        conds = ", ".join(cond_strs) if cond_strs else "none"
        return f"DO{self.target_do:02d}→{state_str} requires: {conds}"


class ConstraintsEngine:
    """
    Configuration-driven constraints engine.
    
    Validates IO operations and motor movements against safety rules
    defined in a CSV configuration file.
    """
    
    def __init__(self, csv_path: str = None):
        """
        Initialize constraints engine.
        
        Args:
            csv_path: Path to constraints configuration CSV file.
                     If None, looks for 'constraints.csv' in current directory.
        """
        if csv_path is None:
            csv_path = Path(__file__).parent / "constraints.csv"
        
        self.csv_path = Path(csv_path)
        self.rules: List[ConstraintRule] = []
        self._lock = threading.Lock()
        
        # Reference to IO worker (set externally)
        self.io_worker = None
        
        # Load rules
        self.reload_config()
    
    def reload_config(self):
        """Load or reload constraint rules from CSV file."""
        with self._lock:
            self.rules.clear()
            
            if not self.csv_path.exists():
                print(f"Warning: Constraints config not found: {self.csv_path}")
                return
            
            try:
                with open(self.csv_path, 'r', newline='') as f:
                    reader = csv.reader(f)
                    for line_num, row in enumerate(reader, start=1):
                        # Skip empty lines and comments
                        if not row or (row[0].strip().startswith('#')):
                            continue
                        
                        try:
                            rule = self._parse_constraint_row(row)
                            if rule:
                                self.rules.append(rule)
                        except Exception as e:
                            print(f"Warning: Failed to parse constraint line {line_num}: {e}")
                
                print(f"Loaded {len(self.rules)} constraint rules from {self.csv_path}")
                
            except Exception as e:
                print(f"Error loading constraints config: {e}")
    
    def _parse_constraint_row(self, row: List[str]) -> Optional[ConstraintRule]:
        """
        Parse a CSV row into ConstraintRule.
        
        Format: DOxx,0/1,DOxx/DIxx,0/1,DOxx/DIxx,0/1,...
        - First pair: target DO and desired state
        - Remaining pairs: conditions (signal and required state)
        """
        if len(row) < 2:
            return None
        
        # Parse target DO and state
        target_str = row[0].strip().upper()
        if not target_str.startswith("DO"):
            return None
        target_do = int(target_str[2:])
        target_state = bool(int(row[1].strip()))
        
        # Parse conditions (pairs of signal,state)
        conditions = []
        for i in range(2, len(row), 2):
            if i + 1 >= len(row):
                break  # Incomplete pair
            
            sig_str = row[i].strip().upper()
            if not sig_str:
                continue
            
            state_str = row[i + 1].strip()
            if not state_str:
                continue
            
            # Determine signal type (DO or DI)
            if sig_str.startswith("DO"):
                sig_type = "DO"
                sig_ch = int(sig_str[2:])
            elif sig_str.startswith("DI"):
                sig_type = "DI"
                sig_ch = int(sig_str[2:])
            else:
                continue  # Invalid signal type
            
            req_state = bool(int(state_str))
            conditions.append((sig_type, sig_ch, req_state))
        
        return ConstraintRule(target_do, target_state, conditions)
    
    def set_io_worker(self, io_worker):
        """Set the IO worker reference for reading current states."""
        self.io_worker = io_worker
    
    def can_execute_operation(self, target_do: int, target_state: bool) -> Tuple[bool, str]:
        """
        Check if a DO operation can be executed.
        
        Args:
            target_do: The DO channel to control
            target_state: The desired state (True=ON, False=OFF)
        
        Returns:
            Tuple of (allowed: bool, reason: str)
            - If allowed=True, reason is empty
            - If allowed=False, reason explains why
        """
        if not self.io_worker:
            # No IO worker set, allow operation
            return True, ""
        
        with self._lock:
            # Find matching rules
            matching_rules = [
                rule for rule in self.rules
                if rule.target_do == target_do and rule.target_state == target_state
            ]
            
            if not matching_rules:
                # No constraints defined for this operation
                return True, ""
            
            # Check each matching rule
            for rule in matching_rules:
                # All conditions must be satisfied
                failed_conditions = []
                
                for sig_type, sig_ch, req_state in rule.conditions:
                    if sig_type == "DO":
                        current_state = self.io_worker.get_do(sig_ch)
                    else:  # DI
                        current_state = self.io_worker.get_di(sig_ch)
                    
                    if current_state != req_state:
                        state_str = "ON" if req_state else "OFF"
                        actual_str = "ON" if current_state else "OFF"
                        failed_conditions.append(
                            f"{sig_type}{sig_ch:02d} must be {state_str} (currently {actual_str})"
                        )
                
                if failed_conditions:
                    # This rule is not satisfied
                    reason = f"Constraint violation: {'; '.join(failed_conditions)}"
                    return False, reason
            
            # All rules satisfied
            return True, ""
    
    def can_move_motor(self, motor_name: str, direction: str = None) -> Tuple[bool, str]:
        """
        Check if a motor movement can be executed.
        
        This is a placeholder for motor-specific constraints.
        Can be extended to check:
        - Safety inputs (e-stop, guards)
        - Position limits
        - Conflicting movements
        
        Args:
            motor_name: Name of the motor (e.g., "stacks", "torque", "table")
            direction: Optional direction ("up", "down", "cw", "ccw", etc.)
        
        Returns:
            Tuple of (allowed: bool, reason: str)
        """
        # Example: Check e-stop or safety inputs
        # This would need to be customized based on actual DI assignments
        
        if not self.io_worker:
            return True, ""
        
        # Example safety check: Check if e-stop is active (assume DI15 = e-stop)
        # Customize this based on actual hardware configuration
        try:
            # This is an example - adjust based on actual DI assignments
            # estop_active = self.io_worker.get_di(15)
            # if estop_active:
            #     return False, "E-stop is active"
            pass
        except Exception:
            pass
        
        # Allow by default
        return True, ""
    
    def get_all_rules(self) -> List[ConstraintRule]:
        """Get all loaded constraint rules."""
        with self._lock:
            return self.rules.copy()


# Example constraints configuration CSV format:
"""
# Constraints configuration
# Format: DOxx,target_state,condition1_signal,condition1_state,condition2_signal,condition2_state,...
# - DOxx: target output channel
# - target_state: 0 (OFF) or 1 (ON)
# - Remaining pairs: conditions that must ALL be satisfied
#   - Signal: DOxx or DIxx
#   - State: 0 (OFF/inactive) or 1 (ON/active)

# Example: To turn ON DO05, DI08 must be ON and DO12 must be OFF
DO05,1,DI08,1,DO12,0

# Example: To turn OFF DO10, DO11 must be OFF
DO10,0,DO11,0

# Motor safety: Cannot activate table gripper (DO04) unless table is in position (DI04)
DO04,1,DI04,1

# Prevent conflicting operations: Cannot have both up and down active
DO00,1,DO01,0
DO01,1,DO00,0

# Safety interlocks (examples - customize for actual hardware):
# Cannot activate grippers unless guards are closed
# DO02,1,DI12,1
# DO05,1,DI12,1
"""
