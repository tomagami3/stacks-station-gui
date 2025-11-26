# Stacks Station GUI - New Features Documentation

## Overview
This document describes the new features added to the Stacks Station GUI application for enhanced shaft assembly control, IO management, alarms, and safety constraints.

## Table of Contents
1. [Shaft Assembly Control](#1-shaft-assembly-control)
2. [Cylinder Control Updates](#2-cylinder-control-updates)
3. [Second IO Card Support](#3-second-io-card-support)
4. [Alarm System](#4-alarm-system)
5. [Constraints Engine](#5-constraints-engine)
6. [Configuration](#6-configuration)

---

## 1. Shaft Assembly Control

### Overview
New dedicated tab for shaft assembly position monitoring and control via Modbus-RTU over COM15.

### Features
- **Live Position Display**: Reads position from COM15 (4800-8N1) using Modbus function 0x04
- **Manual Valve Control**: Momentary buttons for UP (DO01) and DOWN (DO00) cylinders
- **Automatic Go-to-Position**: Set target position and automatic valve control until reached

### Hardware Configuration
- **Port**: COM15
- **Baud Rate**: 4800
- **Data Bits**: 8
- **Stop Bits**: 1
- **Parity**: None
- **Modbus Function**: Read Input Registers (0x04)
- **Register**: Address 0 (first register)

### Software Configuration
Edit `stacks_station/app_config.py`:
```python
SHAFT_PORT = "COM15"
SHAFT_BAUD = 4800
SHAFT_UNIT = 1  # Modbus unit address
```

### Usage
1. Navigate to "Shaft Assembly" tab
2. Click "Connect" to establish serial connection
3. View live position updates
4. Use UP/DOWN buttons for manual control (hold to activate valve)
5. For automatic movement:
   - Enter target position
   - Click "Go to Position"
   - System automatically controls valves until target reached
   - Click "STOP" to abort

### Safety Features
- Timeout protection (30 seconds)
- Position tolerance (±2 counts)
- Manual stop button
- Integration with constraints engine (prevents conflicting valve operations)

---

## 2. Cylinder Control Updates

### Overview
DO00 and DO01 cylinder controls have been updated with swapped mapping and momentary button operation.

### Changes
- **DO00**: Now controls DOWN valve (previously UP)
- **DO01**: Now controls UP valve (previously DOWN)
- **Control Type**: Changed from checkboxes to momentary buttons
- **Behavior**: Hold button to activate valve, release to deactivate

### Location
- Main application: "IO & Stacks %" tab
- Shaft assembly: "Shaft Assembly" tab

### Visual Feedback
- Green button: UP valve (DO01)
- Blue button: DOWN valve (DO00)
- Red background: Valve currently active
- Sunken appearance: Button pressed

---

## 3. Second IO Card Support

### Overview
Extended IO system to support two IO cards for expanded capacity (32 DI, 32 DO).

### Channel Mapping
- **Card 1**: DI00-DI15, DO00-DO15 (addresses 0-15)
- **Card 2**: DI16-DI31, DO16-DO31 (addresses 16-31)

### Hardware Configuration

#### Option 1: Two Separate Cards
1. Configure Card 1:
   - Set Modbus unit address to 1 (via DIP switches or software)
   - Connect to network (e.g., 192.168.1.12)
   
2. Configure Card 2:
   - Set Modbus unit address to 2 (different from Card 1)
   - Connect to same network

#### Option 2: Single Card with Extended Modules
- Single card with 32-channel capacity
- Use contiguous addressing (0-31)
- Configure both software addresses to same unit

### Software Configuration
Edit `stacks_station/app_config.py`:
```python
IO_HOST = "192.168.1.12"           # Card 1 IP
IO_UNIT = 1                         # Card 1 Modbus unit

IO_HOST_CARD2 = "192.168.1.12"     # Card 2 IP (can be same as Card 1)
IO_UNIT_CARD2 = 2                   # Card 2 Modbus unit (or same as Card 1)
```

To disable second card:
```python
IO_UNIT_CARD2 = None
```

### Documentation
See comprehensive hardware configuration notes in `stacks_station/io_worker.py` class docstring.

---

## 4. Alarm System

### Overview
CSV-driven alarm monitoring system that tracks IO operations and verifies sensor responses within timeout periods.

### How It Works
1. Monitors DO (output) state changes
2. When DO changes state, starts timeout timer
3. Checks if expected DI (input) sensors respond
4. If sensor doesn't respond within timeout, triggers alarm
5. Displays active alarms in top bar
6. Click alarm indicator to view details

### Configuration File Format
File: `stacks_station/alarms.csv`

Format: `DOxx,DIxx_when_off,DIxx_when_on,timeout_seconds`

Columns:
1. **DOxx**: Output channel to monitor
2. **DIxx_when_off**: DI sensor that should be active when DO is OFF (blank = not checked)
3. **DIxx_when_on**: DI sensor that should be active when DO is ON (blank = not checked)
4. **timeout_seconds**: Time in seconds before alarm triggers

### Example Configuration
```csv
# Shaft assembly cylinders
# DO00 (down): when ON, expect DI05 within 2 seconds
DO00,,DI05,2.0

# DO01 (up): when OFF, expect DI06 to be inactive within 2 seconds
DO01,DI06,,2.0

# Gripper with both states monitored
DO02,DI07,DI08,1.5
```

### UI Features
- **Top Bar Indicator**: Shows active alarm count
  - Gray: No alarms
  - Red & Bold: Active alarms present
- **Click to View**: Click indicator to open alarm details window
- **Alarm Details**: Shows message, elapsed time, related IO
- **Actions**: Clear all alarms, refresh list

### Adding New Alarms
1. Edit `stacks_station/alarms.csv`
2. Add new line with format: `DOxx,DIxx_off,DIxx_on,timeout`
3. Save file
4. Restart application or reload config (future feature)

---

## 5. Constraints Engine

### Overview
Configuration-driven safety system that validates IO operations before execution to prevent dangerous or conflicting states.

### How It Works
1. Before any DO write operation, checks constraint rules
2. Evaluates all conditions for the requested operation
3. If any condition fails, blocks operation and shows reason
4. If all conditions pass, allows operation

### Configuration File Format
File: `stacks_station/constraints.csv`

Format: `DOxx,target_state,condition_signal,condition_state,...`

Columns:
1. **DOxx**: Target output channel
2. **target_state**: Desired state (0=OFF, 1=ON)
3. **Remaining pairs**: Conditions (signal, required_state)
   - Signal: DOxx or DIxx
   - Required state: 0 (OFF) or 1 (ON)

### Example Configuration
```csv
# Prevent conflicting operations: cannot have both up and down active
# Cannot turn ON DO00 (down) if DO01 (up) is already ON
DO00,1,DO01,0

# Cannot turn ON DO01 (up) if DO00 (down) is already ON
DO01,1,DO00,0

# Safety interlock: cannot activate gripper unless guard is closed
DO02,1,DI13,1

# Multiple conditions: all must be satisfied
DO05,1,DI08,1,DI12,1,DO11,0
```

### UI Feedback
When operation is blocked:
1. Warning dialog shows constraint violation
2. Status bar shows "BLOCKED: [reason]"
3. For checkboxes, state reverts to previous
4. For momentary buttons, valve doesn't activate

### Motor Movement Constraints
The engine includes a placeholder API for motor movement validation:
```python
allowed, reason = constraints_engine.can_move_motor("stacks", "up")
```

This can be extended to check:
- E-stop status
- Guard positions
- Position limits
- Conflicting movements

### Adding New Constraints
1. Edit `stacks_station/constraints.csv`
2. Add rule: `DOxx,state,condition1,state1,condition2,state2,...`
3. Save file
4. Restart application or reload config (future feature)

### Best Practices
- **Mutual Exclusion**: Prevent opposing valves from activating simultaneously
- **Safety Interlocks**: Require guards closed, e-stop released
- **Sequencing**: Ensure operations happen in correct order
- **Position Checks**: Verify position sensors before movement

---

## 6. Configuration

### Main Configuration File
File: `stacks_station/app_config.py`

Key settings:
```python
class Config:
    # IO Card Settings
    IO_HOST = "192.168.1.12"
    IO_UNIT = 1
    IO_HOST_CARD2 = "192.168.1.12"
    IO_UNIT_CARD2 = 2
    
    # Shaft Assembly
    SHAFT_PORT = "COM15"
    SHAFT_BAUD = 4800
    SHAFT_UNIT = 1
    
    # Servo Motors (existing)
    SERVO_PORT = "COM10"
    SERVO_BAUD = 38400
    SERVO_STACKS_UNIT = 1
    SERVO_TORQUE_UNIT = 2
    SERVO_TABLE_UNIT = 3
```

### CSV Configuration Files

#### Alarms Configuration
**File**: `stacks_station/alarms.csv`
**Purpose**: Define IO monitoring rules
**Reload**: Requires restart (future: hot reload)

#### Constraints Configuration
**File**: `stacks_station/constraints.csv`
**Purpose**: Define safety and logic constraints
**Reload**: Requires restart (future: hot reload)

### File Locations
All configuration files are located in the `stacks_station/` directory:
```
stacks_station/
├── app_config.py          # Main configuration
├── alarms.csv             # Alarm rules
├── constraints.csv        # Constraint rules
├── alarm_engine.py        # Alarm system code
├── constraints_engine.py  # Constraints system code
├── shaft_worker.py        # Shaft assembly worker
└── ...
```

---

## Troubleshooting

### Shaft Assembly Connection Issues
- Verify COM15 is available (check Device Manager)
- Ensure no other application is using COM15
- Check Modbus unit address matches hardware
- Verify 4800-8N1 settings on hardware

### Second IO Card Not Responding
- Verify card 2 Modbus unit address is configured correctly
- Check network connectivity
- Ensure IO_UNIT_CARD2 is not None in config
- Check cable connections and power

### Alarms Not Triggering
- Verify alarms.csv file exists and is formatted correctly
- Check DI sensor assignments match hardware
- Ensure timeout values are appropriate for your system
- Review alarm engine startup messages in console

### Operations Being Blocked Unexpectedly
- Check constraints.csv for conflicting rules
- Verify DI sensor states are correct
- Review constraint violation message for details
- Temporarily disable constraints by commenting out rules

### General Issues
- Check console output for error messages
- Verify all CSV files use correct format (no extra commas)
- Ensure pymodbus and pyserial are installed
- Check Python version compatibility (3.7+)

---

## Future Enhancements

### Planned Features
- Hot reload for CSV configuration files
- Visual constraint editor
- Alarm history and logging
- Custom alarm actions (email, sound)
- Constraint rule testing tool
- Enhanced motor movement constraints
- Position-based alarms
- Remote monitoring dashboard

### Contributing
To add new features or report issues, please contact the development team.

---

## Summary

These new features provide:
1. **Enhanced Control**: Dedicated shaft assembly interface with automatic positioning
2. **Improved Safety**: Constraints engine prevents dangerous operations
3. **Better Monitoring**: Alarm system detects and reports IO anomalies
4. **Expanded Capacity**: Second IO card support for larger systems
5. **Flexibility**: CSV-based configuration for easy customization

All features are designed to be:
- **Safe**: Multiple levels of protection and validation
- **Configurable**: CSV files for easy rule management
- **User-Friendly**: Clear feedback and error messages
- **Maintainable**: Well-documented code and behavior
