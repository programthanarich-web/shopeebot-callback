"""
Shopee Sim Manager — Firebase Cloud Functions (Python 3.11)
Database: Firestore (ฟรีถาวร)
"""

import hashlib, hmac, time, json, os, threading
import requests
from datetime import datetime, timedelta
from flask import Flask, jsonify, request
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore

# ─── INIT FIREBASE ────────────────────────────────────────────────────────────
if not firebase_admin._apps:
    firebase_admin.initialize_app()

db = firestore.client()

app = Flask(__name__)
CORS(app)

# ─── CONFIG ───────────────────────────────────────────────────────────────────
PARTNER_ID_LIVE  = 2037264
PARTNER_KEY_LIVE = "shpk7647596d72617a70676e77634b7a7148736b467349537575774d57657243"
PARTNER_ID_TEST  = 1236318
PARTNER_KEY_TEST = "shpk4b41694b50746f4c584e6f79716e776350754a4251594364734d6676684c"
WINDOW_DAYS      = 30

checker_state = {"running": False, "last_run": None, "next_run": None, "interval": 300, "log": []}

# ─── FIRESTORE HELPERS ────────────────────────────────────────────────────────
def fs_get(col, doc_id):
    ref = db.collection(col).document(doc_id)
    doc = ref.get()
    return doc.to_dict() if doc.exists else None

def fs_set(col, doc_id, data):
    db.collection(col).document(doc_id).set(data)

def fs_update(col, doc_id, data):
    db.collection(col).document(doc_id).update(data)

def fs_add(col, data):
    return db.collection(col).add(data)

def fs_query(col, filters=None, order_by=None, limit=None):
    ref = db.collection(col)
    if filters:
        for f in filters:
            ref = ref.where(f[0], f[1], f[2])
    if order_by:
        ref = ref.order_by(order_by, direction=firestore.Query.DESCENDING)
    if limit:
        ref = ref.limit(limit)
    return [{"id": d.id, **d.to_dict()} for d in ref.stream()]

def fs_delete(col, doc_id):
    db.collection(col).document(doc_id).delete()

# ─── CONFIG & TOKENS ──────────────────────────────────────────────────────────
def load_config():
    d = fs_get("app_settings", "config")
    return d or {"env": "live", "shop_id": 0, "interval": 300}

def save_config(cfg):
    fs_set("app_settings", "config", cfg)

def load_tokens():
    d = fs_get("app_settings", "tokens")
    return d or {}

def save_tokens(data):
    fs_set("app_settings", "tokens", data)

# ─── UTILS ────────────────────────────────────────────────────────────────────
def norm(s): return " ".join((s or "").strip().lower().split())

def today_iso(): return datetime.now().date().isoformat()

def age_days(dt):
    if not dt: return 0
    if hasattr(dt, "date"):
        return (datetime.now().date() - dt.date()).days
    try:
        d = datetime.fromisoformat(str(dt).replace("Z",""))
        return (datetime.now() - d.replace(tzinfo=None)).days
    except: return 0

def fmt_dt(dt):
    if not dt: return "-"
    if hasattr(dt, "strftime"): return dt.strftime("%d/%m/%y %H:%M")
    return str(dt)[:16]

def get_keys():
    cfg = load_config()
    if cfg.get("env") == "test":
        return PARTNER_ID_TEST, PARTNER_KEY_TEST, "https://partner.test-stable.shopeemobile.com"
    return PARTNER_ID_LIVE, PARTNER_KEY_LIVE, "https://partner.shopeemobile.com"

def sign_req(path, ts, access_token="", shop_id=0):
    pid, key, _ = get_keys()
    base = f"{pid}{path}{ts}"
    if access_token: base += f"{access_token}{shop_id}"
    return hmac.new(key.encode(), base.encode(), hashlib.sha256).hexdigest()

def shopee_api(path, params=None, body=None, method="GET"):
    pid, _, base_url = get_keys()
    cfg = load_config(); shop_id = cfg.get("shop_id", 0)
    tokens = load_tokens(); ts = int(time.time())
    sig = sign_req(path, ts, tokens.get("access_token", ""), shop_id)
    p = {"partner_id": pid, "timestamp": ts, "access_token": tokens.get("access_token",""), "shop_id": shop_id, "sign": sig}
    if params: p.update(params)
    url = base_url + path
    r = requests.get(url, params=p, timeout=15) if method=="GET" else requests.post(url, params=p, json=body, timeout=15)
    r.raise_for_status(); return r.json()

