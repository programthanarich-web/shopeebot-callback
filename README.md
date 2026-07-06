# Shopee Sim Manager — วิธีเริ่มต้นใช้งาน

## ขั้นตอนที่ 1 — ใส่ Shop ID

เปิดไฟล์ `setup_token.py` บรรทัดที่ 12:
```python
SHOP_ID = 0   # ← เปลี่ยนเป็น Shop ID ของร้าน เช่น 123456789
```

**หา Shop ID ได้จาก:** Shopee Seller Center → คลิกชื่อร้าน → ดูในหน้าตั้งค่าร้าน
หรือดูจาก URL: `seller.shopee.co.th/portal/shop/`**123456789**`/`

---

## ขั้นตอนที่ 2 — เริ่มต้นใช้งาน

### Windows
ดับเบิลคลิกที่ไฟล์:
```
เริ่มต้นใช้งาน_Windows.bat
```

### Mac / Linux
```bash
chmod +x เริ่มต้นใช้งาน_Mac_Linux.sh
./เริ่มต้นใช้งาน_Mac_Linux.sh
```

ระบบจะ:
1. ติดตั้ง dependencies อัตโนมัติ
2. เปิด browser ให้กด Authorize กับ Shopee (ทำครั้งเดียว)
3. เปิดเว็บแอพที่ http://localhost:5000

---

## ขั้นตอนที่ 3 — ใช้งาน

1. ไปที่หน้า **ระบบตรวจอัตโนมัติ** → กด **เริ่มระบบ**
2. ระบบจะดึงออเดอร์ใหม่จาก Shopee ตรวจสอบเงื่อนไขและบันทึกบัญชีดำให้อัตโนมัติ

---

## รันครั้งต่อไป (มี token แล้ว)

### Windows
```bash
python app.py
```
### Mac/Linux
```bash
python3 app.py
```
แล้วเปิด http://localhost:5000

---

## ไฟล์สำคัญ
| ไฟล์ | หน้าที่ |
|------|--------|
| `setup_token.py` | ขอ Shopee token (รันครั้งแรกครั้งเดียว) |
| `app.py` | Web server หลัก |
| `tokens.json` | Token ที่ได้ (สร้างอัตโนมัติ) |
| `data.db` | ฐานข้อมูล SQLite |
