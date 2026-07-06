"""
Shopee Sim Manager — Web App
Backend: Flask + PostgreSQL (Supabase)
"""

import hashlib, hmac, time, json, os, threading, webbrowser, urllib.parse
import http.server, requests
from datetime import datetime, timedelta
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__, static_folder="static")
CORS(app)

# ─── CONFIG ───────────────────────────────────────────────────────────────────
PARTNER_ID_LIVE  = 2037264
PARTNER_KEY_LIVE = "shpk7647596d72617a70676e77634b7a7148736b467349537575774d57657243"
PARTNER_ID_TEST  = 1236318
PARTNER_KEY_TEST = "shpk4b41694b50746f4c584e6f79716e776350754a4251594364734d6676684c"
REDIRECT_PORT    = 8899
WINDOW_DAYS      = 30
DATABASE_URL     = os.environ.get("DATABASE_URL", "")  # Supabase connection string

checker_state = {"running": False, "last_run": None, "next_run": None, "interval": 300, "log": []}
checker_thread = None

# ─── DATABASE (PostgreSQL) ────────────────────────────────────────────────────
def get_db():
    url = DATABASE_URL
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(url, cursor_factory=RealDictCursor)

def init_db():
    if not DATABASE_URL:
        log_checker("⚠️ ไม่มี DATABASE_URL — กรุณาตั้งค่า Environment Variable")
        return
    con = get_db()
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS blacklist (
            id         SERIAL PRIMARY KEY,
            order_sn   TEXT UNIQUE,
            user_id    TEXT,
            buyer_name TEXT,
            phone      TEXT,
            address    TEXT,
            violation  TEXT,
            reasons    TEXT,
            qty        INTEGER DEFAULT 1,
            sent_empty BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS checked_orders (
            id         SERIAL PRIMARY KEY,
            order_sn   TEXT UNIQUE,
            user_id    TEXT,
            buyer_name TEXT,
            phone      TEXT,
            address    TEXT,
            qty        INTEGER DEFAULT 1,
            status     TEXT,
            reasons    TEXT,
            checked_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS products (
            id         SERIAL PRIMARY KEY,
            item_id    TEXT UNIQUE,
            name       TEXT,
            stock      INTEGER DEFAULT 0,
            min_stock  INTEGER DEFAULT 10,
            price      NUMERIC DEFAULT 0,
            updated_at DATE DEFAULT CURRENT_DATE
        );
        CREATE TABLE IF NOT EXISTS app_config (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS app_tokens (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    # seed products if empty
    cur.execute("SELECT COUNT(*) as c FROM products")
    if cur.fetchone()["c"] == 0:
        cur.executemany(
            "INSERT INTO products (item_id,name,stock,min_stock,price) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            [("7891234560","ซิมเปล่า AIS 4G/5G",50,10,49),
             ("7891234561","ซิมเปล่า DTAC 4G",12,10,39),
             ("7891234562","ซิมเปล่า TRUE Move H",0,5,39)]
        )
    con.commit(); con.close()

def load_config():
    if not DATABASE_URL: return {"env":"live","shop_id":0,"interval":300}
    try:
        con = get_db(); cur = con.cursor()
        cur.execute("SELECT key,value FROM app_config")
        rows = {r["key"]: r["value"] for r in cur.fetchall()}
        con.close()
        return {"env": rows.get("env","live"), "shop_id": int(rows.get("shop_id",0) or 0), "interval": int(rows.get("interval",300) or 300)}
    except: return {"env":"live","shop_id":0,"interval":300}

def save_config(cfg):
    if not DATABASE_URL: return
    con = get_db(); cur = con.cursor()
    for k,v in cfg.items():
        cur.execute("INSERT INTO app_config (key,value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (k, str(v)))
    con.commit(); con.close()

def load_tokens():
    if not DATABASE_URL: return {}
    try:
        con = get_db(); cur = con.cursor()
        cur.execute("SELECT value FROM app_tokens WHERE key='main'")
        row = cur.fetchone(); con.close()
        return json.loads(row["value"]) if row else {}
    except: return {}

def save_tokens(data):
    if not DATABASE_URL: return
    con = get_db(); cur = con.cursor()
    cur.execute("INSERT INTO app_tokens (key,value) VALUES ('main',%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (json.dumps(data),))
    con.commit(); con.close()

# ─── HELPERS ──────────────────────────────────────────────────────────────────
def norm(s): return " ".join((s or "").strip().lower().split())
def today_iso(): return datetime.now().date().isoformat()
def days_ago(n): return (datetime.now() - timedelta(days=n)).isoformat()
def age_days(dt):
    if not dt: return 0
    if isinstance(dt, str): dt = datetime.fromisoformat(dt.replace("Z",""))
    return (datetime.now() - dt.replace(tzinfo=None)).days if hasattr(dt,"date") else 0

def get_keys():
    cfg = load_config()
    if cfg.get("env") == "test":
        return PARTNER_ID_TEST, PARTNER_KEY_TEST, "https://partner.test-stable.shopeemobile.com"
    return PARTNER_ID_LIVE, PARTNER_KEY_LIVE, "https://partner.shopeemobile.com"

def sign_request(path, ts, access_token="", shop_id=0):
    pid, key, _ = get_keys()
    base = f"{pid}{path}{ts}"
    if access_token: base += f"{access_token}{shop_id}"
    return hmac.new(key.encode(), base.encode(), hashlib.sha256).hexdigest()

def shopee_api(path, params=None, body=None, method="GET"):
    pid, _, base_url = get_keys()
    cfg = load_config(); shop_id = cfg.get("shop_id", 0)
    tokens = load_tokens(); ts = int(time.time())
    sig = sign_request(path, ts, tokens.get("access_token",""), shop_id)
    p = {"partner_id":pid,"timestamp":ts,"access_token":tokens.get("access_token",""),"shop_id":shop_id,"sign":sig}
    if params: p.update(params)
    url = base_url + path
    r = requests.get(url, params=p, timeout=15) if method=="GET" else requests.post(url, params=p, json=body, timeout=15)
    r.raise_for_status(); return r.json()

def log_checker(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    entry = f"[{ts}] {msg}"
    checker_state["log"].insert(0, entry)
    checker_state["log"] = checker_state["log"][:200]
    print(entry)

# ─── BLACKLIST / ORDER HELPERS ────────────────────────────────────────────────
def bl_window():
    if not DATABASE_URL: return []
    try:
        con = get_db(); cur = con.cursor()
        cur.execute("SELECT user_id,buyer_name,address,order_sn,created_at FROM blacklist WHERE created_at >= NOW() - INTERVAL '%s days'", (WINDOW_DAYS,))
        rows = cur.fetchall(); con.close()
        return [dict(r) for r in rows]
    except: return []

def already_checked(sn):
    if not DATABASE_URL: return False
    try:
        con = get_db(); cur = con.cursor()
        cur.execute("SELECT 1 FROM checked_orders WHERE order_sn=%s", (sn,))
        r = cur.fetchone(); con.close(); return bool(r)
    except: return False

def save_bl(order, violation, reasons):
    if not DATABASE_URL: return
    con = get_db(); cur = con.cursor()
    cur.execute("""INSERT INTO blacklist (order_sn,user_id,buyer_name,phone,address,violation,reasons,qty)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (order_sn) DO NOTHING""",
        (order["order_sn"],order.get("user_id",""),order.get("buyer_name",""),
         order.get("phone",""),order.get("address",""),violation,
         json.dumps(reasons,ensure_ascii=False),order.get("qty",1)))
    con.commit(); con.close()

def save_checked(order, status, reasons=None):
    if not DATABASE_URL: return
    con = get_db(); cur = con.cursor()
    cur.execute("""INSERT INTO checked_orders (order_sn,user_id,buyer_name,phone,address,qty,status,reasons)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (order_sn) DO NOTHING""",
        (order["order_sn"],order.get("user_id",""),order.get("buyer_name",""),
         order.get("phone",""),order.get("address",""),order.get("qty",1),
         status,json.dumps(reasons or [],ensure_ascii=False)))
    con.commit(); con.close()

# ─── CHECK LOGIC ──────────────────────────────────────────────────────────────
def check_one(order, window):
    reasons = []
    if order.get("qty",1) > 1:
        reasons.append(f"สั่ง {order['qty']} ชิ้น เกินกำหนด (1 ชิ้น/คน)")
    uid  = norm(order.get("user_id",""))
    name = norm(order.get("buyer_name",""))
    addr = norm(order.get("address",""))
    for b in window:
        if uid and uid == norm(b.get("user_id","")):
            reasons.append(f"User ID ซ้ำกับออเดอร์ {b['order_sn']}")
        elif name and len(name)>2 and name == norm(b.get("buyer_name","")):
            reasons.append(f"ชื่อซ้ำกับออเดอร์ {b['order_sn']}")
        elif addr and len(addr)>5 and addr == norm(b.get("address","")):
            reasons.append(f"ที่อยู่ซ้ำกับออเดอร์ {b['order_sn']}")
    if not reasons: return True, [], ""
    types = set()
    for r in reasons:
        if "ชิ้น" in r: types.add("qty")
        elif "User" in r: types.add("user")
        elif "ชื่อ" in r: types.add("name")
        elif "ที่อยู่" in r: types.add("addr")
    vtype = "multi" if len(types)>1 else (types.pop() if types else "other")
    return False, reasons, vtype

# ─── SHOPEE FETCH ─────────────────────────────────────────────────────────────
def fetch_new_orders(minutes_back=20):
    t_to = int(time.time()); t_from = t_to - minutes_back*60
    all_sns = []; cursor = ""
    while True:
        params = {"time_range_field":"create_time","time_from":t_from,"time_to":t_to,"page_size":50,"order_status":"READY_TO_SHIP"}
        if cursor: params["cursor"] = cursor
        data = shopee_api("/api/v2/order/get_order_list", params=params)
        items = data.get("response",{}).get("order_list",[])
        all_sns.extend([o["order_sn"] for o in items if not already_checked(o["order_sn"])])
        if not data.get("response",{}).get("more",False): break
        cursor = data.get("response",{}).get("next_cursor","")
    if not all_sns: return []
    result = []
    for i in range(0,len(all_sns),50):
        batch = all_sns[i:i+50]
        data = shopee_api("/api/v2/order/get_order_detail",
            params={"order_sn_list":",".join(batch),"response_optional_fields":"buyer_user_id,recipient_address,item_list"})
        for o in data.get("response",{}).get("order_list",[]):
            addr_obj = o.get("recipient_address",{})
            full_addr = " ".join(filter(None,[addr_obj.get("full_address",""),addr_obj.get("district",""),addr_obj.get("city",""),addr_obj.get("state",""),addr_obj.get("zipcode","")])).strip()
            qty = sum(x.get("model_quantity_purchased",1) for x in o.get("item_list",[]))
            result.append({"order_sn":o.get("order_sn",""),"user_id":str(o.get("buyer_user_id","")),"buyer_name":addr_obj.get("name",""),"phone":addr_obj.get("phone",""),"address":full_addr,"qty":qty})
    return result

# ─── AUTO-CHECKER ─────────────────────────────────────────────────────────────
def run_cycle():
    log_checker("เริ่มรอบตรวจออเดอร์...")
    if not DATABASE_URL: log_checker("⚠️ ไม่มี DATABASE_URL"); return {"passed":[],"failed":[]}
    tokens = load_tokens()
    if not tokens.get("access_token"): log_checker("ไม่มี access token"); return {"passed":[],"failed":[]}
    try: orders = fetch_new_orders(minutes_back=20)
    except Exception as e: log_checker(f"ดึงออเดอร์ไม่ได้: {e}"); return {"passed":[],"failed":[]}
    if not orders: log_checker("ไม่มีออเดอร์ใหม่"); return {"passed":[],"failed":[]}
    window = bl_window(); passed, failed = [], []
    for order in orders:
        ok, reasons, vtype = check_one(order, window)
        if ok:
            save_checked(order,"pass")
            try:
                con=get_db(); cur=con.cursor()
                cur.execute("UPDATE products SET stock=GREATEST(0,stock-%s) WHERE id=(SELECT id FROM products WHERE stock>0 LIMIT 1)", (order.get("qty",1),))
                con.commit(); con.close()
            except: pass
            passed.append(order)
        else:
            save_checked(order,"fail",reasons); save_bl(order,vtype,reasons)
            window.append({"user_id":order.get("user_id",""),"buyer_name":order.get("buyer_name",""),"address":order.get("address",""),"order_sn":order.get("order_sn",""),"created_at":datetime.now()})
            failed.append({"order":order,"reasons":reasons,"violation":vtype})
    log_checker(f"ตรวจ {len(orders)} ออเดอร์ — ✓ ผ่าน {len(passed)} | ✗ ผิดเงื่อนไข {len(failed)}")
    return {"passed":passed,"failed":failed}

def checker_loop():
    while checker_state["running"]:
        checker_state["last_run"] = datetime.now().isoformat()
        checker_state["next_run"] = (datetime.now()+timedelta(seconds=checker_state["interval"])).isoformat()
        run_cycle()
        elapsed = 0
        while elapsed < checker_state["interval"] and checker_state["running"]:
            time.sleep(1); elapsed += 1

# ─── AUTH ─────────────────────────────────────────────────────────────────────
def do_authorize():
    pid, _, base_url = get_keys()
    cfg = load_config(); shop_id = cfg.get("shop_id",0)
    code_holder = {}; event = threading.Event()
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code = qs.get("code",[None])[0]
            if code:
                code_holder["code"] = code
                self.send_response(200); self.end_headers()
                self.wfile.write("<html><body><h2>Authorization success!</h2><p>Close this window.</p></body></html>".encode())
                event.set()
            else: self.send_response(400); self.end_headers()
        def log_message(self,*a): pass
    srv = http.server.HTTPServer(("",REDIRECT_PORT),H)
    threading.Thread(target=srv.serve_forever,daemon=True).start()
    ts = int(time.time()); key = PARTNER_KEY_LIVE if load_config().get("env")!="test" else PARTNER_KEY_TEST
    sig = hmac.new(key.encode(),f"{pid}/api/v2/shop/auth_partner{ts}".encode(),hashlib.sha256).hexdigest()
    redirect = f"http://localhost:{REDIRECT_PORT}/cb"
    url = f"{base_url}/api/v2/shop/auth_partner?partner_id={pid}&timestamp={ts}&sign={sig}&redirect={urllib.parse.quote(redirect)}"
    webbrowser.open(url); event.wait(timeout=120); srv.shutdown()
    code = code_holder.get("code")
    if not code: return False, "หมดเวลา"
    ts2 = int(time.time())
    sig2 = hmac.new(key.encode(),f"{pid}/api/v2/auth/token/get{ts2}".encode(),hashlib.sha256).hexdigest()
    r = requests.post(f"{base_url}/api/v2/auth/token/get",params={"partner_id":pid,"timestamp":ts2,"sign":sig2},json={"code":code,"shop_id":shop_id,"partner_id":pid},timeout=15)
    d = r.json()
    if d.get("access_token"):
        save_tokens({"access_token":d["access_token"],"refresh_token":d.get("refresh_token",""),"expire_in":d.get("expire_in",14400),"updated_at":int(time.time())})
        return True, "Authorization สำเร็จ!"
    return False, f"ล้มเหลว: {d}"

# ─── API ROUTES ───────────────────────────────────────────────────────────────
@app.route("/")
def index(): return send_from_directory("static","index.html")

@app.route("/api/config", methods=["GET","POST"])
def config_api():
    if request.method=="POST": save_config(request.json); return jsonify({"ok":True})
    return jsonify(load_config())

@app.route("/api/auth/status")
def auth_status():
    t = load_tokens(); has = bool(t.get("access_token"))
    expired = has and (time.time()-t.get("updated_at",0))>(t.get("expire_in",14400)-300)
    return jsonify({"has_token":has,"expired":expired,"updated_at":t.get("updated_at")})

@app.route("/api/auth/token", methods=["POST"])
def save_token_api():
    d = request.json
    if not d.get("access_token"): return jsonify({"ok":False,"msg":"ไม่มี access_token"})
    save_tokens({"access_token":d["access_token"],"refresh_token":d.get("refresh_token",""),"expire_in":14400,"updated_at":int(time.time())})
    return jsonify({"ok":True})

@app.route("/api/auth/authorize", methods=["POST"])
def authorize():
    threading.Thread(target=lambda: checker_state.update({"auth_result":dict(zip(["ok","msg"],do_authorize()))}),daemon=True).start()
    return jsonify({"ok":True,"msg":"กำลังเปิด browser..."})

@app.route("/api/auth/result")
def auth_result():
    r = checker_state.pop("auth_result",None); return jsonify(r or {"pending":True})

@app.route("/api/stats")
def stats():
    if not DATABASE_URL: return jsonify({"stock":0,"orders_today":0,"pass_today":0,"fail_today":0,"bl_active":0,"pending_send":0})
    con=get_db(); cur=con.cursor()
    cur.execute("SELECT COALESCE(SUM(stock),0) as s FROM products"); stock=cur.fetchone()["s"]
    cur.execute("SELECT COUNT(*) as c FROM checked_orders WHERE checked_at::date=CURRENT_DATE"); ot=cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) as c FROM checked_orders WHERE checked_at::date=CURRENT_DATE AND status='pass'"); pt=cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) as c FROM checked_orders WHERE checked_at::date=CURRENT_DATE AND status='fail'"); ft=cur.fetchone()["c"]
    cur.execute(f"SELECT COUNT(*) as c FROM blacklist WHERE created_at >= NOW()-INTERVAL '{WINDOW_DAYS} days'"); bla=cur.fetchone()["c"]
    cur.execute(f"SELECT COUNT(*) as c FROM blacklist WHERE sent_empty=FALSE AND created_at >= NOW()-INTERVAL '{WINDOW_DAYS} days'"); ps=cur.fetchone()["c"]
    con.close()
    return jsonify({"stock":int(stock),"orders_today":ot,"pass_today":pt,"fail_today":ft,"bl_active":bla,"pending_send":ps})

@app.route("/api/checker/status")
def checker_status():
    return jsonify({"running":checker_state["running"],"last_run":checker_state["last_run"],"next_run":checker_state["next_run"],"interval":checker_state["interval"],"log":checker_state["log"][:50]})

@app.route("/api/checker/start", methods=["POST"])
def checker_start():
    global checker_thread
    if checker_state["running"]: return jsonify({"ok":False,"msg":"กำลังทำงานอยู่แล้ว"})
    if not DATABASE_URL: return jsonify({"ok":False,"msg":"ยังไม่ได้ตั้งค่า DATABASE_URL"})
    checker_state["running"]=True; checker_state["interval"]=int(request.json.get("interval",300))
    checker_thread=threading.Thread(target=checker_loop,daemon=True); checker_thread.start()
    log_checker(f"เริ่มระบบตรวจอัตโนมัติทุก {checker_state['interval']//60} นาที")
    return jsonify({"ok":True})

@app.route("/api/checker/stop", methods=["POST"])
def checker_stop():
    checker_state["running"]=False; log_checker("หยุดระบบ"); return jsonify({"ok":True})

@app.route("/api/checker/run-now", methods=["POST"])
def checker_run_now():
    threading.Thread(target=run_cycle,daemon=True).start()
    return jsonify({"ok":True,"msg":"กำลังตรวจออเดอร์..."})

@app.route("/api/blacklist")
def get_blacklist():
    if not DATABASE_URL: return jsonify([])
    days=int(request.args.get("days",30)); q=request.args.get("q",""); vt=request.args.get("type",""); st=request.args.get("sent","")
    sql=f"SELECT id,order_sn,user_id,buyer_name,phone,address,violation,reasons,qty,sent_empty,created_at FROM blacklist WHERE created_at >= NOW()-INTERVAL '{days} days'"
    params=[]
    if q: sql+=" AND (order_sn ILIKE %s OR buyer_name ILIKE %s OR user_id ILIKE %s)"; params+=[f"%{q}%"]*3
    if vt: sql+=" AND violation=%s"; params.append(vt)
    if st=="pending": sql+=" AND sent_empty=FALSE"
    elif st=="sent": sql+=" AND sent_empty=TRUE"
    sql+=" ORDER BY created_at DESC"
    con=get_db(); cur=con.cursor(); cur.execute(sql,params)
    rows=cur.fetchall(); con.close()
    result=[]
    for r in rows:
        d=dict(r); d["reasons"]=json.loads(d.get("reasons") or "[]")
        d["sent_empty"]=bool(d["sent_empty"]); d["created_at"]=str(d["created_at"]); d["age_days"]=age_days(r["created_at"])
        result.append(d)
    return jsonify(result)

@app.route("/api/blacklist/<int:bid>/sent", methods=["POST"])
def mark_sent(bid):
    con=get_db(); cur=con.cursor(); cur.execute("UPDATE blacklist SET sent_empty=TRUE WHERE id=%s",(bid,)); con.commit(); con.close(); return jsonify({"ok":True})

@app.route("/api/blacklist/<int:bid>", methods=["DELETE"])
def del_bl(bid):
    con=get_db(); cur=con.cursor(); cur.execute("DELETE FROM blacklist WHERE id=%s",(bid,)); con.commit(); con.close(); return jsonify({"ok":True})

@app.route("/api/blacklist/add", methods=["POST"])
def add_bl_manual():
    d=request.json
    order={"order_sn":d.get("order_sn","manual_"+str(int(time.time()))),"user_id":d.get("user_id",""),"buyer_name":d.get("buyer_name",""),"phone":d.get("phone",""),"address":d.get("address",""),"qty":d.get("qty",1)}
    save_bl(order,d.get("violation","user"),[d.get("reason","บันทึกด้วยตนเอง")]); return jsonify({"ok":True})

@app.route("/api/orders")
def get_orders():
    if not DATABASE_URL: return jsonify([])
    days=int(request.args.get("days",7)); q=request.args.get("q",""); st=request.args.get("status","")
    sql=f"SELECT id,order_sn,user_id,buyer_name,phone,address,qty,status,reasons,checked_at FROM checked_orders WHERE checked_at >= NOW()-INTERVAL '{days} days'"
    params=[]
    if q: sql+=" AND (order_sn ILIKE %s OR buyer_name ILIKE %s OR user_id ILIKE %s)"; params+=[f"%{q}%"]*3
    if st: sql+=" AND status=%s"; params.append(st)
    sql+=" ORDER BY checked_at DESC LIMIT 500"
    con=get_db(); cur=con.cursor(); cur.execute(sql,params)
    rows=cur.fetchall(); con.close()
    result=[]
    for r in rows:
        d=dict(r); d["reasons"]=json.loads(d.get("reasons") or "[]"); d["checked_at"]=str(d["checked_at"]); d["age_days"]=age_days(r["checked_at"])
        result.append(d)
    return jsonify(result)

@app.route("/api/products")
def get_products():
    if not DATABASE_URL: return jsonify([])
    con=get_db(); cur=con.cursor(); cur.execute("SELECT id,item_id,name,stock,min_stock,price,updated_at FROM products ORDER BY name")
    rows=[dict(r) for r in cur.fetchall()]; con.close()
    for r in rows: r["updated_at"]=str(r["updated_at"])
    return jsonify(rows)

@app.route("/api/products", methods=["POST"])
def add_product():
    d=request.json; con=get_db(); cur=con.cursor()
    cur.execute("INSERT INTO products (item_id,name,stock,min_stock,price,updated_at) VALUES (%s,%s,%s,%s,%s,CURRENT_DATE) ON CONFLICT (item_id) DO UPDATE SET name=EXCLUDED.name,stock=EXCLUDED.stock,min_stock=EXCLUDED.min_stock,price=EXCLUDED.price,updated_at=CURRENT_DATE",
        (d["item_id"],d["name"],d.get("stock",0),d.get("min_stock",10),d.get("price",0)))
    con.commit(); con.close(); return jsonify({"ok":True})

@app.route("/api/products/<int:pid>", methods=["PUT"])
def update_product(pid):
    d=request.json; fields=[]; vals=[]
    for k in ["name","stock","min_stock","price"]:
        if k in d: fields.append(f"{k}=%s"); vals.append(d[k])
    vals+=[pid]; con=get_db(); cur=con.cursor()
    cur.execute(f"UPDATE products SET {','.join(fields)},updated_at=CURRENT_DATE WHERE id=%s",vals)
    con.commit(); con.close(); return jsonify({"ok":True})

@app.route("/api/products/<int:pid>", methods=["DELETE"])
def del_product(pid):
    con=get_db(); cur=con.cursor(); cur.execute("DELETE FROM products WHERE id=%s",(pid,)); con.commit(); con.close(); return jsonify({"ok":True})

@app.route("/api/summary")
def get_summary():
    if not DATABASE_URL: return jsonify({"text":"ยังไม่ได้ตั้งค่า DATABASE_URL","passed":0,"failed":0})
    con=get_db(); cur=con.cursor()
    cur.execute("SELECT order_sn,buyer_name,user_id,phone,address,qty FROM checked_orders WHERE checked_at::date=CURRENT_DATE AND status='pass'"); passed=cur.fetchall()
    cur.execute("SELECT order_sn,buyer_name,user_id,phone,address,reasons FROM checked_orders WHERE checked_at::date=CURRENT_DATE AND status='fail'"); failed=cur.fetchall()
    cur.execute("SELECT COALESCE(SUM(stock),0) as s FROM products"); stock=cur.fetchone()["s"]
    cur.execute(f"SELECT COUNT(*) as c FROM blacklist WHERE created_at >= NOW()-INTERVAL '{WINDOW_DAYS} days'"); blc=cur.fetchone()["c"]
    con.close()
    from datetime import date; d=date.today()
    m=["","ม.ค.","ก.พ.","มี.ค.","เม.ย.","พ.ค.","มิ.ย.","ก.ค.","ส.ค.","ก.ย.","ต.ค.","พ.ย.","ธ.ค."]
    thai=f"{d.day} {m[d.month]} {d.year+543}"
    lines=[f"===== ใบสรุปออเดอร์ซิมเปล่า =====",f"วันที่: {thai}",f"สต็อกคงเหลือ: {stock} ชิ้น | บัญชีดำ active: {blc} รายการ",f"ตรวจทั้งหมด: {len(passed)+len(failed)} | ผ่าน: {len(passed)} | ผิดเงื่อนไข: {len(failed)}",""]
    if passed:
        lines.append(f"----- จัดส่งซิม ({len(passed)} ออเดอร์) -----")
        for i,r in enumerate(passed,1):
            lines+=[f"{i}. ออเดอร์: {r['order_sn']}",f"   ชื่อ: {(r['buyer_name'] or '-')} | User: {(r['user_id'] or '-')}",f"   เบอร์: {(r['phone'] or '-')}",f"   ที่อยู่: {(r['address'] or '-')}",f"   จำนวน: {r['qty']} ชิ้น",""]
    if failed:
        lines.append(f"----- ส่งซองเปล่า ({len(failed)} ออเดอร์) -----")
        for i,r in enumerate(failed,1):
            reasons=" / ".join(json.loads(r["reasons"] or "[]"))
            lines+=[f"{i}. ออเดอร์: {r['order_sn']}",f"   ชื่อ: {(r['buyer_name'] or '-')} | User: {(r['user_id'] or '-')}",f"   เบอร์: {(r['phone'] or '-')}",f"   ที่อยู่: {(r['address'] or '-')}",f"   สาเหตุ: {reasons}",""]
    lines.append("==============================")
    return jsonify({"text":"\n".join(lines),"passed":len(passed),"failed":len(failed)})

@app.route("/api/db/status")
def db_status():
    if not DATABASE_URL: return jsonify({"ok":False,"msg":"ไม่มี DATABASE_URL"})
    try:
        con=get_db(); cur=con.cursor(); cur.execute("SELECT 1"); con.close()
        return jsonify({"ok":True,"msg":"เชื่อมต่อฐานข้อมูลสำเร็จ"})
    except Exception as e: return jsonify({"ok":False,"msg":str(e)})

# ─── INIT ─────────────────────────────────────────────────────────────────────
init_db()

# init DB on startup (gunicorn)
try:
    init_db()
except Exception as _e:
    print(f"init_db warning: {_e}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"  เปิดเบราว์เซอร์: http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
