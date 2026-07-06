"""
Shopee Sim Manager — Web App Backend
Flask server + Shopee API integration + SQLite database
"""

import hashlib
import hmac
import time
import json
import sqlite3
import threading
import webbrowser
import urllib.parse
import http.server
import os
import requests
from datetime import datetime, timedelta
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder="static")
CORS(app)

# ─── CONFIG ────────────────────────────────────────────────────────────────────
PARTNER_ID_LIVE  = 2037264
PARTNER_KEY_LIVE = "shpk7647596d72617a70676e77634b7a7148736b467349537575774d57657243"
PARTNER_ID_TEST  = 1236318
PARTNER_KEY_TEST = "shpk4b41694b50746f4c584e6f79716e776350754a4251594364734d6676684c"
REDIRECT_PORT    = 8899
WINDOW_DAYS      = 30

BASE_DIR   = Path(__file__).parent
import os
_DATA_DIR  = Path(os.environ.get("DATA_DIR", str(BASE_DIR)))
DB_PATH    = _DATA_DIR / "data.db"
TOKEN_PATH = _DATA_DIR / "tokens.json"
CFG_PATH   = _DATA_DIR / "config.json"

# in-memory auto-check state
checker_state = {"running": False, "last_run": None, "next_run": None, "interval": 300, "log": []}
checker_thread = None


# ─── HELPERS ───────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if CFG_PATH.exists():
        return json.loads(CFG_PATH.read_text())
    return {"env": "live", "shop_id": 0, "interval": 300}

def save_config(cfg: dict):
    CFG_PATH.write_text(json.dumps(cfg, indent=2))

def load_tokens() -> dict:
    if TOKEN_PATH.exists():
        return json.loads(TOKEN_PATH.read_text())
    return {}

def save_tokens(data: dict):
    TOKEN_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))

def get_keys():
    cfg = load_config()
    if cfg.get("env") == "test":
        return PARTNER_ID_TEST, PARTNER_KEY_TEST, "https://partner.test-stable.shopeemobile.com"
    return PARTNER_ID_LIVE, PARTNER_KEY_LIVE, "https://partner.shopeemobile.com"

def sign_request(path: str, ts: int, access_token: str = "", shop_id: int = 0) -> str:
    pid, key, _ = get_keys()
    base = f"{pid}{path}{ts}"
    if access_token:
        base += f"{access_token}{shop_id}"
    return hmac.new(key.encode(), base.encode(), hashlib.sha256).hexdigest()

def shopee_api(path: str, params: dict = None, body: dict = None, method="GET"):
    pid, _, base_url = get_keys()
    cfg = load_config()
    shop_id = cfg.get("shop_id", 0)
    tokens  = load_tokens()
    ts  = int(time.time())
    sig = sign_request(path, ts, tokens.get("access_token", ""), shop_id)
    p = {
        "partner_id":   pid,
        "timestamp":    ts,
        "access_token": tokens.get("access_token", ""),
        "shop_id":      shop_id,
        "sign":         sig,
    }
    if params:
        p.update(params)
    url = base_url + path
    r = requests.get(url, params=p, timeout=15) if method == "GET" else \
        requests.post(url, params=p, json=body, timeout=15)
    r.raise_for_status()
    return r.json()

def norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())

def today_iso() -> str:
    return datetime.now().date().isoformat()

def days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).isoformat()

def age_days(iso: str) -> int:
    try:
        d = datetime.fromisoformat(iso).date()
        return (datetime.now().date() - d).days
    except:
        return 0

