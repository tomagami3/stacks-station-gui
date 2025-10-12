import cv2
import numpy as np
import json
import time
import threading
from pathlib import Path

# ---------------- CONFIG ----------------
STREAM_URL = "rtsp://192.168.1.104:554/stream1"  # <-- change if needed (RTSP/MJPEG URL)
SETTINGS_PATH = Path("ipcam_fill_gauge_settings.json")
WINDOW = "Saffron Fill Gauge (Robust)"
MAX_LINES = 6
# ----------------------------------------

def load_settings():
    if SETTINGS_PATH.exists():
        try:
            data = json.loads(SETTINGS_PATH.read_text())
            # Backward-compat: ensure fields exist
            data.setdefault("rotate_ccw90", True)
            data.setdefault("roi", None)
            data.setdefault("lines_norm", [])
            data.setdefault("polarities", [])   # 0=auto, +1=pos (dark->bright), -1=neg (bright->dark)
            # Clip to MAX_LINES
            data["lines_norm"] = data["lines_norm"][:MAX_LINES]
            data["polarities"] = (data["polarities"] + [0]*MAX_LINES)[:MAX_LINES]
            return data
        except Exception:
            pass
    return {"rotate_ccw90": True, "roi": None, "lines_norm": [], "polarities": [0]*MAX_LINES}

def save_settings(cfg):
    SETTINGS_PATH.write_text(json.dumps(cfg, indent=2))

class FastCam:
    """Threaded capture that always returns the latest frame (low latency)."""
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
        threading.Thread(target=self._reader, daemon=True).start()
        for _ in range(80):
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

# --------- LINE helpers ---------
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
    dy = y1 - y0; dx = x1 - x0
    length = float(np.hypot(dx, dy))
    if num_samples is None:
        num_samples = max(10, int(length))  # ~1 px spacing
    ts = np.linspace(0, 1, num_samples).astype(np.float32)
    xs = x0 + ts * dx; ys = y0 + ts * dy
    map_x = xs.reshape(1, -1); map_y = ys.reshape(1, -1)
    prof = cv2.remap(gray, map_x, map_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    prof = prof.reshape(-1).astype(np.float32)
    pts = np.stack([xs, ys], axis=1)
    return prof, pts

# --------- ROBUST step-edge detector ---------
def _moving_mean_1d(x, w):
    w = int(max(1, w))
    pad = w // 2
    xp = np.pad(x.astype(np.float32), (pad, pad), mode='edge')
    c = np.cumsum(xp)
    m = (c[w:] - c[:-w]) / float(w)
    # length = len(x) + 2*pad - (w-1); crop to len(x)
    return m[:len(x)]

def step_response(profile, w):
    n = len(profile)
    if n < w + 4:
        g = np.gradient(profile.astype(np.float32))
        return g
    ma = _moving_mean_1d(profile, w)  # centered moving average
    pad = w // 2
    above = np.roll(ma, -pad)  # toward smaller y
    below = np.roll(ma, +pad)  # toward larger y
    score = below - above      # positive when intensity increases downward
    return score

def robust_fill_edge_idx(profile, windows=(11, 21, 41), margin=8, prefer_sign=None, ema_prev=None):
    x = profile.astype(np.float32)
    n = len(x)
    lo, hi = margin, max(margin, n - margin - 1)
    if hi <= lo + 2:
        idx_grad = int(np.argmax(np.abs(np.gradient(x))))
        return idx_grad, 0.0

    best_idx, best_score = None, -np.inf
    for w in windows:
        s = step_response(x, int(w))
        s[:lo] = -np.inf
        s[hi:] = -np.inf

        if prefer_sign == 1:          # pos: dark->bright downward
            idx = int(np.argmax(s)); score = float(s[idx])
        elif prefer_sign == -1:       # neg: bright->dark downward
            idx = int(np.argmin(s)); score = -float(s[idx])  # compare on same positive scale
        else:                         # auto: |score|
            idx_pos = int(np.argmax(s)); sc_pos = float(s[idx_pos])
            idx_neg = int(np.argmin(s)); sc_neg = -float(s[idx_neg])
            if sc_pos >= sc_neg:
                idx, score = idx_pos, sc_pos
            else:
                idx, score = idx_neg, sc_neg

        if score > best_score:
            best_idx, best_score = idx, score

    if ema_prev is not None:
        alpha = 0.7  # closer to previous for stability
        idx_smoothed = int(round(alpha * ema_prev + (1 - alpha) * best_idx))
        return idx_smoothed, best_score
    return best_idx, best_score

# ---------- Drawing ----------
def draw_lines_and_hits(view, lines_px, hits, polarities, selected_idx=None):
    out = view.copy()
    for i, ((x0,y0,x1,y1), hit) in enumerate(zip(lines_px, hits)):
        col = (0,255,0) if i != selected_idx else (0,255,255)
        cv2.line(out, (x0,y0), (x1,y1), col, 2, cv2.LINE_AA)
        cv2.circle(out, (x0,y0), 4, (0,200,255), -1, cv2.LINE_AA)
        cv2.circle(out, (x1,y1), 4, (0,200,255), -1, cv2.LINE_AA)

        pol = polarities[i] if i < len(polarities) else 0
        pol_txt = {0:"auto", 1:"pos", -1:"neg"}.get(pol,"auto")

        if hit is not None:
            (xh, yh), idx, score = hit
            cv2.circle(out, (int(xh), int(yh)), 6, (0,0,255), 2, cv2.LINE_AA)
            # distance from bottom endpoint along the segment
            xb, yb = (x1,y1) if y1 >= y0 else (x0,y0)
            dist_from_bottom = int(np.hypot(xb - xh, yb - yh))
            txt = f"L{i+1} dBtm={dist_from_bottom}px score={score:.1f} pol={pol_txt}"
            ytxt = max(18, min(y0, y1) - 6)
            xtext = min(max(min(x0,x1) + 6, 6), out.shape[1]-240)
            cv2.putText(out, txt, (xtext, ytxt), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255,255,255), 2, cv2.LINE_AA)
            cv2.putText(out, txt, (xtext, ytxt), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0,0,0), 1, cv2.LINE_AA)
    return out