def log_checker(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    checker_state["log"].insert(0, f"[{ts}] {msg}")
    checker_state["log"] = checker_state["log"][:100]

# ─── CHECK LOGIC ──────────────────────────────────────────────────────────────
def bl_window():
    cutoff = datetime.now() - timedelta(days=WINDOW_DAYS)
    return fs_query("blacklist", filters=[("created_at", ">=", cutoff)])

def already_checked(sn):
    docs = db.collection("checked_orders").where("order_sn","==",sn).limit(1).stream()
    return any(True for _ in docs)

def save_bl(order, violation, reasons):
    data = {
        "order_sn":   order["order_sn"],
        "user_id":    order.get("user_id",""),
        "buyer_name": order.get("buyer_name",""),
        "phone":      order.get("phone",""),
        "address":    order.get("address",""),
        "violation":  violation,
        "reasons":    reasons,
        "qty":        order.get("qty",1),
        "sent_empty": False,
        "created_at": firestore.SERVER_TIMESTAMP,
    }
    # upsert by order_sn
    existing = db.collection("blacklist").where("order_sn","==",order["order_sn"]).limit(1).stream()
    for d in existing: return  # already exists
    db.collection("blacklist").add(data)

def save_checked(order, status, reasons=None):
    if already_checked(order["order_sn"]): return
    db.collection("checked_orders").add({
        "order_sn":   order["order_sn"],
        "user_id":    order.get("user_id",""),
        "buyer_name": order.get("buyer_name",""),
        "phone":      order.get("phone",""),
        "address":    order.get("address",""),
        "qty":        order.get("qty",1),
        "status":     status,
        "reasons":    reasons or [],
        "checked_at": firestore.SERVER_TIMESTAMP,
    })

def check_one(order, window):
    reasons = []
    if order.get("qty",1) > 1:
        reasons.append(f"สั่ง {order['qty']} ชิ้น เกินกำหนด (1 ชิ้น/คน)")
    uid  = norm(order.get("user_id",""))
    name = norm(order.get("buyer_name",""))
    addr = norm(order.get("address",""))
    for b in window:
        if uid and uid == norm(b.get("user_id","")):
            reasons.append(f"User ID ซ้ำกับออเดอร์ {b.get('order_sn','')}")
        elif name and len(name)>2 and name == norm(b.get("buyer_name","")):
            reasons.append(f"ชื่อซ้ำกับออเดอร์ {b.get('order_sn','')}")
        elif addr and len(addr)>5 and addr == norm(b.get("address","")):
            reasons.append(f"ที่อยู่ซ้ำกับออเดอร์ {b.get('order_sn','')}")
    if not reasons: return True, [], ""
    types = set()
    for r in reasons:
        if "ชิ้น" in r: types.add("qty")
        elif "User" in r: types.add("user")
        elif "ชื่อ" in r: types.add("name")
        elif "ที่อยู่" in r: types.add("addr")
    vtype = "multi" if len(types)>1 else (types.pop() if types else "other")
    return False, reasons, vtype

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

def run_cycle():
    log_checker("เริ่มรอบตรวจออเดอร์...")
    tokens = load_tokens()
    if not tokens.get("access_token"):
        log_checker("ไม่มี access token"); return {"passed":[],"failed":[]}
    try: orders = fetch_new_orders(minutes_back=20)
    except Exception as e: log_checker(f"ดึงออเดอร์ไม่ได้: {e}"); return {"passed":[],"failed":[]}
    if not orders: log_checker("ไม่มีออเดอร์ใหม่"); return {"passed":[],"failed":[]}
    window = bl_window(); passed, failed = [], []
    for order in orders:
        ok, reasons, vtype = check_one(order, window)
        if ok:
            save_checked(order,"pass")
            # deduct stock
            prods = db.collection("products").where("stock",">",0).limit(1).stream()
            for p in prods:
                p.reference.update({"stock": firestore.Increment(-(order.get("qty",1)))})
            passed.append(order)
        else:
            save_checked(order,"fail",reasons); save_bl(order,vtype,reasons)
            window.append({"user_id":order.get("user_id",""),"buyer_name":order.get("buyer_name",""),"address":order.get("address",""),"order_sn":order.get("order_sn","")})
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

# ─── SEED DEFAULT PRODUCTS ────────────────────────────────────────────────────
def ensure_products():
    prods = list(db.collection("products").limit(1).stream())
    if not prods:
        for p in [
            {"item_id":"7891234560","name":"ซิมเปล่า AIS 4G/5G","stock":50,"min_stock":10,"price":49},
            {"item_id":"7891234561","name":"ซิมเปล่า DTAC 4G","stock":12,"min_stock":10,"price":39},
            {"item_id":"7891234562","name":"ซิมเปล่า TRUE Move H","stock":0,"min_stock":5,"price":39},
        ]:
            db.collection("products").add({**p,"updated_at":firestore.SERVER_TIMESTAMP})

ensure_products()

# ─── API ROUTES ───────────────────────────────────────────────────────────────
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

@app.route("/api/stats")
def stats():
    today = datetime.now().replace(hour=0,minute=0,second=0,microsecond=0)
    cutoff30 = datetime.now() - timedelta(days=WINDOW_DAYS)
    prods = [d.to_dict() for d in db.collection("products").stream()]
    total_stock = sum(p.get("stock",0) for p in prods)
    orders_today = len(list(db.collection("checked_orders").where("checked_at",">=",today).stream()))
    pass_today   = len(list(db.collection("checked_orders").where("checked_at",">=",today).where("status","==","pass").stream()))
    fail_today   = len(list(db.collection("checked_orders").where("checked_at",">=",today).where("status","==","fail").stream()))
    bl_active    = len(list(db.collection("blacklist").where("created_at",">=",cutoff30).stream()))
    pending      = len(list(db.collection("blacklist").where("created_at",">=",cutoff30).where("sent_empty","==",False).stream()))
    return jsonify({"stock":total_stock,"orders_today":orders_today,"pass_today":pass_today,"fail_today":fail_today,"bl_active":bl_active,"pending_send":pending})

@app.route("/api/checker/status")
def checker_status():
    return jsonify({"running":checker_state["running"],"last_run":checker_state["last_run"],"next_run":checker_state["next_run"],"interval":checker_state["interval"],"log":checker_state["log"][:50]})

@app.route("/api/checker/start", methods=["POST"])
def checker_start():
    global checker_thread
    if checker_state["running"]: return jsonify({"ok":False,"msg":"กำลังทำงานอยู่แล้ว"})
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
    days=int(request.args.get("days",30)); q=request.args.get("q","").lower()
    vt=request.args.get("type",""); st=request.args.get("sent","")
    cutoff = datetime.now()-timedelta(days=days)
    docs = db.collection("blacklist").where("created_at",">=",cutoff).order_by("created_at",direction=firestore.Query.DESCENDING).stream()
    result=[]
    for d in docs:
        r={"id":d.id,**d.to_dict()}
        if vt and r.get("violation")!=vt: continue
        if st=="pending" and r.get("sent_empty"): continue
        if st=="sent" and not r.get("sent_empty"): continue
        if q and not any(q in str(r.get(k,"")).lower() for k in ["order_sn","buyer_name","user_id"]): continue
        r["created_at"]=fmt_dt(r.get("created_at")); r["age_days"]=0
        result.append(r)
    return jsonify(result)

@app.route("/api/blacklist/<doc_id>/sent", methods=["POST"])
def mark_sent(doc_id):
    db.collection("blacklist").document(doc_id).update({"sent_empty":True}); return jsonify({"ok":True})

@app.route("/api/blacklist/<doc_id>", methods=["DELETE"])
def del_bl(doc_id):
    db.collection("blacklist").document(doc_id).delete(); return jsonify({"ok":True})

@app.route("/api/blacklist/add", methods=["POST"])
def add_bl_manual():
    d=request.json
    order={"order_sn":d.get("order_sn","manual_"+str(int(time.time()))),"user_id":d.get("user_id",""),"buyer_name":d.get("buyer_name",""),"phone":d.get("phone",""),"address":d.get("address",""),"qty":d.get("qty",1)}
    save_bl(order,d.get("violation","user"),[d.get("reason","บันทึกด้วยตนเอง")]); return jsonify({"ok":True})

@app.route("/api/orders")
def get_orders():
    days=int(request.args.get("days",7)); q=request.args.get("q","").lower(); st=request.args.get("status","")
    cutoff=datetime.now()-timedelta(days=days)
    ref=db.collection("checked_orders").where("checked_at",">=",cutoff).order_by("checked_at",direction=firestore.Query.DESCENDING).limit(500)
    result=[]
    for d in ref.stream():
        r={"id":d.id,**d.to_dict()}
        if st and r.get("status")!=st: continue
        if q and not any(q in str(r.get(k,"")).lower() for k in ["order_sn","buyer_name","user_id"]): continue
        r["checked_at"]=fmt_dt(r.get("checked_at")); result.append(r)
    return jsonify(result)

@app.route("/api/products")
def get_products():
    docs=[{"id":d.id,**d.to_dict()} for d in db.collection("products").stream()]
    for r in docs: r["updated_at"]=fmt_dt(r.get("updated_at"))
    return jsonify(sorted(docs,key=lambda x:x.get("name","")))

@app.route("/api/products", methods=["POST"])
def add_product():
    d=request.json
    existing=list(db.collection("products").where("item_id","==",d["item_id"]).stream())
    if existing: existing[0].reference.update({"name":d["name"],"stock":d.get("stock",0),"min_stock":d.get("min_stock",10),"price":d.get("price",0),"updated_at":firestore.SERVER_TIMESTAMP})
    else: db.collection("products").add({"item_id":d["item_id"],"name":d["name"],"stock":d.get("stock",0),"min_stock":d.get("min_stock",10),"price":d.get("price",0),"updated_at":firestore.SERVER_TIMESTAMP})
    return jsonify({"ok":True})

@app.route("/api/products/<doc_id>", methods=["PUT"])
def update_product(doc_id):
    d=request.json; upd={k:d[k] for k in ["name","stock","min_stock","price"] if k in d}
    upd["updated_at"]=firestore.SERVER_TIMESTAMP
    db.collection("products").document(doc_id).update(upd); return jsonify({"ok":True})

@app.route("/api/products/<doc_id>", methods=["DELETE"])
def del_product(doc_id):
    db.collection("products").document(doc_id).delete(); return jsonify({"ok":True})

@app.route("/api/summary")
def get_summary():
    today=datetime.now().replace(hour=0,minute=0,second=0,microsecond=0)
    passed=[d.to_dict() for d in db.collection("checked_orders").where("checked_at",">=",today).where("status","==","pass").stream()]
    failed=[d.to_dict() for d in db.collection("checked_orders").where("checked_at",">=",today).where("status","==","fail").stream()]
    prods=[d.to_dict() for d in db.collection("products").stream()]
    stock=sum(p.get("stock",0) for p in prods)
    cutoff30=datetime.now()-timedelta(days=30)
    blc=len(list(db.collection("blacklist").where("created_at",">=",cutoff30).stream()))
    d=datetime.now(); m=["","ม.ค.","ก.พ.","มี.ค.","เม.ย.","พ.ค.","มิ.ย.","ก.ค.","ส.ค.","ก.ย.","ต.ค.","พ.ย.","ธ.ค."]
    thai=f"{d.day} {m[d.month]} {d.year+543}"
    lines=[f"===== ใบสรุปออเดอร์ซิมเปล่า =====",f"วันที่: {thai}",f"สต็อก: {stock} ชิ้น | บัญชีดำ: {blc} รายการ",f"ตรวจ: {len(passed)+len(failed)} | ผ่าน: {len(passed)} | ผิดเงื่อนไข: {len(failed)}",""]
    if passed:
        lines.append(f"----- จัดส่งซิม ({len(passed)} ออเดอร์) -----")
        for i,r in enumerate(passed,1): lines+=[f"{i}. ออเดอร์: {r.get('order_sn','-')}",f"   ชื่อ: {r.get('buyer_name','-')} | User: {r.get('user_id','-')}",f"   เบอร์: {r.get('phone','-')}",f"   ที่อยู่: {r.get('address','-')}",f"   จำนวน: {r.get('qty',1)} ชิ้น",""]
    if failed:
        lines.append(f"----- ส่งซองเปล่า ({len(failed)} ออเดอร์) -----")
        for i,r in enumerate(failed,1):
            reasons=" / ".join(r.get("reasons",[]))
            lines+=[f"{i}. ออเดอร์: {r.get('order_sn','-')}",f"   ชื่อ: {r.get('buyer_name','-')} | User: {r.get('user_id','-')}",f"   เบอร์: {r.get('phone','-')}",f"   ที่อยู่: {r.get('address','-')}",f"   สาเหตุ: {reasons}",""]
    lines.append("==============================")
    return jsonify({"text":"\n".join(lines),"passed":len(passed),"failed":len(failed)})

@app.route("/api/db/status")
def db_status():
    try: db.collection("products").limit(1).stream(); return jsonify({"ok":True,"msg":"Firestore เชื่อมต่อสำเร็จ"})
    except Exception as e: return jsonify({"ok":False,"msg":str(e)})

# Entry point for Firebase Functions
from firebase_functions import https_fn

@https_fn.on_request()
def api(req: https_fn.Request) -> https_fn.Response:
    with app.request_context(req.environ):
        return app.full_dispatch_request()
