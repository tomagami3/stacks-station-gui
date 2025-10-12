import cv2
import numpy as np
import json
import time
import threading
from pathlib import Path

# ---------- CONFIG ----------
STREAM_URL = "rtsp://192.168.1.104:554/stream1"  # <-- change if needed
SETTINGS_PATH = Path("ipcam_fill_gauge_settings.json")
WINDOW = "Saffron Fill Gauge (Line Mode)"
MAX_LINES = 6
# ----------------------------

def load_settings():
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text())
        except Exception:
            pass
    return {
        "rotate_ccw90": True,
        "roi": None,              # [x,y,w,h] on RAW frame
        "lines_norm": []          # each: [x0,y0,x1,y1] normalized to [0..1] in cropped+rotated view
    }

def save_settings(cfg):
    SETTINGS_PATH.write_text(json.dumps(cfg, indent=2))

class FastCam:
    def __init__(self, url, width=None, height=None):
        self.url = url
        self.cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if width:  self.cap.set(cv2.CAP_PROP_FRAME_WIDTH,  width)
        if height: self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.frame = None
        self.lock = threading.Lock()
        self.running = False
        self.ok = False

    def start(self):
        self.running = True
        t = threading.Thread(target=self._reader, daemon=True)
        t.start()
        for _ in range(50):
            if self.frame is not None: break
            time.sleep(0.02)
        return self

    def _reader(self):
        while self.running:
            ok, f = self.cap.read()
            if not ok:
                self.ok = False
                time.sleep(0.05)
                continue
            with self.lock:
                self.frame = f
                self.ok = True

    def read(self):
        with self.lock:
            f = None if self.frame is None else self.frame.copy()
        return self.ok and f is not None, f

    def stop(self):
        self.running = False
        time.sleep(0.05)
        try: self.cap.release()
        except Exception: pass

def select_roi_interactive(img, initial=None):
    r = cv2.selectROI("Select ROI (ENTER=OK)", img, fromCenter=False, showCrosshair=True)
    cv2.destroyWindow("Select ROI (ENTER=OK)")
    if r == (0,0,0,0): return None
    return list(map(int, r))