def log_checker(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    entry = f"[{ts}] {msg}"
    checker_state["log"].insert(0, entry)
    checker_state["log"] = checker_state["log"][:200]
    print(entry)


# ─── DATABASE ──────────────────────────────────────────────────────────────────

def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS blacklist (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            order_sn   TEXT UNIQUE,
            user_id    TEXT,
            buyer_name TEXT,
            phone      TEXT,
            address    TEXT,
            violation  TEXT,
            reasons    TEXT,
            qty        INTEGER DEFAULT 1,
            sent_empty INTEGER DEFAULT 0,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS checked_orders (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            order_sn   TEXT UNIQUE,
            user_id    TEXT,
            buyer_name TEXT,
            phone      TEXT,
            address    TEXT,
            qty        INTEGER DEFAULT 1,
            status     TEXT,
            reasons    TEXT,
            checked_at TEXT
        );
        CREATE TABLE IF NOT EXISTS products (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id  TEXT UNIQUE,
            name     TEXT,
            stock    INTEGER DEFAULT 0,
            min_stock INTEGER DEFAULT 10,
            price    REAL DEFAULT 0,
            updated_at TEXT
        );
    """)
    con.commit()
    # seed products if empty
    if not con.execute("SELECT 1 FROM products LIMIT 1").fetchone():
        con.executemany("INSERT OR IGNORE INTO products (item_id,name,stock,min_stock,price,updated_at) VALUES (?,?,?,?,?,?)", [
            ("7891234560","ซิมเปล่า AIS 4G/5G",50,10,49,today_iso()),
            ("7891234561","ซิมเปล่า DTAC 4G",12,10,39,today_iso()),
            ("7891234562","ซิมเปล่า TRUE Move H",0,5,39,today_iso()),
        ])
        con.commit()
    con.close()

def get_db():
    return sqlite3.connect(DB_PATH)

def bl_window():
    cutoff = days_ago(WINDOW_DAYS)
    con = get_db()
    rows = con.execute(
        "SELECT user_id,buyer_name,address FROM blacklist WHERE created_at>=?", (cutoff,)
    ).fetchall()
    con.close()
    return [{"user_id":r[0],"name":r[1],"addr":r[2]} for r in rows]

def already_checked(sn: str) -> bool:
    con = get_db()
    r = con.execute("SELECT 1 FROM checked_orders WHERE order_sn=?", (sn,)).fetchone()
    con.close()
    return bool(r)

def save_bl(order, violation, reasons):
    con = get_db()
    con.execute("""INSERT OR IGNORE INTO blacklist
        (order_sn,user_id,buyer_name,phone,address,violation,reasons,qty,created_at)
        VALUES(?,?,?,?,?,?,?,?,?)""",
        (order["order_sn"],order.get("user_id",""),order.get("buyer_name",""),
         order.get("phone",""),order.get("address",""),violation,
         json.dumps(reasons,ensure_ascii=False),order.get("qty",1),
         datetime.now().isoformat()))
    con.commit(); con.close()

def save_checked(order, status, reasons=None):
    con = get_db()
    con.execute("""INSERT OR IGNORE INTO checked_orders
        (order_sn,user_id,buyer_name,phone,address,qty,status,reasons,checked_at)
        VALUES(?,?,?,?,?,?,?,?,?)""",
        (order["order_sn"],order.get("user_id",""),order.get("buyer_name",""),
         order.get("phone",""),order.get("address",""),order.get("qty",1),
         status,json.dumps(reasons or [],ensure_ascii=False),
         datetime.now().isoformat()))
    con.commit(); con.close()


# ─── ORDER CHECK LOGIC ─────────────────────────────────────────────────────────

def check_one(order: dict, window: list):
    reasons = []
    if order.get("qty",1) > 1:
        reasons.append(f"สั่ง {order['qty']} ชิ้น เกินกำหนด (1 ชิ้น/คน)")
    uid  = norm(order.get("user_id",""))
    name = norm(order.get("buyer_name",""))
    addr = norm(order.get("address",""))
    for b in window:
        if uid and uid == norm(b.get("user_id","")):
            reasons.append(f"User ID ซ้ำกับบัญชีดำ ({b['user_id']})")
        elif name and len(name)>2 and name == norm(b.get("name","")):
            reasons.append(f"ชื่อซ้ำกับบัญชีดำ ({b['name']})")
        elif addr and len(addr)>5 and addr == norm(b.get("addr","")):
            reasons.append(f"ที่อยู่ซ้ำกับบัญชีดำ")
    if not reasons:
        return True, [], ""
    types = set()
    for r in reasons:
        if "ชิ้น" in r: types.add("qty")
        elif "User" in r: types.add("user")
        elif "ชื่อ" in r: types.add("name")
        elif "ที่อยู่" in r: types.add("addr")
    vtype = "multi" if len(types)>1 else (types.pop() if types else "other")
    return False, reasons, vtype


# ─── SHOPEE FETCH ──────────────────────────────────────────────────────────────

def fetch_new_orders(minutes_back: int = 20) -> list:
    t_to   = int(time.time())
    t_from = t_to - minutes_back * 60
    all_sns = []
    cursor  = ""
    while True:
        params = {"time_range_field":"create_time","time_from":t_from,"time_to":t_to,
                  "page_size":50,"order_status":"READY_TO_SHIP"}
        if cursor:
            params["cursor"] = cursor
        data  = shopee_api("/api/v2/order/get_order_list", params=params)
        items = data.get("response",{}).get("order_list",[])
        all_sns.extend([o["order_sn"] for o in items if not already_checked(o["order_sn"])])
        if not data.get("response",{}).get("more",False):
            break
        cursor = data.get("response",{}).get("next_cursor","")
    if not all_sns:
        return []
    result = []
    for i in range(0, len(all_sns), 50):
        batch = all_sns[i:i+50]
        data  = shopee_api("/api/v2/order/get_order_detail",
                           params={"order_sn_list":",".join(batch),
                                   "response_optional_fields":"buyer_user_id,recipient_address,item_list"})
        for o in data.get("response",{}).get("order_list",[]):
            addr_obj = o.get("recipient_address",{})
            full_addr = " ".join(filter(None,[
                addr_obj.get("full_address",""),addr_obj.get("district",""),
                addr_obj.get("city",""),addr_obj.get("state",""),addr_obj.get("zipcode","")
            ])).strip()
            qty = sum(x.get("model_quantity_purchased",1) for x in o.get("item_list",[]))
            result.append({
                "order_sn":    o.get("order_sn",""),
                "user_id":     str(o.get("buyer_user_id","")),
                "buyer_name":  addr_obj.get("name",""),
                "phone":       addr_obj.get("phone",""),
                "address":     full_addr,
                "qty":         qty,
            })
    return result


# ─── AUTO-CHECKER THREAD ───────────────────────────────────────────────────────

def run_cycle():
    log_checker("เริ่มรอบตรวจออเดอร์...")
    tokens = load_tokens()
    if not tokens.get("access_token"):
        log_checker("ไม่มี access token — กรุณา authorize ก่อน")
        return {"passed":[],"failed":[]}
    try:
        orders = fetch_new_orders(minutes_back=20)
    except Exception as e:
        log_checker(f"ดึงออเดอร์ไม่ได้: {e}")
        return {"passed":[],"failed":[]}
    if not orders:
        log_checker("ไม่มีออเดอร์ใหม่")
        return {"passed":[],"failed":[]}

    window = bl_window()
    passed, failed = [], []
    for order in orders:
        ok, reasons, vtype = check_one(order, window)
        if ok:
            save_checked(order,"pass")
            # deduct stock
            con = get_db()
            row = con.execute("SELECT id,stock FROM products WHERE stock>0 LIMIT 1").fetchone()
            if row:
                con.execute("UPDATE products SET stock=MAX(0,stock-?) WHERE id=?",
                            (order.get("qty",1), row[0]))
                con.commit()
            con.close()
            passed.append(order)
        else:
            save_checked(order,"fail",reasons)
            save_bl(order,vtype,reasons)
            window.append({"user_id":order.get("user_id",""),
                           "name":order.get("buyer_name",""),
                           "addr":order.get("address","")})
            failed.append({"order":order,"reasons":reasons,"violation":vtype})
    log_checker(f"ตรวจ {len(orders)} ออเดอร์ — ผ่าน {len(passed)} | ผิดเงื่อนไข {len(failed)}")
    return {"passed":passed,"failed":failed}

def checker_loop():
    while checker_state["running"]:
        checker_state["last_run"] = datetime.now().isoformat()
        checker_state["next_run"] = (datetime.now() + timedelta(seconds=checker_state["interval"])).isoformat()
        run_cycle()
        elapsed = 0
        while elapsed < checker_state["interval"] and checker_state["running"]:
            time.sleep(1)
            elapsed += 1

# ─── AUTH ──────────────────────────────────────────────────────────────────────

def do_authorize():
    pid, _, base_url = get_keys()
    cfg     = load_config()
    shop_id = cfg.get("shop_id", 0)
    code_holder = {}
    event = threading.Event()

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code = qs.get("code",[None])[0]
            if code:
                code_holder["code"] = code
                self.send_response(200); self.end_headers()
                self.wfile.write("<html><body style='font-family:sans-serif;padding:2rem'><h2>✅ Authorization สำเร็จ!</h2><p>ปิดหน้าต่างนี้แล้วกลับไปที่ระบบได้เลย</p></body></html>".encode())
                event.set()
            else:
                self.send_response(400); self.end_headers()
        def log_message(self,*a): pass

    srv = http.server.HTTPServer(("",REDIRECT_PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    ts  = int(time.time())
    key = PARTNER_KEY_LIVE if cfg.get("env")!="test" else PARTNER_KEY_TEST
    sig = hmac.new(key.encode(), f"{pid}/api/v2/shop/auth_partner{ts}".encode(), hashlib.sha256).hexdigest()
    redirect = f"http://localhost:{REDIRECT_PORT}/cb"
    url = (f"{base_url}/api/v2/shop/auth_partner"
           f"?partner_id={pid}&timestamp={ts}&sign={sig}"
           f"&redirect={urllib.parse.quote(redirect)}")
    webbrowser.open(url)
    event.wait(timeout=120)
    srv.shutdown()

    code = code_holder.get("code")
    if not code:
        return False, "หมดเวลา ไม่ได้รับ authorization code"

    ts2  = int(time.time())
    sig2 = hmac.new(key.encode(), f"{pid}/api/v2/auth/token/get{ts2}".encode(), hashlib.sha256).hexdigest()
    r = requests.post(f"{base_url}/api/v2/auth/token/get",
                      params={"partner_id":pid,"timestamp":ts2,"sign":sig2},
                      json={"code":code,"shop_id":shop_id,"partner_id":pid}, timeout=15)
    d = r.json()
    if d.get("access_token"):
        save_tokens({"access_token":d["access_token"],"refresh_token":d.get("refresh_token",""),
                     "expire_in":d.get("expire_in",14400),"updated_at":int(time.time())})
        return True, "Authorization สำเร็จ!"
    return False, f"แลก token ล้มเหลว: {d}"


# ─── API ROUTES ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static","index.html")

# Config
@app.route("/api/config", methods=["GET","POST"])
def config():
    if request.method == "POST":
        cfg = {**load_config(), **request.json}
        save_config(cfg)
        return jsonify({"ok":True})
    return jsonify(load_config())

# Auth
@app.route("/api/auth/status")
def auth_status():
    t = load_tokens()
    has_token = bool(t.get("access_token"))
    expired   = False
    if has_token:
        updated = t.get("updated_at",0)
        expire  = t.get("expire_in",14400)
        expired = (time.time()-updated) > (expire-300)
    return jsonify({"has_token":has_token,"expired":expired,"updated_at":t.get("updated_at")})

@app.route("/api/auth/authorize", methods=["POST"])
def authorize():
    def _do():
        ok, msg = do_authorize()
        checker_state["auth_result"] = {"ok":ok,"msg":msg}
    threading.Thread(target=_do,daemon=True).start()
    return jsonify({"ok":True,"msg":"กำลังเปิด browser เพื่อ authorize..."})

@app.route("/api/auth/result")
def auth_result():
    r = checker_state.pop("auth_result", None)
    return jsonify(r or {"pending":True})

# Stats
@app.route("/api/stats")
def stats():
    today = today_iso()
    cutoff30 = days_ago(WINDOW_DAYS)
    con = get_db()
    total_stock = con.execute("SELECT COALESCE(SUM(stock),0) FROM products").fetchone()[0]
    orders_today = con.execute("SELECT COUNT(*) FROM checked_orders WHERE date(checked_at)=?",(today,)).fetchone()[0]
    pass_today   = con.execute("SELECT COUNT(*) FROM checked_orders WHERE date(checked_at)=? AND status='pass'",(today,)).fetchone()[0]
    fail_today   = con.execute("SELECT COUNT(*) FROM checked_orders WHERE date(checked_at)=? AND status='fail'",(today,)).fetchone()[0]
    bl_active    = con.execute("SELECT COUNT(*) FROM blacklist WHERE created_at>=?",(cutoff30,)).fetchone()[0]
    pending_send = con.execute("SELECT COUNT(*) FROM blacklist WHERE sent_empty=0 AND created_at>=?",(cutoff30,)).fetchone()[0]
    con.close()
    return jsonify({"stock":total_stock,"orders_today":orders_today,"pass_today":pass_today,
                    "fail_today":fail_today,"bl_active":bl_active,"pending_send":pending_send})

# Auto-checker control
@app.route("/api/checker/status")
def checker_status():
    return jsonify({
        "running":  checker_state["running"],
        "last_run": checker_state["last_run"],
        "next_run": checker_state["next_run"],
        "interval": checker_state["interval"],
        "log":      checker_state["log"][:50],
    })

@app.route("/api/checker/start", methods=["POST"])
def checker_start():
    global checker_thread
    if checker_state["running"]:
        return jsonify({"ok":False,"msg":"ระบบกำลังทำงานอยู่แล้ว"})
    cfg = load_config()
    if not cfg.get("shop_id"):
        return jsonify({"ok":False,"msg":"กรุณาตั้งค่า Shop ID ก่อน"})
    checker_state["running"]  = True
    checker_state["interval"] = int(request.json.get("interval", cfg.get("interval",300)))
    checker_thread = threading.Thread(target=checker_loop, daemon=True)
    checker_thread.start()
    log_checker(f"เริ่มระบบตรวจอัตโนมัติทุก {checker_state['interval']//60} นาที")
    return jsonify({"ok":True})

@app.route("/api/checker/stop", methods=["POST"])
def checker_stop():
    checker_state["running"] = False
    log_checker("หยุดระบบตรวจอัตโนมัติ")
    return jsonify({"ok":True})

@app.route("/api/checker/run-now", methods=["POST"])
def checker_run_now():
    def _run():
        result = run_cycle()
        checker_state["last_manual"] = {"passed":len(result["passed"]),"failed":len(result["failed"]),"at":datetime.now().isoformat()}
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok":True,"msg":"กำลังตรวจออเดอร์..."})

# Blacklist
@app.route("/api/blacklist")
def get_blacklist():
    days = int(request.args.get("days",30))
    cutoff = days_ago(days)
    q  = request.args.get("q","")
    vt = request.args.get("type","")
    st = request.args.get("sent","")
    sql = "SELECT id,order_sn,user_id,buyer_name,phone,address,violation,reasons,qty,sent_empty,created_at FROM blacklist WHERE created_at>=?"
    params = [cutoff]
    if q:
        sql += " AND (order_sn LIKE ? OR buyer_name LIKE ? OR user_id LIKE ?)"
        params += [f"%{q}%",f"%{q}%",f"%{q}%"]
    if vt:
        sql += " AND violation=?"; params.append(vt)
    if st == "pending": sql += " AND sent_empty=0"
    elif st == "sent":  sql += " AND sent_empty=1"
    sql += " ORDER BY created_at DESC"
    con = get_db()
    rows = con.execute(sql,params).fetchall()
    con.close()
    return jsonify([{
        "id":r[0],"order_sn":r[1],"user_id":r[2],"buyer_name":r[3],
        "phone":r[4],"address":r[5],"violation":r[6],
        "reasons":json.loads(r[7] or "[]"),"qty":r[8],
        "sent_empty":bool(r[9]),"created_at":r[10],
        "age_days":age_days(r[10])
    } for r in rows])

@app.route("/api/blacklist/<int:bid>/sent", methods=["POST"])
def mark_sent(bid):
    con = get_db()
    con.execute("UPDATE blacklist SET sent_empty=1 WHERE id=?",(bid,))
    con.commit(); con.close()
    return jsonify({"ok":True})

@app.route("/api/blacklist/<int:bid>", methods=["DELETE"])
def del_bl(bid):
    con = get_db()
    con.execute("DELETE FROM blacklist WHERE id=?",(bid,))
    con.commit(); con.close()
    return jsonify({"ok":True})

@app.route("/api/blacklist/add", methods=["POST"])
def add_bl_manual():
    d = request.json
    order = {"order_sn":d.get("order_sn","manual_"+str(int(time.time()))),
             "user_id":d.get("user_id",""),"buyer_name":d.get("buyer_name",""),
             "phone":d.get("phone",""),"address":d.get("address",""),"qty":d.get("qty",1)}
    save_bl(order, d.get("violation","user"), [d.get("reason","บันทึกด้วยตนเอง")])
    return jsonify({"ok":True})

# Orders history
@app.route("/api/orders")
def get_orders():
    days = int(request.args.get("days",7))
    cutoff = days_ago(days)
    q  = request.args.get("q","")
    st = request.args.get("status","")
    sql = "SELECT id,order_sn,user_id,buyer_name,phone,address,qty,status,reasons,checked_at FROM checked_orders WHERE checked_at>=?"
    params = [cutoff]
    if q:
        sql += " AND (order_sn LIKE ? OR buyer_name LIKE ? OR user_id LIKE ?)"
        params += [f"%{q}%",f"%{q}%",f"%{q}%"]
    if st:
        sql += " AND status=?"; params.append(st)
    sql += " ORDER BY checked_at DESC LIMIT 500"
    con = get_db()
    rows = con.execute(sql,params).fetchall()
    con.close()
    return jsonify([{
        "id":r[0],"order_sn":r[1],"user_id":r[2],"buyer_name":r[3],
        "phone":r[4],"address":r[5],"qty":r[6],"status":r[7],
        "reasons":json.loads(r[8] or "[]"),"checked_at":r[9],
        "age_days":age_days(r[9])
    } for r in rows])

# Products/Stock
@app.route("/api/products")
def get_products():
    con = get_db()
    rows = con.execute("SELECT id,item_id,name,stock,min_stock,price,updated_at FROM products ORDER BY name").fetchall()
    con.close()
    return jsonify([{"id":r[0],"item_id":r[1],"name":r[2],"stock":r[3],"min_stock":r[4],"price":r[5],"updated_at":r[6]} for r in rows])

@app.route("/api/products", methods=["POST"])
def add_product():
    d = request.json
    con = get_db()
    con.execute("INSERT OR REPLACE INTO products (item_id,name,stock,min_stock,price,updated_at) VALUES(?,?,?,?,?,?)",
                (d["item_id"],d["name"],d.get("stock",0),d.get("min_stock",10),d.get("price",0),today_iso()))
    con.commit(); con.close()
    return jsonify({"ok":True})

@app.route("/api/products/<int:pid>", methods=["PUT"])
def update_product(pid):
    d = request.json
    fields = []
    vals   = []
    for k in ["name","stock","min_stock","price"]:
        if k in d:
            fields.append(f"{k}=?")
            vals.append(d[k])
    vals += [today_iso(), pid]
    con = get_db()
    con.execute(f"UPDATE products SET {','.join(fields)},updated_at=? WHERE id=?", vals)
    con.commit(); con.close()
    return jsonify({"ok":True})

@app.route("/api/products/<int:pid>", methods=["DELETE"])
def del_product(pid):
    con = get_db()
    con.execute("DELETE FROM products WHERE id=?",(pid,))
    con.commit(); con.close()
    return jsonify({"ok":True})

# Summary text
@app.route("/api/summary")
def get_summary():
    today = today_iso()
    con = get_db()
    passed = con.execute("SELECT order_sn,buyer_name,user_id,phone,address,qty FROM checked_orders WHERE date(checked_at)=? AND status='pass'",(today,)).fetchall()
    failed = con.execute("SELECT order_sn,buyer_name,user_id,phone,address,reasons FROM checked_orders WHERE date(checked_at)=? AND status='fail'",(today,)).fetchall()
    stock  = con.execute("SELECT COALESCE(SUM(stock),0) FROM products").fetchone()[0]
    bl_count = con.execute("SELECT COUNT(*) FROM blacklist WHERE created_at>=?",(days_ago(30),)).fetchone()[0]
    con.close()

    from datetime import date
    thai_months = ["","ม.ค.","ก.พ.","มี.ค.","เม.ย.","พ.ค.","มิ.ย.","ก.ค.","ส.ค.","ก.ย.","ต.ค.","พ.ย.","ธ.ค."]
    d = date.today()
    thai_date = f"{d.day} {thai_months[d.month]} {d.year+543}"

    lines = [
        f"===== ใบสรุปออเดอร์ซิมเปล่า =====",
        f"วันที่: {thai_date}",
        f"สต็อกคงเหลือ: {stock} ชิ้น | บัญชีดำ active (30 วัน): {bl_count} รายการ",
        f"ตรวจทั้งหมด: {len(passed)+len(failed)} | ผ่าน: {len(passed)} | ผิดเงื่อนไข: {len(failed)}",
        "",
    ]
    if passed:
        lines.append(f"----- จัดส่งซิม ({len(passed)} ออเดอร์) -----")
        for i,r in enumerate(passed,1):
            lines += [f"{i}. ออเดอร์: {r[0]}",f"   ชื่อ: {r[1]} | User: {r[2]}",
                      f"   เบอร์: {r[3]}",f"   ที่อยู่: {r[4]}",f"   จำนวน: {r[5]} ชิ้น",""]
    if failed:
        lines.append(f"----- ส่งซองเปล่า ({len(failed)} ออเดอร์) -----")
        for i,r in enumerate(failed,1):
            reasons = " / ".join(json.loads(r[5] or "[]"))
            lines += [f"{i}. ออเดอร์: {r[0]}",f"   ชื่อ: {r[1]} | User: {r[2]}",
                      f"   เบอร์: {r[3]}",f"   ที่อยู่: {r[4]}",f"   สาเหตุ: {reasons}",""]
    lines.append("==============================")
    return jsonify({"text":"\n".join(lines),"passed":len(passed),"failed":len(failed)})


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    print("="*55)
    print(f"  Shopee Sim Manager — Web App")
    print(f"  เปิดเบราว์เซอร์: http://localhost:{port}")
    print("="*55)
    app.run(host="0.0.0.0", port=port, debug=False)
