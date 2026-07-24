#!/usr/bin/env python3
"""
renovate.pk Bot — Admin Dashboard (CRM)

A local web panel so a non-technical owner can:
  - Start / Stop the openclaw bot (kill switch)
  - See whether it's running and connected to WhatsApp
  - Browse ALL customers CRM-style: search, filter by label, sort, export CSV
  - Move a customer between labels (goes through workspace/db.py set-category,
    the sole safe writer — it also drives WhatsApp label sync + the inbound gate)
  - See captured leads

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
DB_PY = os.path.join(REPO, "workspace", "db.py")
# Must match workspace/db.py CATEGORIES (the validator of record).
CATEGORIES = ["new customer", "important", "hot leads", "followup", "junk", "complaints", "vendor", "ahsan", "ahmed", "imran", "rafay"]
PATCHER = os.path.join(REPO, "openclaw-patches", "apply_patches.py")
KAPSO_GATE_PATCHER = os.path.join(REPO, "openclaw-patches", "patch_kapso_gate.py")
ENV_FILE = os.path.join(REPO, ".env")
POLLER = os.path.join(REPO, "scripts", "kapso_poller.py")
POLLER_LOG = os.path.join(REPO, "progress", "kapso-poller.log")
BRIDGE_JS = os.path.join(REPO, "label-bridge", "bridge.js")
BRIDGE_CREDS = os.path.join(REPO, "label-bridge", "auth", "creds.json")
BRIDGE_QR = os.path.join(REPO, "progress", "label-bridge-qr.png")
BRIDGE_LOG = os.path.join(REPO, "progress", "label-bridge.log")
OPENCLAW_CONF = os.path.expanduser("~/.openclaw/openclaw.json")
ADMINS_FILE = os.path.join(REPO, "workspace", "data", "admins.json")
OWNER_NUMBER = "+923362615506"   # always an admin; cannot be removed
NODE_BIN = "/usr/local/node-v22.21.1/bin"
# When running under supervisor (Docker), drive the gateway via supervisorctl
# instead of spawning/killing it directly.
SUPERVISOR_NAME = os.environ.get("GATEWAY_SUPERVISOR")  # e.g. "gateway"
SUPERVISOR_CONF = os.environ.get("SUPERVISOR_CONF", "")
PASSWORD_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "admin_password.txt")
HOST = os.environ.get("ADMIN_HOST", "0.0.0.0")
PORT = int(os.environ.get("ADMIN_PORT", "8088"))

app = Flask(__name__)


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'"
    return response


@app.route("/healthz")
def healthz():
    """Container liveness only; intentionally contains no private state."""
    return jsonify({"ok": True}), 200


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


# ---- transport / poller ----------------------------------------------
def _env_value(key):
    try:
        with open(ENV_FILE) as f:
            for line in f:
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass
    return ""


def wa_transport():
    return (_env_value("WA_TRANSPORT") or "baileys").lower()


def poller_running():
    try:
        out = subprocess.run(["pgrep", "-f", "kapso_poller.py"], capture_output=True, text=True, timeout=5)
        return bool(out.stdout.strip())
    except Exception:
        return False


def start_poller_if_needed():
    """Kapso + no public URL means the polling relay IS the inbound path —
    a dead poller is a silent, total inbound outage."""
    if wa_transport() != "kapso" or _env_value("KAPSO_PUBLIC_URL") or poller_running():
        return
    logf = open(POLLER_LOG, "a")
    subprocess.Popen(["python3", POLLER], cwd=REPO, stdout=logf, stderr=logf, start_new_session=True)


# ---- process control -------------------------------------------------
def gateway_pids():
    # 2026.4.9 names the process "openclaw-gateway"; 2026.6.x names it "openclaw"
    pids = []
    try:
        out = subprocess.run(["pgrep", "-x", "openclaw"], capture_output=True, text=True, timeout=5)
        pids += [int(p) for p in out.stdout.split() if p.strip()]
    except Exception:
        pass
    try:
        out = subprocess.run(["pgrep", "-f", "openclaw-gateway"], capture_output=True, text=True, timeout=5)
        pids += [int(p) for p in out.stdout.split() if p.strip()]
    except Exception:
        pass
    return sorted(set(pids))


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

    # connected = a channel-up line appears after the most recent restart/exit
    # (Baileys: "Listening for personal"; Kapso: "registered Kapso webhook route")
    connected = False
    last_listen = max((i for i, l in enumerate(lines)
                       if "Listening for personal" in l or "registered Kapso webhook route" in l), default=-1)
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
    inbound = [l for l in lines if "Inbound message" in l or "webhook dispatch" in l]
    if inbound:
        tm = re.search(r"(\d{4}-\d{2}-\d{2}T[\d:]+)", inbound[-1])
        last_inbound = tm.group(1) if tm else None

    transport = wa_transport()
    status = {
        "running": running,
        "connected": connected,
        "model": model,
        "pid": pids[0] if pids else None,
        "uptime_seconds": uptime,
        "last_inbound": last_inbound,
        "transport": transport,
    }
    if transport == "kapso" and not _env_value("KAPSO_PUBLIC_URL"):
        status["poller_running"] = poller_running()
        if running and not status["poller_running"]:
            status["connected"] = False  # inbound is dead without the relay
    return status


def _supervisorctl(action):
    cmd = ["supervisorctl"]
    if SUPERVISOR_CONF:
        cmd += ["-c", SUPERVISOR_CONF]
    cmd += [action, SUPERVISOR_NAME]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def start_gateway():
    if is_running():
        return {"ok": True, "message": "Already running"}
    if SUPERVISOR_NAME:
        _supervisorctl("start")
        for _ in range(20):
            if is_running():
                break
            time.sleep(0.5)
        return {"ok": is_running(), "message": "Starting…"}
    env = os.environ.copy()
    env["PATH"] = NODE_BIN + ":" + env.get("PATH", "")
    env["OPENCLAW_AUTO_UPDATE"] = "0"  # never auto-update (would wipe our patches)
    # ensure patches are applied (idempotent) before launch:
    # - apply_patches.py: Baileys runtime patches (no-ops on non-2026.4.9 installs)
    # - patch_kapso_gate.py: category gate for the kapso plugin (no-ops if absent)
    for patcher in (PATCHER, KAPSO_GATE_PATCHER):
        try:
            subprocess.run(["python3", patcher], cwd=REPO, env=env, capture_output=True, text=True, timeout=60)
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
    try:
        start_poller_if_needed()
    except Exception:
        pass
    # resume label sync too, but only if already paired (never surprise-generate a QR)
    try:
        if os.path.exists(BRIDGE_CREDS) and not bridge_running():
            start_bridge()
    except Exception:
        pass
    return {"ok": is_running(), "message": "Starting…"}


def stop_gateway():
    subprocess.run(["pkill", "-f", "kapso_poller.py"], capture_output=True)
    if SUPERVISOR_NAME:
        _supervisorctl("stop")
        time.sleep(2)
        return {"ok": not is_running(), "message": "Stopped"}
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


# ---- label bridge (WhatsApp linked device for labels) ----------------
def bridge_running():
    try:
        out = subprocess.run(["pgrep", "-f", "label-bridge/bridge.js"], capture_output=True, text=True, timeout=5)
        return bool(out.stdout.strip())
    except Exception:
        return False


def bridge_status():
    """running = process up; qr_available = a QR is waiting to be scanned;
    linked = we have saved credentials and no QR is pending (i.e. already paired)."""
    running = bridge_running()
    qr = os.path.exists(BRIDGE_QR)
    linked = os.path.exists(BRIDGE_CREDS) and not qr
    connected = False
    if running and linked:
        # the bridge logs this line once paired; treat a recent one as connected
        for l in reversed(tail(BRIDGE_LOG, 60)):
            if "connected as linked device" in l:
                connected = True
                break
    return {"running": running, "linked": linked, "qr_available": qr, "connected": connected}


def start_bridge():
    """Start the label bridge (needed for first pairing so it emits a QR, and to
    resume label sync). Idempotent."""
    if bridge_running():
        return {"ok": True, "message": "already running"}
    if not os.path.exists(BRIDGE_JS):
        return {"ok": False, "message": "label bridge not installed"}
    env = os.environ.copy()
    env["PATH"] = NODE_BIN + ":" + env.get("PATH", "")
    os.makedirs(os.path.dirname(BRIDGE_LOG), exist_ok=True)
    logf = open(BRIDGE_LOG, "a")
    subprocess.Popen(["node", BRIDGE_JS], cwd=REPO, env=env, stdout=logf, stderr=logf, start_new_session=True)
    # wait briefly for a QR to appear (first pairing) or for it to come up
    for _ in range(24):
        if os.path.exists(BRIDGE_QR) or bridge_running():
            break
        time.sleep(0.5)
    return {"ok": bridge_running(), "message": "starting"}


# ---- access control (who can message the bot) ------------------------
def read_access():
    """Current inbound policy from the live gateway config.
    mode 'open' = bot replies to everyone; 'allowlist' = only listed numbers."""
    try:
        ch = json.load(open(OPENCLAW_CONF)).get("channels", {}).get("kapso-whatsapp", {})
        mode = (ch.get("dmSecurity") or "allowlist").lower()
        nums, seen = [], set()
        for n in ch.get("allowFrom", []):
            d = re.sub(r"\D", "", str(n))
            if d and d not in seen:
                seen.add(d)
                nums.append("+" + d)
        return {"mode": mode, "numbers": nums}
    except Exception as e:
        return {"mode": "unknown", "numbers": [], "error": str(e)}


def read_admins():
    """Handoff-alert recipients (workspace/data/admins.json). Owner always first."""
    nums = []
    try:
        nums = json.load(open(ADMINS_FILE)).get("admins", [])
    except Exception:
        nums = []
    out, seen = [], set()
    for n in [OWNER_NUMBER] + list(nums):
        d = re.sub(r"\D", "", str(n))
        if d and d not in seen:
            seen.add(d)
            out.append("+" + d)
    return out or [OWNER_NUMBER]


def write_admins(numbers):
    """Persist the admin list (owner always kept). Atomic."""
    out, seen = [], set()
    for n in [OWNER_NUMBER] + list(numbers):
        d = re.sub(r"\D", "", str(n))
        if d and d not in seen:
            seen.add(d)
            out.append("+" + d)
    tmp = ADMINS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"admins": out}, f, indent=2)
    os.replace(tmp, ADMINS_FILE)
    return out


def write_access(mode, numbers):
    """Write the inbound policy to the live gateway config (atomic). Stores each
    number in both +E.164 and digits-only form (Kapso delivers digits-only)."""
    cfg = json.load(open(OPENCLAW_CONF))
    ch = cfg.setdefault("channels", {}).setdefault("kapso-whatsapp", {})
    ch["dmSecurity"] = "open" if mode == "open" else "allowlist"
    af, seen = [], set()
    for n in numbers:
        d = re.sub(r"\D", "", str(n))
        if not d or d in seen:
            continue
        seen.add(d)
        af += ["+" + d, d]
    ch["allowFrom"] = af  # kept even in 'open' mode, so toggling back restores the list
    tmp = OPENCLAW_CONF + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, OPENCLAW_CONF)


def restart_gateway():
    stop_gateway()
    return start_gateway()


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
            "SELECT phone, name, email, category, cadence_status, lead_score, status, "
            "first_contact_at, last_message_at, notes "
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


def load_leads():
    try:
        conn = sqlite3.connect(f"file:{DB_FILE}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, captured_at, phone, name, email, products_of_interest, pain_point, "
            "intent, lead_score, score_tier, status, notes "
            "FROM leads ORDER BY captured_at DESC LIMIT 300"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ---- routes ----------------------------------------------------------
@app.route("/api/status")
@require_auth
def api_status():
    s = gateway_status()
    s["bridge"] = bridge_status()
    return jsonify(s)


@app.route("/api/qr")
@require_auth
def api_qr():
    """Serve the current WhatsApp-linking QR image if one is waiting."""
    if os.path.exists(BRIDGE_QR):
        try:
            with open(BRIDGE_QR, "rb") as f:
                return Response(f.read(), mimetype="image/png")
        except Exception:
            pass
    return Response("no qr", 404)


@app.route("/api/link-whatsapp", methods=["POST"])
@require_auth
def api_link_whatsapp():
    """Start the label bridge so it emits a QR to scan (first pairing / re-link)."""
    return jsonify(start_bridge())


@app.route("/api/access")
@require_auth
def api_access():
    a = read_access()
    a["admins"] = read_admins()
    return jsonify(a)


@app.route("/api/admins", methods=["POST"])
@require_auth
def api_set_admins():
    """Add/remove handoff-alert recipients. Owner is always kept. No gateway restart needed."""
    data = request.get_json(silent=True) or {}
    clean = []
    for n in (data.get("numbers") or []):
        d = re.sub(r"\D", "", str(n))
        if not re.fullmatch(r"\d{6,15}", d):
            return jsonify({"ok": False, "message": f"invalid number: {n}"}), 400
        clean.append("+" + d)
    try:
        saved = write_admins(clean)
        return jsonify({"ok": True, "admins": saved, "message": f"Saved {len(saved)} admin number(s)."})
    except Exception as e:
        return jsonify({"ok": False, "message": "save failed: " + str(e)}), 500


@app.route("/api/access", methods=["POST"])
@require_auth
def api_set_access():
    data = request.get_json(silent=True) or {}
    mode = (data.get("mode") or "").strip().lower()
    if mode not in ("open", "allowlist"):
        return jsonify({"ok": False, "message": "mode must be 'open' or 'allowlist'"}), 400
    clean, seen = [], set()
    for n in (data.get("numbers") or []):
        d = re.sub(r"\D", "", str(n))
        if not re.fullmatch(r"\d{6,15}", d):
            return jsonify({"ok": False, "message": f"invalid number: {n}"}), 400
        if d not in seen:
            seen.add(d)
            clean.append("+" + d)
    if mode == "allowlist" and not clean:
        return jsonify({"ok": False, "message": "Limited mode needs at least one number"}), 400
    try:
        write_access(mode, clean)
    except Exception as e:
        return jsonify({"ok": False, "message": "config write failed: " + str(e)}), 500
    r = restart_gateway()  # reload the new policy (also re-applies the category gate)
    return jsonify({"ok": bool(r.get("ok")), "mode": mode, "count": len(clean),
                    "message": "Saved and bot restarted." if r.get("ok") else "Saved, but bot restart is still coming up — check status."})


@app.route("/api/customers")
@require_auth
def api_customers():
    return jsonify(load_customers())


@app.route("/api/leads")
@require_auth
def api_leads():
    return jsonify({"leads": load_leads()})


@app.route("/api/set-category", methods=["POST"])
@require_auth
def api_set_category():
    data = request.get_json(silent=True) or {}
    phone = (data.get("phone") or "").strip()
    category = (data.get("category") or "").strip().lower()
    if not re.fullmatch(r"\+\d{6,15}", phone):
        return jsonify({"ok": False, "message": "Invalid phone"}), 400
    if category not in CATEGORIES:
        return jsonify({"ok": False, "message": "Invalid category"}), 400
    try:
        r = subprocess.run(
            ["python3", DB_PY, "set-category", "--phone", phone, "--category", category],
            capture_output=True, text=True, timeout=30, cwd=REPO,
        )
        ok = r.returncode == 0
        msg = (r.stdout + r.stderr).strip()[-300:] or ("Moved to " + category)
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 500)
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


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
<title>renovate.pk CRM</title>
<style>
  :root { --bg:#0f1419; --card:#1a2129; --line:#2a3441; --txt:#e6edf3; --muted:#8b98a5;
          --green:#2ea043; --red:#da3633; --amber:#d29922; --blue:#388bfd;
          --purple:#a371f7; --orange:#f0883e; --teal:#39c5cf; --gray:#6e7681; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:system-ui,Segoe UI,Roboto,sans-serif; background:var(--bg); color:var(--txt); }
  .wrap { max-width:1180px; margin:0 auto; padding:20px 16px 60px; }
  h1 { font-size:20px; margin:0; } .sub { color:var(--muted); font-size:13px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; margin-bottom:14px; }
  .topbar { display:flex; align-items:center; gap:14px; flex-wrap:wrap; }
  .dot { width:13px; height:13px; border-radius:50%; display:inline-block; flex:none; }
  .badge { font-weight:700; font-size:16px; }
  .meta { color:var(--muted); font-size:12.5px; display:flex; gap:14px; flex-wrap:wrap; margin-top:8px; }
  .grow { flex:1; }
  button { border:0; border-radius:8px; padding:10px 16px; font-size:14px; font-weight:600; cursor:pointer; color:#fff; background:var(--gray); }
  button:disabled { opacity:.4; cursor:not-allowed; }
  .start { background:var(--green); } .stop { background:var(--red); }
  .ghost { background:transparent; border:1px solid var(--line); color:var(--txt); font-weight:500; }

  /* connections */
  .conn { display:flex; align-items:center; gap:10px; padding:7px 0; font-size:14px; color:var(--txt); }
  .conn .cdot2 { width:11px; height:11px; border-radius:50%; flex:none; }
  .conn .cname { min-width:190px; }
  .conn .cstate { color:var(--muted); font-size:13px; }
  .ok2 { background:var(--green); } .warn2 { background:var(--amber); } .bad2 { background:var(--red); }

  /* access control */
  .accopt { display:block; padding:6px 0; font-size:14px; cursor:pointer; }
  .accopt input { margin-right:8px; }
  .numlist { display:flex; flex-wrap:wrap; gap:8px; }
  .numpill { display:inline-flex; align-items:center; gap:8px; background:rgba(255,255,255,.05);
             border:1px solid var(--line); border-radius:20px; padding:6px 12px; font-size:13px; }
  .numpill button { background:transparent; color:var(--muted); border:0; padding:0 2px; cursor:pointer; font-size:16px; line-height:1; }
  input[type=text] { background:var(--bg); color:var(--txt); border:1px solid var(--line); border-radius:8px; padding:9px 11px; font-size:14px; }
  input[type=text]::placeholder { color:var(--muted); }

  /* label chips */
  .chips { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
  .chip { display:flex; align-items:center; gap:8px; padding:8px 13px; border-radius:20px; cursor:pointer;
          border:1px solid var(--line); background:transparent; font-size:13px; color:var(--txt); user-select:none; }
  .chip .cdot { width:9px; height:9px; border-radius:50%; }
  .chip .cnt { font-weight:800; }
  .chip.on { border-color:#fff; background:rgba(255,255,255,.08); }
  .chipsep { color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.5px; margin:0 2px; }

  /* colors per label */
  .c-new  { --c:var(--blue); }   .c-imp  { --c:var(--amber); }
  .c-hot  { --c:var(--red); }    .c-fol  { --c:var(--purple); }
  .c-junk { --c:var(--gray); }   .c-comp { --c:var(--orange); }
  .c-team { --c:var(--teal); }   .c-vend { --c:#9a6700; }
  .cdot { background:var(--c); }

  /* toolbar */
  .toolbar { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:12px; }
  input[type=search] { flex:1; min-width:200px; background:var(--bg); color:var(--txt);
      border:1px solid var(--line); border-radius:8px; padding:10px 12px; font-size:14px; }
  input[type=search]::placeholder { color:var(--muted); }

  /* table */
  .tablewrap { overflow-x:auto; }
  table { width:100%; border-collapse:collapse; font-size:13.5px; white-space:nowrap; }
  th,td { text-align:left; padding:9px 10px; border-bottom:1px solid var(--line); }
  th { color:var(--muted); font-weight:600; font-size:11.5px; text-transform:uppercase; cursor:pointer; user-select:none; position:sticky; top:0; background:var(--card); }
  th .arrow { font-size:10px; }
  td.notes { max-width:220px; overflow:hidden; text-overflow:ellipsis; color:var(--muted); }
  a.phone { color:#79c0ff; text-decoration:none; } a.phone:hover { text-decoration:underline; }
  .nm { font-weight:600; } .em { color:var(--muted); font-size:11.5px; }
  .foot { color:var(--muted); font-size:12px; }
  .score { font-weight:700; }

  /* label select styled as pill */
  select.pill { appearance:none; -webkit-appearance:none; border-radius:20px; padding:4px 26px 4px 12px; font-size:12.5px; font-weight:600;
      border:1px solid var(--c,var(--line)); color:var(--c,var(--txt)); background:rgba(255,255,255,.03); cursor:pointer;
      background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='8' height='6'><path d='M0 0l4 6 4-6z' fill='%238b98a5'/></svg>");
      background-repeat:no-repeat; background-position:right 9px center; }
  select.pill option { background:var(--card); color:var(--txt); }

  .silence { color:var(--amber); font-size:11px; margin-left:6px; }

  /* toast */
  #toast { position:fixed; bottom:20px; left:50%; transform:translateX(-50%); background:#000; color:#fff;
      border:1px solid var(--line); border-radius:10px; padding:11px 18px; font-size:14px; opacity:0;
      transition:opacity .25s; pointer-events:none; max-width:90vw; z-index:50; }
  #toast.show { opacity:.96; }
  #toast.err { background:#3b1113; border-color:var(--red); }

  h2 { font-size:15px; margin:0 0 10px; }
  @media (max-width:700px){ .hidemob { display:none; } }
</style></head><body><div class="wrap">

  <div class="card">
    <div class="topbar">
      <span class="dot" id="dot" style="background:var(--muted)"></span>
      <div>
        <h1>renovate.pk — CRM &amp; Bot Admin</h1>
        <div class="sub"><span class="badge" id="badge">Checking…</span> <span id="wa"></span></div>
      </div>
      <div class="grow"></div>
      <button class="start" id="startBtn" onclick="ctl('start')">▶ Start bot</button>
      <button class="stop" id="stopBtn" onclick="ctl('stop')">■ Stop bot</button>
    </div>
    <div class="meta" id="meta"></div>
  </div>

  <div class="card">
    <h2 style="margin:0 0 10px">Connections</h2>
    <div id="connList" class="foot">Checking…</div>
    <div id="qrPanel" style="display:none; margin-top:14px; text-align:center">
      <div class="foot" style="margin-bottom:8px">
        <b>Scan to link WhatsApp for labels.</b><br>
        On your phone: WhatsApp Business → ⋮ or Settings → <b>Linked devices</b> → <b>Link a device</b> → scan this.
      </div>
      <img id="qrImg" alt="WhatsApp QR" width="240" height="240"
           style="background:#fff; border-radius:10px; padding:8px">
    </div>
    <button class="ghost" id="linkBtn" style="display:none; margin-top:10px" onclick="linkWhatsApp()">🔗 Link WhatsApp (show QR)</button>
  </div>

  <div class="card">
    <h2 style="margin:0 0 10px">Who can message the bot</h2>
    <label class="accopt"><input type="radio" name="accmode" value="open" onchange="onModeChange()"> <b>Allow everyone</b> — the bot replies to <b>every</b> number that messages it</label>
    <label class="accopt"><input type="radio" name="accmode" value="allowlist" onchange="onModeChange()"> <b>Limited access</b> — only the numbers below can talk to the bot</label>
    <div id="allowBox" style="margin:10px 0 4px">
      <div id="allowNums" class="numlist"></div>
      <div style="display:flex; gap:8px; margin-top:10px">
        <input type="text" id="newNum" placeholder="+9230XXXXXXXX" onkeydown="if(event.key==='Enter')addNum()">
        <button class="ghost" onclick="addNum()">+ Add number</button>
      </div>
    </div>
    <div class="foot" style="margin-top:8px">Changes take effect after you press Apply — which <b>restarts the bot</b> (a few seconds of downtime).</div>
    <div style="margin-top:10px">
      <button class="start" id="applyAccBtn" onclick="applyAccess()">Apply &amp; restart bot</button>
    </div>
    <div style="margin-top:14px; border-top:1px solid var(--line); padding-top:12px">
      <b style="font-size:14px">Admin numbers</b> <span class="foot">(receive handoff alerts — 🔥 hot leads, 📷 media, 😠 complaints, 🛒 orders)</span>
      <div id="adminNums" class="numlist" style="margin-top:10px"></div>
      <div style="display:flex; gap:8px; margin-top:10px">
        <input type="text" id="newAdmin" placeholder="+9230XXXXXXXX" onkeydown="if(event.key==='Enter')addAdmin()">
        <button class="ghost" onclick="addAdmin()">+ Add admin</button>
        <button class="start" id="saveAdminsBtn" onclick="saveAdmins()">Save admins</button>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="chips" id="chips"></div>
  </div>

  <div class="card">
    <div class="toolbar">
      <h2 style="margin:0">Customers</h2>
      <span class="foot" id="total"></span>
      <div class="grow"></div>
      <input type="search" id="q" placeholder="Search name, number, notes…" oninput="render()">
      <button class="ghost" onclick="exportCSV()">⬇ CSV</button>
    </div>
    <div class="tablewrap">
      <table>
        <thead><tr>
          <th data-k="name">Customer <span class="arrow"></span></th>
          <th data-k="phone">Number <span class="arrow"></span></th>
          <th data-k="category">Label <span class="arrow"></span></th>
          <th data-k="first_contact_at" class="hidemob">First contact <span class="arrow"></span></th>
          <th data-k="last_message_at">Last seen <span class="arrow"></span></th>
          <th data-k="notes" class="hidemob">Notes <span class="arrow"></span></th>
        </tr></thead>
        <tbody id="rows"><tr><td colspan="6" class="foot">Loading…</td></tr></tbody>
      </table>
    </div>
    <div class="foot" style="margin-top:8px">Change a customer's label with the dropdown — it updates WhatsApp labels too.
      Labels <b>Hot lead / Complaint / Junk / team member</b> silence the bot on that chat until you move it back.</div>
  </div>

  <div class="card">
    <div class="toolbar"><h2 style="margin:0">Captured leads</h2><span class="foot" id="leadtotal"></span></div>
    <div class="tablewrap">
      <table>
        <thead><tr><th>Date</th><th>Name</th><th>Number</th><th>Interested in</th><th class="hidemob">Intent</th><th>Score</th><th class="hidemob">Status</th></tr></thead>
        <tbody id="leadrows"><tr><td colspan="7" class="foot">Loading…</td></tr></tbody>
      </table>
    </div>
  </div>

  <div class="foot">Auto-refreshes every 5s (paused while you're choosing a label). Conversations are private and not shown here.</div>
</div>
<div id="toast"></div>
<script>
const CAT = {
  'new customer': {label:'New customer', cls:'c-new'},
  'important':    {label:'Important',    cls:'c-imp'},
  'hot leads':    {label:'Hot lead',     cls:'c-hot'},
  'followup':     {label:'Follow-up',    cls:'c-fol'},
  'junk':         {label:'Junk',         cls:'c-junk'},
  'complaints':   {label:'Complaint',    cls:'c-comp'},
  'vendor':       {label:'Vendor',       cls:'c-vend'},
  'ahsan':        {label:'Ahsan',        cls:'c-team', team:true},
  'ahmed':        {label:'Ahmed',        cls:'c-team', team:true},
  'imran':        {label:'Imran',        cls:'c-team', team:true},
  'rafay':        {label:'Rafay',        cls:'c-team', team:true},
};
const SILENT = new Set(['hot leads','complaints','junk','vendor','ahsan','ahmed','imran','rafay']);
let DATA = {customers:[], counts:{}, categories:Object.keys(CAT), total:0};
let LEADS = [];
let filter = 'all';
let sortK = 'last_message_at', sortDir = -1;
// Pause table re-render ONLY while a label dropdown is actually focused, computed
// live from the DOM so it can never get "stuck" and freeze updates (old bug:
// a boolean that stayed true kept the dashboard showing stale rows).
function dropdownOpen(){ const a=document.activeElement; return !!(a && a.tagName==='SELECT' && a.classList.contains('pill')); }

function esc(v){ return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function fmtUptime(s){ if(s==null)return''; let h=Math.floor(s/3600),m=Math.floor(s%3600/60); return h?`${h}h ${m}m`:`${m}m`; }
function catMeta(c){ return CAT[(c||'').toLowerCase()] || {label:c||'—', cls:''}; }
function rel(ts){
  if(!ts) return '—';
  const d = new Date(ts); if(isNaN(d)) return esc(ts);
  const s = (Date.now()-d.getTime())/1000;
  if(s<60) return 'just now';
  if(s<3600) return Math.floor(s/60)+'m ago';
  if(s<86400) return Math.floor(s/3600)+'h ago';
  if(s<86400*30) return Math.floor(s/86400)+'d ago';
  return d.toISOString().slice(0,10);
}
function fullDate(ts){ if(!ts) return ''; const d=new Date(ts); return isNaN(d)?String(ts):d.toLocaleString(); }
function toast(msg, err){
  const t=document.getElementById('toast'); t.textContent=msg;
  t.className='show'+(err?' err':''); clearTimeout(t._h);
  t._h=setTimeout(()=>t.className='',3000);
}

function chips(){
  const c = DATA.counts||{};
  let h = `<span class="chip ${filter==='all'?'on':''}" onclick="setFilter('all')"><span class="cnt">${DATA.total||0}</span> All numbers</span>`;
  const main = DATA.categories.filter(k=>!CAT[k]?.team), team = DATA.categories.filter(k=>CAT[k]?.team);
  for(const k of main){
    const m = catMeta(k);
    h += `<span class="chip ${m.cls} ${filter===k?'on':''}" onclick="setFilter('${esc(k)}')"><span class="cdot"></span><span class="cnt">${c[k]??0}</span> ${esc(m.label)}s</span>`;
  }
  h += `<span class="chipsep">team</span>`;
  for(const k of team){
    const m = catMeta(k);
    h += `<span class="chip ${m.cls} ${filter===k?'on':''}" onclick="setFilter('${esc(k)}')"><span class="cdot"></span><span class="cnt">${c[k]??0}</span> ${esc(m.label)}</span>`;
  }
  document.getElementById('chips').innerHTML = h;
}
function setFilter(f){ filter=f; render(); }

function visibleRows(){
  const q = (document.getElementById('q').value||'').toLowerCase().trim();
  let rows = (DATA.customers||[]).slice();
  if(filter!=='all') rows = rows.filter(x=>(x.category||'').toLowerCase()===filter);
  if(q) rows = rows.filter(x=>[x.name,x.phone,x.email,x.notes,x.category,x.status].some(v=>String(v||'').toLowerCase().includes(q)));
  rows.sort((a,b)=>{
    let av=a[sortK], bv=b[sortK];
    if(sortK==='lead_score'){ av=av==null?-1:+av; bv=bv==null?-1:+bv; }
    else { av=String(av??'').toLowerCase(); bv=String(bv??'').toLowerCase(); }
    return (av<bv?-1:av>bv?1:0)*sortDir;
  });
  return rows;
}

function render(){
  chips();
  const rows = visibleRows();
  document.getElementById('total').textContent = rows.length + ' of ' + (DATA.total||0);
  document.querySelectorAll('th[data-k]').forEach(th=>{
    th.querySelector('.arrow').textContent = th.dataset.k===sortK ? (sortDir<0?'▼':'▲') : '';
  });
  const opts = DATA.categories.map(k=>`<option value="${esc(k)}">${esc(catMeta(k).label)}</option>`).join('');
  const html = rows.map(x=>{
    const m = catMeta(x.category);
    const digits = String(x.phone||'').replace(/\D/g,'');
    const silent = SILENT.has((x.category||'').toLowerCase());
    return `<tr>
      <td><span class="nm">${esc(x.name||'—')}</span>${x.email?`<div class="em">${esc(x.email)}</div>`:''}</td>
      <td><a class="phone" href="https://wa.me/${digits}" target="_blank" rel="noopener">${esc(x.phone||'')}</a></td>
      <td><select class="pill ${m.cls}" data-phone="${esc(x.phone)}" onchange="changeCat(this)">
            ${opts}
          </select>${silent?'<span class="silence" title="Bot is silent on this chat">⏸</span>':''}</td>
      <td class="hidemob foot" title="${esc(fullDate(x.first_contact_at))}">${esc((x.first_contact_at||'').slice(0,10)||'—')}</td>
      <td title="${esc(fullDate(x.last_message_at))}">${rel(x.last_message_at)}</td>
      <td class="notes hidemob" title="${esc(x.notes||'')}">${esc(x.notes||'')}</td>
    </tr>`;
  }).join('');
  document.getElementById('rows').innerHTML = html || '<tr><td colspan="6" class="foot">No customers match.</td></tr>';
  // set current value on each select (can't do it inline safely)
  document.querySelectorAll('select.pill').forEach(sel=>{
    const row = rows.find(r=>r.phone===sel.dataset.phone);
    if(row) sel.value = (row.category||'').toLowerCase();
  });
}

async function changeCat(sel){
  const phone = sel.dataset.phone, cat = sel.value;
  const cur = (DATA.customers.find(c=>c.phone===phone)||{}).category;
  const m = catMeta(cat);
  let msg = `Move ${phone} to "${m.label}"?`;
  if(SILENT.has(cat)) msg += `\n\nThe bot will STOP replying on this chat until you move it back to New customer / Important / Follow-up.`;
  else if(SILENT.has((cur||'').toLowerCase())) msg += `\n\nThe bot will START replying on this chat again.`;
  if(!confirm(msg)){ sel.value=(cur||'').toLowerCase(); sel.blur(); return; }
  sel.disabled = true;
  try{
    const r = await fetch('/api/set-category',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone,category:cat})});
    const j = await r.json();
    if(j.ok){ toast(`${phone} → ${m.label}`); const row=DATA.customers.find(c=>c.phone===phone); if(row) row.category=cat; }
    else { toast('Failed: '+(j.message||'unknown error'), true); sel.value=(cur||'').toLowerCase(); }
  }catch(e){ toast('Network error — label not changed', true); sel.value=(cur||'').toLowerCase(); }
  sel.disabled = false;
  sel.blur();          // release focus so the refresh below can re-render immediately
  refresh();           // pull fresh server truth and repaint (fixes stale-row bug)
}

function renderLeads(){
  document.getElementById('leadtotal').textContent = LEADS.length ? LEADS.length+' total' : '';
  const html = LEADS.map(l=>`<tr>
      <td class="foot" title="${esc(fullDate(l.captured_at))}">${esc((l.captured_at||'').slice(0,10))}</td>
      <td class="nm">${esc(l.name||'—')}</td>
      <td><a class="phone" href="https://wa.me/${String(l.phone||'').replace(/\D/g,'')}" target="_blank" rel="noopener">${esc(l.phone||'')}</a></td>
      <td class="notes">${esc(l.products_of_interest||'—')}</td>
      <td class="hidemob">${esc(l.intent||'—')}</td>
      <td class="score">${l.lead_score??'—'}${l.score_tier?` <span class="foot">(${esc(l.score_tier)})</span>`:''}</td>
      <td class="hidemob">${esc(l.status||'—')}</td>
    </tr>`).join('');
  document.getElementById('leadrows').innerHTML = html || '<tr><td colspan="7" class="foot">No leads captured yet.</td></tr>';
}

function exportCSV(){
  const rows = visibleRows();
  const cols = ['name','phone','email','category','first_contact_at','last_message_at','notes'];
  const csv = [cols.join(',')].concat(rows.map(r=>cols.map(c=>{
    let v = String(r[c]??'').replace(/"/g,'""');
    return /[",\n]/.test(v) ? `"${v}"` : v;
  }).join(','))).join('\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv],{type:'text/csv'}));
  a.download = 'customers.csv'; a.click(); URL.revokeObjectURL(a.href);
}

document.querySelectorAll('th[data-k]').forEach(th=>th.onclick=()=>{
  const k = th.dataset.k;
  if(sortK===k) sortDir*=-1; else { sortK=k; sortDir = (k==='last_message_at'||k==='first_contact_at'||k==='lead_score')?-1:1; }
  render();
});

async function refresh(){
  try{
    const s = await (await fetch('/api/status')).json();
    const dot=document.getElementById('dot'), badge=document.getElementById('badge');
    if(s.running){ dot.style.background='var(--green)'; badge.textContent='ONLINE'; }
    else { dot.style.background='var(--red)'; badge.textContent='OFFLINE'; }
    document.getElementById('wa').textContent = s.running ? (s.connected?'• WhatsApp connected':'• connecting…') : '';
    document.getElementById('meta').innerHTML =
      [ s.transport?('WhatsApp: '+esc(s.transport)):'',
        s.poller_running!==undefined?('Poller: '+(s.poller_running?'running':'<span style="color:var(--red);font-weight:700">DOWN — bot cannot receive messages</span>')):'',
        s.model?('Model: '+esc(s.model)):'', s.uptime_seconds!=null?('Uptime: '+fmtUptime(s.uptime_seconds)):'',
        s.last_inbound?('Last message: '+esc(s.last_inbound.replace('T',' '))):'' ].filter(Boolean).join(' &nbsp;|&nbsp; ');
    document.getElementById('startBtn').disabled = s.running;
    document.getElementById('stopBtn').disabled = !s.running;
    renderConnections(s);
  }catch(e){}
  try{
    const c = await (await fetch('/api/customers')).json();
    DATA = c;
    if(!dropdownOpen()) render();
  }catch(e){}
  try{
    const l = await (await fetch('/api/leads')).json();
    LEADS = l.leads||[];
    if(!dropdownOpen()) renderLeads();
  }catch(e){}
}

function connRow(name, level, state){
  const cls = level==='ok'?'ok2':level==='warn'?'warn2':'bad2';
  return `<div class="conn"><span class="cdot2 ${cls}"></span><span class="cname">${esc(name)}</span><span class="cstate">${esc(state)}</span></div>`;
}
function renderConnections(s){
  const b = s.bridge || {};
  let rows = '';
  // 1) the bot itself (openclaw)
  if(s.running && s.connected) rows += connRow('Bot (openclaw)', 'ok', 'online & connected');
  else if(s.running)          rows += connRow('Bot (openclaw)', 'warn', 'starting / connecting…');
  else                        rows += connRow('Bot (openclaw)', 'bad', 'offline — press ▶ Start bot');
  // 2) WhatsApp inbound (Kapso poller), when applicable
  if(s.transport==='kapso' && s.poller_running!==undefined){
    rows += s.poller_running ? connRow('WhatsApp messages (inbound)', 'ok', 'receiving')
                             : connRow('WhatsApp messages (inbound)', 'bad', 'down — bot cannot receive');
  }
  // 3) WhatsApp label link (Baileys linked device)
  if(b.qr_available)      rows += connRow('WhatsApp labels (linked device)', 'warn', 'not linked — scan the QR below');
  else if(b.running && b.linked) rows += connRow('WhatsApp labels (linked device)', 'ok', 'linked & syncing');
  else if(b.linked)       rows += connRow('WhatsApp labels (linked device)', 'warn', 'linked but not running');
  else                    rows += connRow('WhatsApp labels (linked device)', 'bad', 'not linked');
  document.getElementById('connList').innerHTML = rows;

  // QR / link button
  const qrPanel=document.getElementById('qrPanel'), linkBtn=document.getElementById('linkBtn'), qrImg=document.getElementById('qrImg');
  if(b.qr_available){
    qrImg.src = '/api/qr?t=' + Date.now();       // cache-bust; QR refreshes itself
    qrPanel.style.display = 'block';
    linkBtn.style.display = 'none';
  } else if(!b.linked){
    qrPanel.style.display = 'none';
    linkBtn.style.display = 'inline-block';
    linkBtn.disabled = false; linkBtn.textContent = '🔗 Link WhatsApp (show QR)';
  } else {
    qrPanel.style.display = 'none';
    linkBtn.style.display = 'none';
  }
}
async function linkWhatsApp(){
  const b=document.getElementById('linkBtn'); b.disabled=true; b.textContent='Starting… QR will appear';
  try{ await fetch('/api/link-whatsapp',{method:'POST'}); }catch(e){}
  setTimeout(refresh, 2000);
}

// ---- access control (who can message the bot) ----
let ACCESS = {mode:'allowlist', numbers:[], admins:[]};
async function loadAccess(){
  try{
    const a = await (await fetch('/api/access')).json();
    ACCESS = {mode:(a.mode==='open'?'open':'allowlist'), numbers:a.numbers||[], admins:a.admins||[]};
    renderAccess();
  }catch(e){}
}
const OWNER_NUMBER = '+923362615506';
function renderAdmins(){
  const el=document.getElementById('adminNums');
  el.innerHTML = (ACCESS.admins||[]).map(n=>{
    const owner = n===OWNER_NUMBER;
    return `<span class="numpill">${esc(n)}${owner?' <span class="foot">(owner)</span>':`<button title="Remove" onclick="removeAdmin('${esc(n)}')">×</button>`}</span>`;
  }).join('') || '<span class="foot">none</span>';
}
function addAdmin(){
  const el=document.getElementById('newAdmin'); const d=(el.value||'').replace(/\D/g,'');
  if(d.length<8){ toast('Enter a full number with country code', true); return; }
  const e='+'+d;
  if(!ACCESS.admins.includes(e)) ACCESS.admins.push(e);
  el.value=''; renderAdmins();
}
function removeAdmin(n){ if(n===OWNER_NUMBER) return; ACCESS.admins=ACCESS.admins.filter(x=>x!==n); renderAdmins(); }
async function saveAdmins(){
  const b=document.getElementById('saveAdminsBtn'); b.disabled=true; b.textContent='Saving…';
  try{
    const r=await fetch('/api/admins',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({numbers:ACCESS.admins})});
    const j=await r.json();
    if(j.ok){ ACCESS.admins=j.admins; renderAdmins(); toast(j.message||'Saved'); }
    else toast('Failed: '+(j.message||''), true);
  }catch(e){ toast('Network error', true); }
  b.disabled=false; b.textContent='Save admins';
}
function renderAccess(){
  document.querySelectorAll('input[name=accmode]').forEach(r=>{ r.checked=(r.value===ACCESS.mode); });
  renderAdmins();
  const box=document.getElementById('allowBox');
  const on = ACCESS.mode==='allowlist';
  box.style.opacity = on?'1':'.4'; box.style.pointerEvents = on?'auto':'none';
  document.getElementById('allowNums').innerHTML = (ACCESS.numbers||[]).map(n=>
    `<span class="numpill">${esc(n)}<button title="Remove" onclick="removeNum('${esc(n)}')">×</button></span>`).join('')
    || '<span class="foot">No numbers yet — add at least one.</span>';
}
function onModeChange(){ ACCESS.mode=document.querySelector('input[name=accmode]:checked').value; renderAccess(); }
function addNum(){
  const el=document.getElementById('newNum'); const d=(el.value||'').replace(/\D/g,'');
  if(d.length<8){ toast('Enter a full number with country code, e.g. +92300…', true); return; }
  const e='+'+d;
  if(!ACCESS.numbers.includes(e)) ACCESS.numbers.push(e);
  el.value=''; renderAccess();
}
function removeNum(n){ ACCESS.numbers=ACCESS.numbers.filter(x=>x!==n); renderAccess(); }
async function applyAccess(){
  const mode=document.querySelector('input[name=accmode]:checked').value;
  if(mode==='allowlist' && ACCESS.numbers.length===0){ toast('Add at least one number, or choose Allow everyone', true); return; }
  const msg = mode==='open'
    ? 'Allow EVERYONE to message the bot?\nIt will reply to any number that writes in.\n\nThis restarts the bot (a few seconds down).'
    : `Limit the bot to these ${ACCESS.numbers.length} number(s)? Everyone else is ignored.\n\nThis restarts the bot (a few seconds down).`;
  if(!confirm(msg)) return;
  const b=document.getElementById('applyAccBtn'); b.disabled=true; b.textContent='Applying… restarting bot';
  try{
    const r=await fetch('/api/access',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode,numbers:ACCESS.numbers})});
    const j=await r.json(); toast(j.message||(j.ok?'Applied':'Failed'), !j.ok);
  }catch(e){ toast('Network error while applying', true); }
  b.disabled=false; b.textContent='Apply & restart bot';
  setTimeout(()=>{ loadAccess(); refresh(); }, 3000);
}
async function ctl(action){
  const b=document.getElementById(action+'Btn'); b.disabled=true; b.textContent='…';
  try{ await fetch('/api/'+action,{method:'POST'}); }catch(e){}
  setTimeout(()=>{ refresh(); b.textContent = action==='start'?'▶ Start bot':'■ Stop bot'; }, 1500);
}
refresh(); loadAccess(); setInterval(refresh, 5000);
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
