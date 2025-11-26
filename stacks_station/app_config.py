class Config:
    # Rates
    IO_POLL_HZ = 25
    MOTOR_POLL_HZ = 25
    CAMERA_SNAPSHOT_HZ = 1  # default; main bumps to ~10 Hz on Stacks tab

    # Motor defaults (same port/baud; different unit addresses)
    SERVO_PORT = "COM10"
    SERVO_BAUD = 38400

    # Stacks axis (existing)
    SERVO_STACKS_UNIT = 1
    # Keep backward-compat name used elsewhere:
    SERVO_UNIT = SERVO_STACKS_UNIT

    # NEW: torque axis
    SERVO_TORQUE_UNIT = 2

    # NEW: main table axis
    SERVO_TABLE_UNIT = 3
    
    # Shaft assembly (COM15, 4800-8N1, Modbus 0x04)
    SHAFT_PORT = "COM15"
    SHAFT_BAUD = 4800
    SHAFT_UNIT = 1

    # IO defaults (can be overridden by your IO module at runtime)
    IO_HOST = "192.168.1.12"
    IO_UNIT = 1
    
    # Second IO card configuration (optional)
    # Set IO_UNIT_CARD2 to None to disable second card
    # If both cards share same network and addressing scheme, set to same as IO_HOST
    IO_HOST_CARD2 = "192.168.1.12"  # Same network by default
    IO_UNIT_CARD2 = 2  # Different Modbus unit address (or None to disable)

    # Cameras
    CAM_MAIN_URL = 0  # will be overridden from cameras/stacks.py if present
    CAM_AUX_URL = "rtsp://192.168.1.101:554/stream1"

    # UI
    WINDOW = "1500x920"
    THEME = "clam"

    # Local phone web UI
    PHONE_PORT = 8088
