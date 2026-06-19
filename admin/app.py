#!/usr/bin/env python3
"""
renovate.pk Bot — Admin Dashboard

A small local web panel so a non-technical owner can:
  - Start / Stop the openclaw bot (kill switch)
  - See whether it's running and connected to WhatsApp
  - Monitor customers by label (New customers / Hot leads / Important)

It does NOT show conversations. It controls the gateway as a subprocess, so the
dashboard stays up even when the bot is off ("start it from here anytime").

Run:   python3 admin/app.py
Then open the URL it prints. Username: admin. Password: see admin/admin_password.txt
"""

import json
import os
import re
import secrets
import sqlite3
import subprocess
import time
from functools import wraps

from flask import Flask, Response, jsonify, request

# ---- paths / config -------------------------------------------------
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATEWAY_LOG = os.path.join(REPO, "progress", "gateway.log")
CUSTOMERS_FILE = os.path.join(REPO, "workspace", "data", "customers.json")
DB_FILE = os.path.join(REPO, "workspace", "data", "leadgen.db")
CATEGORIES = ["new customer", "important", "hot leads"]
PATCHER = os.path.join(REPO, "openclaw-patches", "apply_patches.py")
NODE_BIN = "/usr/local/node-v22.21.1/bin"
PASSWORD_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "admin_password.txt")
HOST = os.environ.get("ADMIN_HOST", "0.0.0.0")
PORT = int(os.environ.get("ADMIN_PORT", "8088"))

app = Flask(__name__)


def get_password():
    pw = os.environ.get("ADMIN_PASS")
    if pw:
        return pw
    if os.path.exists(PASSWORD_FILE):
        return open(PASSWORD_FILE).read().strip()
    pw = secrets.token_urlsafe(9)
    with open(PASSWORD_FILE, "w") as f:
        f.write(pw + "\n")
    os.chmod(PASSWORD_FILE, 0o600)
    return pw


ADMIN_PASSWORD = get_password()


def require_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth = request.authorization
        if not auth or auth.username != "admin" or not secrets.compare_digest(auth.password or "", ADMIN_PASSWORD):
            return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="Bot Admin"'})
        return f(*args, **kwargs)

    return wrapper


# ---- process control -------------------------------------------------
def gateway_pids():
    try:
        out = subprocess.run(["pgrep", "-f", "openclaw-gateway"], capture_output=True, text=True, timeout=5)
        return [int(p) for p in out.stdout.split() if p.strip()]
    except Exception:
        return []


def is_running():
    return len(gateway_pids()) > 0


def tail(path, n=400):
    try:
        with open(path, "r", errors="ignore") as f:
            return f.readlines()[-n:]
    except Exception:
        return []


def gateway_status():
    pids = gateway_pids()
    running = len(pids) > 0
    lines = tail(GATEWAY_LOG, 400)
    text = "".join(lines)

    # connected = a "Listening" line appears after the most recent restart/exit
    connected = False
    last_listen = max((i for i, l in enumerate(lines) if "Listening for personal" in l), default=-1)
    last_down = max((i for i, l in enumerate(lines) if ("channel exited" in l or "ECONNREFUSED" in l or "starting provider" in l)), default=-2)
    if running and last_listen >= 0 and last_listen >= last_down:
        connected = True

    model = None
    m = re.findall(r"agent model:\s*([^\s]+)", text)
    if m:
        model = m[-1]

    uptime = None
    if pids:
        try:
            et = subprocess.run(["ps", "-o", "etimes=", "-p", str(pids[0])], capture_output=True, text=True, timeout=5)
            uptime = int(et.stdout.strip())
        except Exception:
            uptime = None

    last_inbound = None
    inbound = [l for l in lines if "Inbound message" in l]
    if inbound:
        tm = re.search(r"(\d{4}-\d{2}-\d{2}T[\d:]+)", inbound[-1])
        last_inbound = tm.group(1) if tm else None

    return {
        "running": running,
        "connected": connected,
        "model": model,
        "pid": pids[0] if pids else None,
        "uptime_seconds": uptime,
        "last_inbound": last_inbound,
    }


def start_gateway():
    if is_running():
        return {"ok": True, "message": "Already running"}
    env = os.environ.copy()
    env["PATH"] = NODE_BIN + ":" + env.get("PATH", "")
    env["OPENCLAW_AUTO_UPDATE"] = "0"  # never auto-update (would wipe our patches)
    # ensure patches are applied (idempotent) before launch
    try:
        subprocess.run(["python3", PATCHER], cwd=REPO, env=env, capture_output=True, text=True, timeout=60)
    except Exception:
        pass
    os.makedirs(os.path.dirname(GATEWAY_LOG), exist_ok=True)
    logf = open(GATEWAY_LOG, "a")
    subprocess.Popen(
        ["openclaw", "gateway"],
        cwd=REPO, env=env, stdout=logf, stderr=logf,
        start_new_session=True,  # detach so it survives this request
    )
    # wait briefly for it to come up
    for _ in range(20):
        if is_running():
            break
        time.sleep(0.5)
    return {"ok": is_running(), "message": "Starting…"}


