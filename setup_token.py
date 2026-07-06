"""
setup_token.py — ขอ Shopee Access Token แบบกดปุ่มเดียวจบ
รันครั้งเดียว: python setup_token.py
"""
import hashlib, hmac, time, json, threading, webbrowser, urllib.parse
import http.server, requests
from pathlib import Path

# ─── ใส่ข้อมูลร้านตรงนี้ ───────────────────────────────
SHOP_ID     = 0          # ← ใส่ Shop ID ของร้าน (ตัวเลขเท่านั้น)
USE_LIVE    = True       # True = Live, False = Test
# ────────────────────────────────────────────────────────

LIVE = {"pid": 2037264,  "key": "shpk7647596d72617a70676e77634b7a7148736b467349537575774d57657243", "url": "https://partner.shopeemobile.com"}
TEST = {"pid": 1236318,  "key": "shpk4b41694b50746f4c584e6f79716e776350754a4251594364734d6676684c", "url": "https://partner.test-stable.shopeemobile.com"}
CFG  = LIVE if USE_LIVE else TEST
PORT = 8899
OUT  = Path(__file__).parent / "tokens.json"

def sign(path, ts):
    base = f"{CFG['pid']}{path}{ts}"
    return hmac.new(CFG["key"].encode(), base.encode(), hashlib.sha256).hexdigest()

def main():
    if not SHOP_ID:
        print("\n⚠️  กรุณาใส่ SHOP_ID ในไฟล์ setup_token.py แล้วรันใหม่")
        print("   วิธีหา Shop ID: เปิด Seller Center → ดูจาก URL หรือหน้าตั้งค่าร้าน\n")
        return

    mode = "Live" if USE_LIVE else "Test"
    print(f"\n{'='*50}")
    print(f"  Shopee Token Setup — {mode} Mode")
    print(f"  Shop ID: {SHOP_ID}")
    print(f"{'='*50}\n")

    code_holder, event = {}, threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            qs   = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code = qs.get("code", [None])[0]
            if code:
                code_holder["code"] = code
                html = b"""<html><head><meta charset="utf-8">
                <style>body{font-family:sans-serif;display:flex;align-items:center;
                justify-content:center;height:100vh;margin:0;background:#f0fdf4}
                .box{text-align:center;padding:2rem;background:#fff;border-radius:12px;
                border:1px solid #bbf7d0;max-width:400px}
                h2{color:#15803d;margin-bottom:.5rem}p{color:#6b7280;font-size:14px}</style></head>
                <body><div class="box"><h2>&#10003; Authorization สำเร็จ!</h2>
                <p>ปิดหน้าต่างนี้ได้เลย<br>กลับไปดูผลในหน้าต่าง Terminal</p></div></body></html>"""
                self.send_response(200); self.end_headers(); self.wfile.write(html)
                event.set()
            else:
                self.send_response(400); self.end_headers()
                self.wfile.write(b"<h2>ไม่พบ code กรุณาลองใหม่</h2>")
        def log_message(self, *a): pass

    srv = http.server.HTTPServer(("", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    ts       = int(time.time())
    path_auth = "/api/v2/shop/auth_partner"
    sig      = sign(path_auth, ts)
    redirect = f"http://localhost:{PORT}/callback"
    auth_url = (f"{CFG['url']}{path_auth}"
                f"?partner_id={CFG['pid']}&timestamp={ts}&sign={sig}"
                f"&redirect={urllib.parse.quote(redirect)}")

    print("  กำลังเปิด browser เพื่อ Authorize กับ Shopee...")
    print("  ถ้า browser ไม่เปิดอัตโนมัติ คัดลอก URL ด้านล่างไปวางในเบราว์เซอร์:\n")
    print(f"  {auth_url}\n")
    webbrowser.open(auth_url)
    print("  รอการ Authorize... (มีเวลา 2 นาที)\n")

    if not event.wait(timeout=120):
        print("  ⚠️  หมดเวลา 2 นาที ไม่ได้รับการ Authorize")
        srv.shutdown(); return

    srv.shutdown()
    code = code_holder.get("code")
    print(f"  ✓ ได้รับ Authorization Code แล้ว")
    print(f"  กำลังแลก code เป็น access token...\n")

    ts2       = int(time.time())
    path_tok  = "/api/v2/auth/token/get"
    sig2      = sign(path_tok, ts2)
    r = requests.post(
        CFG["url"] + path_tok,
        params={"partner_id": CFG["pid"], "timestamp": ts2, "sign": sig2},
        json={"code": code, "shop_id": SHOP_ID, "partner_id": CFG["pid"]},
        timeout=15
    )
    data = r.json()

    if not data.get("access_token"):
        print(f"  ✗ แลก token ล้มเหลว: {data}")
        return

    tokens = {
        "access_token":  data["access_token"],
        "refresh_token": data.get("refresh_token", ""),
        "expire_in":     data.get("expire_in", 14400),
        "updated_at":    int(time.time()),
        "shop_id":       SHOP_ID,
        "mode":          "live" if USE_LIVE else "test",
    }
    OUT.write_text(json.dumps(tokens, indent=2, ensure_ascii=False))

    print(f"{'='*50}")
    print(f"  ✓ สำเร็จ! บันทึก tokens.json แล้ว")
    print(f"  Access Token : {data['access_token'][:20]}...")
    print(f"  หมดอายุใน   : {data.get('expire_in', 14400) // 3600} ชั่วโมง")
    print(f"{'='*50}")
    print(f"\n  ขั้นตอนถัดไป:")
    print(f"  1. รัน: python app.py")
    print(f"  2. เปิด: http://localhost:5000")
    print(f"  3. ไปที่ 'ระบบตรวจอัตโนมัติ' → กด 'เริ่มระบบ'\n")

if __name__ == "__main__":
    main()
