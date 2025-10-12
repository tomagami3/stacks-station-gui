import json, socket, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer

# Optional IO map import
_io_mod = None
try:
    import manual_io as _io_mod
except Exception:
    try:
        import maunal_io as _io_mod
    except Exception:
        _io_mod = None

from io_worker import get_do_list
from positions_store import PositionsStore


# ---- helpers ----
def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]
    except Exception:
        ip = socket.gethostbyname(socket.gethostname()) or "127.0.0.1"
    finally:
        s.close()
    return ip


SHELL = """
<!doctype html><html><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Habonim - Valve assembly machine</title>
<style>
 body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:16px}
 h1{font-size:22px;margin:0 0 14px} h2{font-size:18px;margin:14px 0 10px}
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
 .grid2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
 .card{padding:12px;border:1px solid #ddd;border-radius:12px}
 .row{display:flex;align-items:center;justify-content:space-between;margin:6px 0}
 .btn{display:inline-block;padding:10px 14px;border:1px solid #ccc;border-radius:10px;text-decoration:none;color:#000}
 .btn:active{transform:scale(.98)} .btn.primary{border-color:#0a7a0a;background:#eaffea}
 .bigbtn{display:block;padding:16px;border:1px solid #aaa;border-radius:14px;text-align:center;text-decoration:none;color:#000;font-weight:600}
 .stack{display:flex;flex-direction:column;gap:8px} .status{font-family:Consolas,monospace}
 .switch{position:relative;display:inline-block;width:54px;height:28px}
 .switch input{display:none}
 .slider{position:absolute;cursor:pointer;top:0;left:0;right:0;bottom:0;background:#ccc;transition:.2s;border-radius:28px}
 .slider:before{position:absolute;content:"";height:22px;width:22px;left:3px;bottom:3px;background:white;transition:.2s;border-radius:50%}
 input:checked + .slider{background:#4caf50} input:checked + .slider:before{transform:translateX(26px)}
 .vbtns{display:flex;flex-direction:column;gap:8px} .vbtns button{padding:10px 12px}
 .pill{display:inline-block;padding:4px 10px;border:1px solid #ddd;border-radius:999px;font-size:13px}
 .slots button{padding:10px 12px;margin:4px;border-radius:10px;border:1px solid #bbb}
 .topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
 .hint{font-size:12px;color:#666}
 .inline{display:inline-flex;gap:8px;align-items:center}
</style></head><body>
<div class="topbar">
  <h1>Habonim - Valve assembly machine</h1>
  <div class="status">
    Stacks pos: <span id="pos">—</span> |
    Table pos: <span id="pos_tbl">—</span> |
    Torque pos: <span id="pos_torque">—</span> |
    Half-auto: <span id="ha">—</span> <span id="harun"></span> |
    AutoCycle: <span id="acr">—</span> <span id="acp"></span>
  </div>
</div>
__BODY__
<script>
async function api(path, data=null){
  const opt = data ? {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(data)} : {};
  const r = await fetch(path, opt); return r.json();
}
async function refresh(){
  try{
    const st = await api("/api/state");
    pos.textContent       = st.pos ?? "—";
    pos_tbl.textContent   = st.pos_table ?? "—";
    pos_torque.textContent= st.pos_torque ?? "—";
    ha.textContent  = st.half_auto_enabled ? "ON" : "OFF";
    harun.textContent = st.semi_auto_running ? " (running)" : "";
    acr.textContent = st.auto_cycle_running ? "ON" : "OFF";
    acp.textContent = st.auto_cycle_running ? (" ("+(st.auto_cycle_phase||"")+")") : "";
    document.querySelectorAll('input[id^="do"]').forEach(cb=>{
      const ch = parseInt(cb.id.replace("do",""));
      if (st.do && (String(ch) in st.do)) cb.checked = !!st.do[String(ch)];
    });
    const ha_cb = document.getElementById("half_auto_cb");
    if (ha_cb) ha_cb.checked = !!st.half_auto_enabled;
  }catch(e){}
}
async function setDO(ch,val){ await api("/api/io",{ch:ch,val:!!val}); setTimeout(refresh,150); }
async function gotoSlot(slot){ await api("/api/motor/goto_slot", {slot: slot}); setTimeout(refresh,200); }
async function gotoTorque(slot){ await api("/api/torque/goto_slot", {slot: slot}); setTimeout(refresh,200); }
async function setHalfAuto(val){ await api("/api/half_auto/set", {enabled: !!val}); setTimeout(refresh,150); }
async function tableRel(delta){ await api("/api/table/rel",{delta:delta}); setTimeout(refresh,150); }
async function tableSlowJog(sign){ await api("/api/table/slow_jog",{sign:sign}); setTimeout(refresh,150); }
async function tableStation(sign){ await api("/api/table/station_step",{sign:sign}); setTimeout(refresh,200); }
async function autoStart(){ await api("/api/auto_cycle/start",{}); setTimeout(refresh,300); }
async function autoStop(){ await api("/api/auto_cycle/stop",{}); setTimeout(refresh,150); }
refresh(); setInterval(refresh, 1000);
</script></body></html>
""".strip()


