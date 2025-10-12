import json
import os

# _DEFAULTS = {
#     "half_step_counts": 5000,
#     "full_step_counts": 10000,
#     "slow_jog_rpm": 10,
#     "slow_jog_step": 200,
#     "acc": 80,
#     "speed_rpm": 300,
#     "leave_do_on_exit": True,
#     # NEW:
#     "camera_enabled": True,        # Desktop toggle – when False, camera threads are off
#     "half_auto_enabled": False,    # Web toggle – half-auto on the Stacks page
# }

_DEFAULTS = {
    "half_step_counts": 46080,
    "full_step_counts": 92160,
    "slow_jog_rpm": 100,
    "slow_jog_step": 1000,
    "acc": 100,
    "speed_rpm": 300,
    "leave_do_on_exit": True,
    "camera_enabled": True,
    "half_auto_enabled": False,

    # Half-auto tuning
    "semi_tol_counts": 20,
    "semi_wait_ms": 1200,
    "semi_timeout1_s": 10,
    "semi_timeout2_s": 10,
    "semi_speed_rpm": 300,

    # NEW: table “station” step distance
    "station_step_counts": 46080,   # one station = 46080 pulses (Table motor)
}


class SettingsStore:
    def __init__(self, path=None):
        if path is None:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_settings.json")
        self.path = path
        self.data = dict(_DEFAULTS)
        self.load()

    def load(self):
        try:
            if os.path.isfile(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                for k in _DEFAULTS:
                    if k in raw:
                        self.data[k] = raw[k]
        except Exception:
            pass
        return self.data

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            return True
        except Exception:
            return False

    def get(self, key, default=None):
        return self.data.get(key, _DEFAULTS.get(key, default))

    def set(self, key, value):
        self.data[key] = value