# ---------- Interactive state ----------
class Editor:
    def __init__(self):
        self.edit_mode = False
        self.pending = []          # clicks in current line [(x,y)]
        self.lines_norm = []       # persisted
        self.polarities = [0]*MAX_LINES  # 0 auto, +1 pos, -1 neg
        self.last_view_shape = None
        self.selected_line = 0     # 0..MAX_LINES-1

    def mouse_cb(self, event, x, y, flags, param):
        # param is (editor, cfg) by convention here (not used for now)
        if not self.edit_mode: return
        if self.last_view_shape is None: return
        H, W = self.last_view_shape[:2]
        if event == cv2.EVENT_LBUTTONDOWN:
            self.pending.append((x,y))
            if len(self.pending) == 2:
                x0,y0 = self.pending[0]; x1,y1 = self.pending[1]
                ln = norm_line((x0,y0,x1,y1), W, H)
                if len(self.lines_norm) < MAX_LINES:
                    self.lines_norm.append(ln)
                else:
                    self.lines_norm[self.selected_line] = ln
                self.pending = []
        elif event == cv2.EVENT_RBUTTONDOWN:
            if self.pending: self.pending = []
            elif self.lines_norm:
                # delete currently selected line
                if self.selected_line < len(self.lines_norm):
                    self.lines_norm.pop(self.selected_line)
                    self.polarities.pop(self.selected_line)
                    self.polarities.append(0)  # keep list size
                    self.selected_line = max(0, min(self.selected_line, len(self.lines_norm)-1))

def main():
    cfg = load_settings()
    editor = Editor()
    editor.lines_norm = cfg.get("lines_norm", [])
    # Extend polarities list to MAX_LINES
    stored_pols = cfg.get("polarities", [0]*MAX_LINES)
    editor.polarities = (stored_pols + [0]*MAX_LINES)[:MAX_LINES]

    cam = FastCam(STREAM_URL).start()
    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW, 1280, 720)
    cv2.setMouseCallback(WINDOW, editor.mouse_cb, (editor, cfg))  # positional param only (Windows OpenCV)

    auto = True
    rotate_ccw = bool(cfg.get("rotate_ccw90", True))

    # Per-line temporal smoothing of index
    ema_idx_by_line = {}

    print(
        "[keys]\n"
        "  a: toggle AUTO overlay     e: EDIT lines (click two points per line)\n"
        "  r: toggle 90° CCW rotate   c: select crop ROI on RAW frame\n"
        "  d: delete ALL lines        s: save settings\n"
        "  1..6: select line index    p: cycle polarity of selected line (auto/pos/neg)\n"
        "  ESC/q: quit\n"
        "  (EDIT: LeftClick adds points; RightClick undoes or deletes selected line)"
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
        editor.last_view_shape = (H, W, 3)

        # Prepare geometry
        lines_px = [denorm_line(ln, W, H) for ln in editor.lines_norm]
        display = view.copy()

        # AUTO overlay: robust step detection
        hits = []
        if auto and len(lines_px) > 0:
            gray = cv2.cvtColor(view, cv2.COLOR_BGR2GRAY)
            for li, (x0,y0,x1,y1) in enumerate(lines_px):
                x0,y0,x1,y1 = order_top_to_bottom(x0,y0,x1,y1)
                prof, pts = sample_line_profile(gray, x0,y0,x1,y1)

                prefer = editor.polarities[li] if li < len(editor.polarities) else 0
                last_idx = ema_idx_by_line.get(li)
                idx, score = robust_fill_edge_idx(
                    prof, windows=(11,21,41), margin=10,
                    prefer_sign=prefer if prefer in (-1,0,1) else 0,
                    ema_prev=last_idx
                )
                ema_idx_by_line[li] = idx
                xh, yh = pts[idx]
                hits.append(((xh, yh), idx, score))
            display = draw_lines_and_hits(display, lines_px, hits, editor.polarities, editor.selected_line)

        # Edit overlays
        if editor.edit_mode:
            for p in editor.pending:
                cv2.circle(display, p, 5, (255,0,0), -1, cv2.LINE_AA)
            msg = f"EDIT: click two points (lines {len(editor.lines_norm)}/{MAX_LINES}) | selected L{editor.selected_line+1}"
            cv2.putText(display, msg, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,0,0), 3, cv2.LINE_AA)
            cv2.putText(display, msg, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,255,255), 2, cv2.LINE_AA)

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
            editor.polarities = [0]*MAX_LINES
            editor.pending = []
            ema_idx_by_line.clear()
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
            cfg["polarities"] = editor.polarities
            save_settings(cfg)
            print("Settings saved.")
        elif k in (ord('1'), ord('2'), ord('3'), ord('4'), ord('5'), ord('6')):
            editor.selected_line = min(MAX_LINES-1, max(0, k - ord('1')))
            print(f"Selected line: L{editor.selected_line+1}")
        elif k == ord('p'):
            # cycle polarity: 0 -> +1 -> -1 -> 0
            i = editor.selected_line
            cur = editor.polarities[i]
            nxt = {0:1, 1:-1, -1:0}.get(cur, 0)
            editor.polarities[i] = nxt
            ema_idx_by_line.pop(i, None)  # reset smoothing for that line
            print(f"L{i+1} polarity now: {{0:'auto',1:'pos',-1:'neg'}}[{nxt}]")

    cam.stop()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