def _toggle_row(ch: int, label: str) -> str:
    return (f'<div class="row"><span>DO{ch:02d} – {label}</span>'
            f'<label class="switch"><input type="checkbox" id="do{ch}" onchange="setDO({ch}, this.checked)"><span class="slider"></span></label></div>')

def _group_rows(pairs) -> str:
    return "\n".join(_toggle_row(ch, name) for ch, name in pairs)

def _wrap(body_html: str) -> str:
    return SHELL.replace("__BODY__", body_html)

def _home_page() -> str:
    return _wrap("""
<div class="grid">
  <a class="bigbtn" href="/table">Table Main</a>
  <a class="bigbtn" href="/grease">Grease station</a>
  <a class="bigbtn" href="/seal">Internal seal station</a>
  <a class="bigbtn" href="/shaft">Shaft assembly</a>
  <a class="bigbtn" href="/stacks">Stacks station</a>
  <a class="bigbtn" href="/torque">Torque station</a>
</div>
""".strip())

def _table_page(settings) -> str:
    half_c = int(settings.get("half_step_counts", 5000))
    full_c = int(settings.get("full_step_counts", 10000))
    station = int(settings.get("station_step_counts", 46080))
    body = f"""
<a class="btn" href="/">Main</a>
<h2>Table Main</h2>
<div class="grid2">
  <div class="card">
    <h3>Table cylinders</h3>
    {_toggle_row(4,  "Table cyl A")}
    {_toggle_row(6,  "Table cyl B")}
    {_toggle_row(12, "Setup password / lock")}
    {_toggle_row(13, "Table aux / DO13")}
  </div>
  <div class="card">
    <h3>Table Motion</h3>
    <div class="vbtns">
      <button class="btn" onclick="tableRel(-{half_c})">◀ Half step (−{half_c})</button>
      <button class="btn primary" onclick="tableRel({half_c})">Half step (+{half_c}) ▶</button>
      <button class="btn" onclick="tableRel(-{full_c})">◀ Full step (−{full_c})</button>
      <button class="btn primary" onclick="tableRel({full_c})">Full step (+{full_c}) ▶</button>
      <div style="height:6px"></div>
      <div class="inline">
        <button class="btn" onclick="tableStation(-1)">◀ Station step (−{station})</button>
        <button class="btn primary" onclick="tableStation(1)">Station step (+{station}) ▶</button>
      </div>
      <div style="height:6px"></div>
      <button class="btn" onclick="tableSlowJog(-1)">◀ Slow jog (−)</button>
      <button class="btn" onclick="tableSlowJog(1)">Slow jog (+) ▶</button>
    </div>
    <div class="hint">Station = {station} pulses (editable in Setup if desired).</div>
  </div>
</div>
"""
    return _wrap(body)

def _grease_page() -> str:
    return _wrap('<a class="btn" href="/">Main</a><h2>Grease station</h2><div class="card">No controls yet.</div>')

def _seal_page() -> str:
    return _wrap('<a class="btn" href="/">Main</a><h2>Internal seal station</h2><div class="card">No controls yet.</div>')

