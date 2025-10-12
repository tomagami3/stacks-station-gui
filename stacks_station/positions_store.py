import json, os

_DEFAULTS = {"Table": 0, "1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "6": 0}

class PositionsStore:
    """Each station has its own file: positions_<name>.json"""
    def __init__(self, name: str = "stacks", folder: str = None):
        self.name = name
        base = folder or os.path.dirname(os.path.abspath(__file__))
        self.path = os.path.join(base, f"positions_{name}.json")
        self.data = dict(_DEFAULTS); self.load()

    def load(self):
        try:
            if os.path.isfile(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                for k in _DEFAULTS:
                    if k in raw: self.data[k] = int(raw[k])
        except Exception: pass
        return self.data

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            return True
        except Exception: return False

    def all(self): return dict(self.data)
    def get(self, key, default=0): return int(self.data.get(str(key), default))
    def set(self, key, val): self.data[str(key)] = int(val)
