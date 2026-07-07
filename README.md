# Shopee Sim Manager — Firebase

## Stack ฟรีถาวร ไม่ sleep
- **Firebase Hosting** — host เว็บ
- **Firebase Cloud Functions** — Python backend
- **Firestore** — ฐานข้อมูล

## ขั้นตอน Deploy

### 1. สร้าง Firebase Project
1. ไปที่ [console.firebase.google.com](https://console.firebase.google.com)
2. กด **Add project** → ตั้งชื่อ `shopee-sim-manager`
3. เปิดใช้ **Firestore Database** (ใช้ Production mode)
4. เปิดใช้ **Functions** (ต้องอัปเกรดเป็น Blaze plan — จ่ายตามจริง แต่ฟรีถ้าใช้ไม่เกิน quota)

### 2. ติดตั้ง Firebase CLI
```bash
npm install -g firebase-tools
firebase login
```

### 3. Deploy
```bash
firebase use --add   # เลือก project
firebase deploy
```

### 4. เปิดใช้งาน
เปิด URL ที่ได้จาก Firebase Hosting แล้วไปที่ **ตั้งค่า** ใส่ Shop ID + Token