def _shaft_page() -> str:
    rows = ""
    if _io_mod is not None and hasattr(_io_mod, "SHAFT_ASSEMBLY"):
        rows = _group_rows(getattr(_io_mod, "SHAFT_ASSEMBLY"))
    else:
        rows = "<div>(No SHAFT_ASSEMBLY mapping found)</div>"
    return _wrap(f'<a class="btn" href="/">Main</a><h2>Shaft assembly</h2><div class="card">{rows}</div>')

def _stacks_page(store: PositionsStore, half_auto_enabled: bool, auto_running: bool, auto_phase: str) -> str:
    slots = store.all()
    slot_html = "".join(
        f'<button onclick="gotoSlot(\'{name}\')">{name} <span class="pill">{slots.get(name, 0)}</span></button>'
        for name in ["Table", "1", "2", "3", "4", "5", "6"]
    )
    ha_chk = 'checked' if half_auto_enabled else ''
    start_disabled = 'disabled' if auto_running else ''
    stop_disabled  = '' if auto_running else 'disabled'
    body = f"""
<a class="btn" href="/">Main</a>
<h2>Stacks station</h2>
<div class="card">
  <div class="row"><strong>Half-auto</strong>
    <label class="switch">
      <input type="checkbox" id="half_auto_cb" onchange="setHalfAuto(this.checked)" {ha_chk}>
      <span class="slider"></span>
    </label>
  </div>
  <div class="row">
    <strong>Auto Cycle</strong>
    <div class="inline">
      <button class="btn primary" onclick="autoStart()" {start_disabled}>Start</button>
      <button class="btn" onclick="autoStop()" {stop_disabled}>Stop</button>
    </div>
  </div>
  <div class="hint">Auto Cycle: 1→6 stacks, DO10 down 1s at each, return to Table, then next.</div>
</div>
<div class="grid">
  <div class="card">
    <h3>Stacks push cylinder (DO09)</h3>
    <div class="vbtns">
      <button class="btn" onclick="setDO(9,false)">▲ Retract (OFF)</button>
      <button class="btn primary" onclick="setDO(9,true)">▼ Push (ON)</button>
    </div>
  </div>
  <div class="card">
    <h3>Piston slider (DO10)</h3>
    <div class="vbtns">
      <button class="btn" onclick="setDO(10,false)">▲ Up (OFF)</button>
      <button class="btn primary" onclick="setDO(10,true)">▼ Down (ON)</button>
    </div>
  </div>
  <div class="card">
    <h3>Positions</h3>
    <div class="slots">{slot_html}</div>
    <div class="hint">Values from positions_stacks.json.</div>
  </div>
</div>
"""
    return _wrap(body)

def _torque_page(store_torque: PositionsStore) -> str:
    slots = store_torque.all()
    slot_html = "".join(
        f'<button onclick="gotoTorque(\'{name}\')">{name} <span class="pill">{slots.get(name, 0)}</span></button>'
        for name in ["Table", "1", "2", "3", "4", "5", "6"]
    )
    return _wrap(f"""
<a class="btn" href="/">Main</a>
<h2>Torque station</h2>
<div class="card">
  <h3>Positions (Torque)</h3>
  <div class="slots">{slot_html}</div>
  <div class="hint">Values from positions_torque.json.</div>
</div>
""")