def stop_gateway():
    pids = gateway_pids()
    # also catch the parent launcher
    try:
        parent = subprocess.run(["pgrep", "-x", "openclaw"], capture_output=True, text=True, timeout=5)
        pids += [int(p) for p in parent.stdout.split() if p.strip()]
    except Exception:
        pass
    for sig in ("-TERM", "-KILL"):
        alive = [p for p in set(pids) if _alive(p)]
        if not alive:
            break
        subprocess.run(["kill", sig] + [str(p) for p in alive], capture_output=True)
        time.sleep(2)
    return {"ok": not is_running(), "message": "Stopped"}


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


# ---- customer data ---------------------------------------------------
def _tally(custs):
    counts = {c: 0 for c in CATEGORIES}
    for c in custs:
        k = (c.get("category") or "").strip().lower()
        if k in counts:
            counts[k] += 1
    return {"categories": CATEGORIES, "counts": counts, "customers": custs, "total": len(custs)}


def load_customers():
    # Source of truth is the SQLite DB (read-only). Fall back to the JSON mirror.
    try:
        conn = sqlite3.connect(f"file:{DB_FILE}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT phone, name, email, category, lead_score, status, first_contact_at, last_message_at "
            "FROM customers ORDER BY last_message_at DESC"
        ).fetchall()
        conn.close()
        return _tally([dict(r) for r in rows])
    except Exception:
        try:
            d = json.load(open(CUSTOMERS_FILE))
            return _tally(d.get("customers", []))
        except Exception:
            return _tally([])


# ---- routes ----------------------------------------------------------
@app.route("/api/status")
@require_auth
def api_status():
    return jsonify(gateway_status())


@app.route("/api/customers")
@require_auth
def api_customers():
    return jsonify(load_customers())


@app.route("/api/start", methods=["POST"])
@require_auth
def api_start():
    return jsonify(start_gateway())


@app.route("/api/stop", methods=["POST"])
@require_auth
def api_stop():
    return jsonify(stop_gateway())


@app.route("/")
@require_auth
def index():
    return Response(PAGE, mimetype="text/html")


PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>renovate.pk Bot Admin</title>
<style>
  :root { --bg:#0f1419; --card:#1a2129; --line:#2a3441; --txt:#e6edf3; --muted:#8b98a5;
          --green:#2ea043; --red:#da3633; --amber:#d29922; --blue:#388bfd; }
  * { box-sizing:border-box; } body { margin:0; font-family:system-ui,Segoe UI,Roboto,sans-serif;
      background:var(--bg); color:var(--txt); }
  .wrap { max-width:900px; margin:0 auto; padding:24px 16px 60px; }
  h1 { font-size:20px; margin:0 0 4px; } .sub { color:var(--muted); font-size:13px; margin-bottom:20px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:18px; margin-bottom:16px; }
  .statusrow { display:flex; align-items:center; gap:14px; flex-wrap:wrap; }
  .dot { width:14px; height:14px; border-radius:50%; display:inline-block; }
  .badge { font-weight:700; font-size:18px; }
  .meta { color:var(--muted); font-size:13px; display:flex; gap:18px; flex-wrap:wrap; margin-top:10px; }
  .btns { margin-top:16px; display:flex; gap:10px; }
  button { border:0; border-radius:8px; padding:12px 20px; font-size:15px; font-weight:600; cursor:pointer; color:#fff; }
  button:disabled { opacity:.4; cursor:not-allowed; }
  .start { background:var(--green); } .stop { background:var(--red); }
  .cards { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }
  .lbl { border-radius:12px; padding:16px; border:1px solid var(--line); }
  .lbl .n { font-size:34px; font-weight:800; } .lbl .t { font-size:13px; color:var(--muted); }
  .lbl.new { background:rgba(56,139,253,.12); } .lbl.hot { background:rgba(218,54,51,.12); }
  .lbl.imp { background:rgba(210,153,34,.12); }
  table { width:100%; border-collapse:collapse; font-size:14px; }
  th,td { text-align:left; padding:9px 8px; border-bottom:1px solid var(--line); }
  th { color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase; }
  .pill { font-size:12px; padding:2px 9px; border-radius:20px; }
  .pill.new { background:rgba(56,139,253,.2); color:#79c0ff; }
  .pill.hot { background:rgba(218,54,51,.2); color:#ff7b72; }
  .pill.imp { background:rgba(210,153,34,.2); color:#e3b341; }
  .foot { color:var(--muted); font-size:12px; margin-top:8px; }
</style></head><body><div class="wrap">
  <h1>renovate.pk — Bot Admin</h1>
  <div class="sub">Supervise and control the WhatsApp assistant. (Conversations are private and not shown here.)</div>

  <div class="card">
    <div class="statusrow">
      <span class="dot" id="dot" style="background:var(--muted)"></span>
      <span class="badge" id="badge">Checking…</span>
      <span class="meta" id="wa"></span>
    </div>
    <div class="meta" id="meta"></div>
    <div class="btns">
      <button class="start" id="startBtn" onclick="ctl('start')">▶ Start bot</button>
      <button class="stop" id="stopBtn" onclick="ctl('stop')">■ Stop bot (kill switch)</button>
    </div>
  </div>

  <div class="cards">
    <div class="lbl new"><div class="n" id="c_new">–</div><div class="t">New customers</div></div>
    <div class="lbl hot"><div class="n" id="c_hot">–</div><div class="t">Hot leads</div></div>
    <div class="lbl imp"><div class="n" id="c_imp">–</div><div class="t">Important</div></div>
  </div>

  <div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
      <strong>Customers</strong><span class="foot" id="total"></span>
    </div>
    <table><thead><tr><th>Name</th><th>Phone</th><th>Label</th><th>Last seen</th></tr></thead>
    <tbody id="rows"><tr><td colspan="4" class="foot">Loading…</td></tr></tbody></table>
  </div>
  <div class="foot">Auto-refreshes every 5s.</div>
</div>
<script>
function fmtUptime(s){ if(s==null)return''; let h=Math.floor(s/3600),m=Math.floor(s%3600/60); return h?`${h}h ${m}m`:`${m}m`; }
function pill(cat){ cat=(cat||'').toLowerCase(); if(cat.includes('hot'))return'<span class="pill hot">Hot leads</span>';
  if(cat.includes('important'))return'<span class="pill imp">Important</span>'; return'<span class="pill new">New customer</span>'; }
async function refresh(){
  try{
    const s = await (await fetch('/api/status')).json();
    const dot=document.getElementById('dot'), badge=document.getElementById('badge');
    if(s.running){ dot.style.background='var(--green)'; badge.textContent='ONLINE'; }
    else { dot.style.background='var(--red)'; badge.textContent='OFFLINE'; }
    document.getElementById('wa').innerHTML = s.running ? (s.connected?'• WhatsApp connected':'• connecting…') : '';
    document.getElementById('meta').innerHTML =
      [ s.model?('Model: '+s.model):'', s.uptime_seconds!=null?('Uptime: '+fmtUptime(s.uptime_seconds)):'',
        s.last_inbound?('Last message: '+s.last_inbound.replace('T',' ')):'' ].filter(Boolean).join(' &nbsp;|&nbsp; ');
    document.getElementById('startBtn').disabled = s.running;
    document.getElementById('stopBtn').disabled = !s.running;
  }catch(e){}
  try{
    const c = await (await fetch('/api/customers')).json();
    const cl = (n)=>{ for(const k in (c.counts||{})) if(k.toLowerCase().includes(n)) return c.counts[k]; return 0; };
    document.getElementById('c_new').textContent = cl('new');
    document.getElementById('c_hot').textContent = cl('hot');
    document.getElementById('c_imp').textContent = cl('important');
    document.getElementById('total').textContent = (c.total||0)+' total';
    const rows = (c.customers||[]).map(x=>`<tr><td>${x.name||'—'}</td><td>${x.phone||''}</td><td>${pill(x.category)}</td><td class="foot">${(x.last_message_at||'').replace('T',' ').slice(0,16)}</td></tr>`).join('');
    document.getElementById('rows').innerHTML = rows || '<tr><td colspan="4" class="foot">No customers yet.</td></tr>';
  }catch(e){}
}
async function ctl(action){
  const b=document.getElementById(action+'Btn'); b.disabled=true; b.textContent='…';
  try{ await fetch('/api/'+action,{method:'POST'}); }catch(e){}
  setTimeout(()=>{ refresh(); b.textContent = action==='start'?'▶ Start bot':'■ Stop bot (kill switch)'; }, 1500);
}
refresh(); setInterval(refresh, 5000);
</script></body></html>"""


if __name__ == "__main__":
    ip = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()
    lan = ip[0] if ip else "127.0.0.1"
    print("=" * 56)
    print("  renovate.pk Bot Admin Dashboard")
    print(f"  Local:   http://127.0.0.1:{PORT}")
    print(f"  Network: http://{lan}:{PORT}")
    print(f"  Login:   admin  /  {ADMIN_PASSWORD}")
    print("=" * 56)
    app.run(host=HOST, port=PORT, threaded=True)