def get_cropped_rotated(frame, roi, rotate_ccw90):
    if roi:
        x,y,w,h = roi
        x2 = min(x+w, frame.shape[1])
        y2 = min(y+h, frame.shape[0])
        crop = frame[y:y2, x:x2]
    else:
        crop = frame
    if rotate_ccw90:
        crop = cv2.rotate(crop, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return crop

# ---------- LINE HELPERS ----------
def denorm_line(line_norm, W, H):
    x0 = int(np.clip(line_norm[0]*W, 0, max(W-1,0)))
    y0 = int(np.clip(line_norm[1]*H, 0, max(H-1,0)))
    x1 = int(np.clip(line_norm[2]*W, 0, max(W-1,0)))
    y1 = int(np.clip(line_norm[3]*H, 0, max(H-1,0)))
    return (x0, y0, x1, y1)

def norm_line(px_line, W, H):
    x0,y0,x1,y1 = px_line
    Wm = max(W-1, 1); Hm = max(H-1, 1)
    return [x0/Wm, y0/Hm, x1/Wm, y1/Hm]

def order_top_to_bottom(x0,y0,x1,y1):
    return (x0,y0,x1,y1) if y0 <= y1 else (x1,y1,x0,y0)

def sample_line_profile(gray, x0,y0,x1,y1, num_samples=None):
    dy = y1 - y0
    dx = x1 - x0
    length = float(np.hypot(dx, dy))
    if num_samples is None:
        num_samples = max(10, int(length))  # ~1px spacing
    ts = np.linspace(0, 1, num_samples).astype(np.float32)
    xs = x0 + ts * dx
    ys = y0 + ts * dy
    map_x = xs.reshape(1, -1)
    map_y = ys.reshape(1, -1)
    prof = cv2.remap(gray, map_x, map_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    prof = prof.reshape(-1).astype(np.float32)
    pts = np.stack([xs, ys], axis=1)
    return prof, pts

def strongest_gradient_idx(profile):
    k = 9 if profile.size >= 9 else (profile.size | 1)
    if k >= 3:
        sm = cv2.GaussianBlur(profile.reshape(-1,1), (1,k), 0).ravel()
    else:
        sm = profile
    g = np.gradient(sm)
    idx = int(np.argmax(np.abs(g)))
    return idx, float(sm[idx]), float(g[idx])

def draw_lines_and_hits(view, lines_px, hits):
    out = view.copy()
    for i, ((x0,y0,x1,y1), hit) in enumerate(zip(lines_px, hits), start=1):
        cv2.line(out, (x0,y0), (x1,y1), (0,255,0), 2, cv2.LINE_AA)
        cv2.circle(out, (x0,y0), 4, (0,200,255), -1, cv2.LINE_AA)
        cv2.circle(out, (x1,y1), 4, (0,200,255), -1, cv2.LINE_AA)
        if hit is not None:
            (xh, yh), idx, grad = hit
            cv2.circle(out, (int(xh), int(yh)), 6, (0,0,255), 2, cv2.LINE_AA)
            # distance from bottom endpoint along the segment
            xb, yb = (x1,y1) if y1 >= y0 else (x0,y0)
            dist_from_bottom = int(np.hypot(xb - xh, yb - yh))
            txt = f"L{i}: dBtm={dist_from_bottom}px grad={grad:+.1f}"
            cv2.putText(out, txt, (min(x0,x1)+6, min(y0,y1)-8 if i<=3 else min(y0,y1)+18*i//2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255,255,255), 2, cv2.LINE_AA)
            cv2.putText(out, txt, (min(x0,x1)+6, min(y0,y1)-8 if i<=3 else min(y0,y1)+18*i//2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,0,0), 1, cv2.LINE_AA)
    return out

# ---------- INTERACTIVE STATE ----------
class Editor:
    def __init__(self):
        self.edit_mode = False
        self.pending = []        # clicks [(x,y)]
        self.lines_norm = []     # persisted
        self.last_view_shape = None  # (H,W,3)

    def begin_edit(self):
        self.edit_mode = True
        self.pending = []

    def cancel_edit(self):
        self.edit_mode = False
        self.pending = []

    # This expects param=(self, cfg) via setMouseCallback
    def mouse_cb(self, event, x, y, flags, param):
        editor, cfg = param
        if not self.edit_mode: return
        if self.last_view_shape is None: return
        H, W = self.last_view_shape[:2]

        if event == cv2.EVENT_LBUTTONDOWN:
            self.pending.append((x,y))
            if len(self.pending) == 2:
                x0,y0 = self.pending[0]
                x1,y1 = self.pending[1]
                self.lines_norm.append(norm_line((x0,y0,x1,y1), W, H))
                self.lines_norm = self.lines_norm[:MAX_LINES]
                self.pending = []
        elif event == cv2.EVENT_RBUTTONDOWN:
            if self.pending:
                self.pending = []
            elif self.lines_norm:
                self.lines_norm.pop()

def main():
    cfg = load_settings()
    editor = Editor()
    editor.lines_norm = cfg.get("lines_norm", [])

    cam = FastCam(STREAM_URL).start()
    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW, 1280, 720)

    auto = True
    rotate_ccw = bool(cfg.get("rotate_ccw90", True))

    # IMPORTANT: Windows OpenCV uses positional param, no 'userdata'
    cv2.setMouseCallback(WINDOW, editor.mouse_cb, (editor, cfg))

    print(
        "[keys]\n"
        "  a: toggle AUTO overlay     e: EDIT lines (click two points per line)\n"
        "  r: toggle 90° CCW rotate   c: select crop ROI on RAW frame\n"
        "  d: delete ALL lines        s: save settings\n"
        "  ESC/q: quit\n"
        "  (in EDIT: LeftClick adds points, RightClick undoes last/pending)"
    )

    while True:
        ok, frame = cam.read()
        if not ok or frame is None:
            info = np.zeros((240, 480, 3), np.uint8)
            cv2.putText(info, "Connecting to camera...", (20,120),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255,255,255), 2, cv2.LINE_AA)
            cv2.imshow(WINDOW, info)
            if cv2.waitKey(20) in (27, ord('q')): break
            continue

        view = get_cropped_rotated(frame, cfg.get("roi"), rotate_ccw)
        H, W = view.shape[:2]
        editor.last_view_shape = (H, W, 3)  # keep current view size for clicks

        lines_px = [denorm_line(ln, W, H) for ln in editor.lines_norm]
        display = view.copy()

        if auto and len(lines_px) > 0:
            gray = cv2.cvtColor(view, cv2.COLOR_BGR2GRAY)
            hits = []
            for (x0,y0,x1,y1) in lines_px:
                x0,y0,x1,y1 = order_top_to_bottom(x0,y0,x1,y1)
                prof, pts = sample_line_profile(gray, x0,y0,x1,y1)
                idx, val, grad = strongest_gradient_idx(prof)
                xh, yh = pts[idx]
                hits.append(((xh, yh), idx, grad))
            display = draw_lines_and_hits(display, lines_px, hits)

        if editor.edit_mode:
            for p in editor.pending:
                cv2.circle(display, p, 5, (255,0,0), -1, cv2.LINE_AA)
            cv2.putText(display, f"EDIT MODE: click two points per line ({len(editor.lines_norm)}/{MAX_LINES})",
                        (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,0,0), 3, cv2.LINE_AA)
            cv2.putText(display, f"EDIT MODE: click two points per line ({len(editor.lines_norm)}/{MAX_LINES})",
                        (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,255,255), 2, cv2.LINE_AA)

        cv2.imshow(WINDOW, display)
        k = cv2.waitKey(1) & 0xFF
        if k in (27, ord('q')):
            break
        elif k == ord('a'):
            auto = not auto
        elif k == ord('e'):
            editor.edit_mode = not editor.edit_mode
            editor.pending = []
        elif k == ord('d'):
            editor.lines_norm = []
            editor.pending = []
        elif k == ord('r'):
            rotate_ccw = not rotate_ccw
        elif k == ord('c'):
            ok2, f2 = cam.read()
            if ok2 and f2 is not None:
                roi = select_roi_interactive(f2, cfg.get("roi"))
                if roi is not None:
                    cfg["roi"] = [int(v) for v in roi]
        elif k == ord('s'):
            cfg["rotate_ccw90"] = bool(rotate_ccw)
            cfg["lines_norm"] = editor.lines_norm
            save_settings(cfg)
            print("Settings saved.")

    cam.stop()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