# ---- server factory ----
def make_handler(io_worker, stacks_motor_worker, table_motor_worker, torque_motor_worker, settings_store):
    store_stacks = PositionsStore("stacks")
    store_torque = PositionsStore("torque")
    settings = settings_store

    # semi-auto orchestration state for stacks (single-shot)
    semi_lock = threading.Lock()
    semi_running = {"flag": False, "phase": "idle"}  # phase: idle, to_slot, dwell, to_table, done, abort
    DO_PISTON = 10

    # auto-cycle orchestration (multi-stack)
    auto_lock = threading.Lock()
    auto_state = {"running": False, "phase": "idle", "idx": -1, "stacks_order": ["1","2","3","4","5","6"], "stop": False}

    def _within(cur: int, tgt: int, tol: int) -> bool:
        return abs(int(cur) - int(tgt)) <= int(tol)

    def _safe_do(ch, val):
        try: io_worker.write_do(ch, bool(val))
        except Exception: pass

    def _wait_until_close(motor, target, tol, running_flag_callable, timeout_s=8.0, poll=0.04):
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            if not running_flag_callable():
                return False
            cur = motor.read_position_now()
            if _within(cur, target, tol):
                return True
            time.sleep(poll)
        return False

    # ---- Half-auto (single stack) ----
    def _semi_auto_sequence(slot_name: str):
        POS_TOL = int(settings.get("semi_tol_counts", 20))
        wait_ms = int(settings.get("semi_wait_ms", 1200))
        t1 = float(settings.get("semi_timeout1_s", 10))
        t2 = float(settings.get("semi_timeout2_s", 10))
        acc = int(settings.get("semi_acc", settings.get("acc", 80)))
        spd = int(settings.get("semi_speed_rpm", 300))

        with semi_lock:
            if semi_running["flag"]:
                return
            semi_running["flag"] = True
            semi_running["phase"] = "to_slot"

        try:
            store_stacks.load()
            tgt_pos = int(store_stacks.get(slot_name, 0))
            tbl_pos = int(store_stacks.get("Table", 0))

            # go to slot
            stacks_motor_worker.move_to_abs_target(tgt_pos, spd, acc)
            if not _wait_until_close(stacks_motor_worker, tgt_pos, POS_TOL, lambda: semi_running["flag"], timeout_s=t1):
                semi_running["phase"] = "abort"; return

            # dwell w/ DO10 on
            semi_running["phase"] = "dwell"
            _safe_do(DO_PISTON, True)
            t0 = time.time()
            while (time.time() - t0) < (wait_ms/1000.0):
                if not semi_running["flag"]: break
                time.sleep(0.02)

            # return to table
            semi_running["phase"] = "to_table"
            stacks_motor_worker.move_to_abs_target(tbl_pos, spd, acc)
            if not _wait_until_close(stacks_motor_worker, tbl_pos, POS_TOL, lambda: semi_running["flag"], timeout_s=t2):
                semi_running["phase"] = "abort"; return

            semi_running["phase"] = "done"

        finally:
            _safe_do(DO_PISTON, False)
            semi_running["flag"] = False
            if semi_running["phase"] != "done":
                semi_running["phase"] = "idle"

    # ---- Auto-cycle (1..6) ----
    def _auto_cycle_sequence():
        POS_TOL = int(settings.get("semi_tol_counts", 20))
        wait_ms = 1000  # 1.0s at the stack
        t1 = float(settings.get("semi_timeout1_s", 10))
        t2 = float(settings.get("semi_timeout2_s", 10))
        acc = int(settings.get("semi_acc", settings.get("acc", 80)))
        spd = int(settings.get("semi_speed_rpm", 300))

        with auto_lock:
            if auto_state["running"]:
                return
            auto_state["running"] = True
            auto_state["phase"] = "start"
            auto_state["idx"] = -1
            auto_state["stop"] = False

        try:
            order = list(auto_state["stacks_order"])
            store_stacks.load()
            tbl_pos = int(store_stacks.get("Table", 0))

            for i, name in enumerate(order):
                with auto_lock:
                    auto_state["idx"] = i
                    if auto_state["stop"]:
                        break
                    auto_state["phase"] = f"to {name}"

                tgt_pos = int(store_stacks.get(name, 0))

                # 1) move to STACK (DO10 still up here)
                stacks_motor_worker.move_to_abs_target(tgt_pos, spd, acc)
                ok1 = _wait_until_close(
                    stacks_motor_worker, tgt_pos, POS_TOL,
                    lambda: (auto_state["running"] and not auto_state["stop"]),
                    timeout_s=t1
                )
                if not ok1:
                    break

                # 2) DO10 DOWN at STACK, dwell 1.0s
                with auto_lock:
                    if auto_state["stop"]:
                        break
                    auto_state["phase"] = f"dwell {name}"
                _safe_do(DO_PISTON, True)
                t0 = time.time()
                while (time.time() - t0) < (wait_ms / 1000.0):
                    with auto_lock:
                        if auto_state["stop"]:
                            break
                    time.sleep(0.02)
                with auto_lock:
                    if auto_state["stop"]:
                        break
                    auto_state["phase"] = f"to Table from {name}"

                # 3) KEEP DO10 DOWN while moving back to TABLE (slide)
                stacks_motor_worker.move_to_abs_target(tbl_pos, spd, acc)
                ok2 = _wait_until_close(
                    stacks_motor_worker, tbl_pos, POS_TOL,
                    lambda: (auto_state["running"] and not auto_state["stop"]),
                    timeout_s=t2
                )
                # 4) Once AT TABLE, DO10 UP
                _safe_do(DO_PISTON, False)
                time.sleep(0.5)
                if not ok2:
                    break

            with auto_lock:
                auto_state["phase"] = "done"

        finally:
            # safety: ensure DO10 is UP on any exit
            _safe_do(DO_PISTON, False)
            with auto_lock:
                auto_state["running"] = False
                auto_state["idx"] = -1
                if auto_state["phase"] != "done":
                    auto_state["phase"] = "idle"

    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, obj, code=200):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(json.dumps(obj).encode("utf-8"))

        def _send_html(self, html, code=200):
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        def log_message(self, *args, **kwargs): return

        def do_GET(self):
            path = (self.path or "/").split("?")[0]
            if path == "/":
                self._send_html(_home_page()); return
            if path == "/table":
                settings.load(); self._send_html(_table_page(settings)); return
            if path == "/grease":
                self._send_html(_grease_page()); return
            if path == "/seal":
                self._send_html(_seal_page()); return
            if path == "/shaft":
                self._send_html(_shaft_page()); return
            if path == "/stacks":
                store_stacks.load(); settings.load()
                self._send_html(_stacks_page(
                    store_stacks,
                    bool(settings.get("half_auto_enabled", False)),
                    bool(auto_state["running"]),
                    str(auto_state["phase"])
                )); return
            if path == "/torque":
                store_torque.load()
                self._send_html(_torque_page(store_torque)); return

            if path.startswith("/api/state"):
                try:
                    state = {
                        "pos": stacks_motor_worker.pos_counts,
                        "pos_table": table_motor_worker.pos_counts,
                        "pos_torque": torque_motor_worker.pos_counts,
                        "do": {str(ch): bool(io_worker.get_do(ch)) for ch, _ in get_do_list()},
                        "connected": {
                            "io": io_worker.connected,
                            "stacks_motor": stacks_motor_worker.connected,
                            "table_motor": table_motor_worker.connected,
                            "torque_motor": torque_motor_worker.connected,
                        },
                        "half_auto_enabled": bool(settings.get("half_auto_enabled", False)),
                        "semi_auto_running": bool(semi_running["flag"]),
                        "semi_auto_phase": str(semi_running.get("phase","idle")),
                        "auto_cycle_running": bool(auto_state["running"]),
                        "auto_cycle_phase": str(auto_state["phase"]),
                    }
                except Exception:
                    state = {"pos": None, "pos_table": None, "pos_torque": None,
                             "do": {}, "connected": {}, "half_auto_enabled": False,
                             "semi_auto_running": False, "semi_auto_phase": "idle",
                             "auto_cycle_running": False, "auto_cycle_phase": "idle"}
                self._send_json(state); return

            self._send_json({"error": "not found"}, 404)

        def do_POST(self):
            path = (self.path or "/").split("?")[0]
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except Exception:
                length = 0
            raw = self.rfile.read(length) if length > 0 else b""
            try:
                data = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                data = {}

            # IO write
            if path.startswith("/api/io"):
                ch = int(data.get("ch", -1)); val = bool(data.get("val", False))
                ok = False
                try:
                    if io_worker.connected and ch >= 0:
                        io_worker.write_do(ch, val); ok = True
                except Exception:
                    ok = False
                self._send_json({"ok": ok}, 200 if ok else 500); return

            # Stacks goto slot (with optional half-auto)
            if path.startswith("/api/motor/goto_slot"):
                slot = str(data.get("slot", "Table"))
                ha = bool(settings.get("half_auto_enabled", False))
                if ha and slot != "Table":
                    def _runner():
                        try: _semi_auto_sequence(slot)
                        except Exception:
                            with semi_lock: semi_running["flag"] = False
                    threading.Thread(target=_runner, daemon=True).start()
                    self._send_json({"ok": True, "mode": "semi-auto", "slot": slot}); return
                else:
                    store_stacks.load(); tgt = int(store_stacks.get(slot, 0))
                    spd = int(data.get("speed", settings.get("speed_rpm", 300)))
                    acc = int(data.get("acc", settings.get("acc", 80)))
                    ok = False
                    try:
                        if stacks_motor_worker.connected:
                            stacks_motor_worker.move_to_abs_target(tgt, spd, acc); ok = True
                    except Exception:
                        ok = False
                    self._send_json({"ok": ok, "mode": "direct", "slot": slot, "target": tgt}, 200 if ok else 500); return

            # Torque goto slot
            if path.startswith("/api/torque/goto_slot"):
                slot = str(data.get("slot", "Table"))
                store_torque.load(); tgt = int(store_torque.get(slot, 0))
                spd = int(data.get("speed", settings.get("speed_rpm", 300)))
                acc = int(data.get("acc", settings.get("acc", 80)))
                ok = False
                try:
                    if torque_motor_worker.connected:
                        torque_motor_worker.move_to_abs_target(tgt, spd, acc); ok = True
                except Exception:
                    ok = False
                self._send_json({"ok": ok, "slot": slot, "target": tgt}, 200 if ok else 500); return

            # Table relative, slow jog (existing)
            if path.startswith("/api/table/rel"):
                spd = int(settings.get("speed_rpm", 300)); acc = int(settings.get("acc", 80))
                delta = int(data.get("delta", 0))
                ok = False
                try:
                    if table_motor_worker.connected:
                        table_motor_worker.go_rel(delta, spd, acc); ok = True
                except Exception:
                    ok = False
                self._send_json({"ok": ok}, 200 if ok else 500); return

            if path.startswith("/api/table/slow_jog"):
                sign = int(data.get("sign", 1))
                delta = int(settings.get("slow_jog_step", 200)) * (1 if sign >= 0 else -1)
                spd = int(settings.get("slow_jog_rpm", 10)); acc = int(settings.get("acc", 80))
                ok = False
                try:
                    if table_motor_worker.connected:
                        table_motor_worker.go_rel(delta, spd, acc); ok = True
                except Exception:
                    ok = False
                self._send_json({"ok": ok}, 200 if ok else 500); return

            # NEW: Table station step (±station distance)
            if path.startswith("/api/table/station_step"):
                sign = 1 if int(data.get("sign", 1)) >= 0 else -1
                step = int(settings.get("station_step_counts", 46080)) * sign
                spd = int(settings.get("speed_rpm", 300)); acc = int(settings.get("acc", 80))
                ok = False
                try:
                    if table_motor_worker.connected:
                        table_motor_worker.go_rel(step, spd, acc); ok = True
                except Exception:
                    ok = False
                self._send_json({"ok": ok, "delta": step}, 200 if ok else 500); return

            # Half-auto toggle
            if path.startswith("/api/half_auto/set"):
                en = bool(data.get("enabled", False))
                settings.set("half_auto_enabled", en); settings.save()
                self._send_json({"ok": True, "enabled": en}); return

            # NEW: Auto Cycle control
            if path.startswith("/api/auto_cycle/start"):
                def _runner():
                    try: _auto_cycle_sequence()
                    except Exception:
                        with auto_lock: auto_state["running"] = False
                with auto_lock:
                    if not auto_state["running"]:
                        threading.Thread(target=_runner, daemon=True).start()
                self._send_json({"ok": True, "started": True}); return

            if path.startswith("/api/auto_cycle/stop"):
                with auto_lock:
                    auto_state["stop"] = True
                self._send_json({"ok": True}); return

            self._send_json({"error": "not found"}, 404)

    return Handler


def start_control_server(io_worker, stacks_motor_worker, table_motor_worker, torque_motor_worker, settings_store, port=8088):
    handler = make_handler(io_worker, stacks_motor_worker, table_motor_worker, torque_motor_worker, settings_store)
    httpd = HTTPServer(("", port), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True); t.start()
    return httpd
