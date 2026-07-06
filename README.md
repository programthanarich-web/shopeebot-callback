# Shopee Sim Manager — Deploy Guide

## Stack
- **Backend**: Flask + PostgreSQL (Supabase)
- **Host**: Render (ฟรีถาวร)
- **DB**: Supabase (ฟรีถาวร 500MB)

## ขั้นตอน Deploy

### 1. สร้างฐานข้อมูล Supabase (ฟรี)
1. ไปที่ [supabase.com](https://supabase.com) → Sign in with GitHub
2. กด **New Project** → ตั้งชื่อ `sim-manager` → ตั้ง password → Create
3. รอ 1-2 นาที → ไปที่ **Settings → Database**
4. คัดลอก **Connection string (URI)** → เก็บไว้ใช้ใน Render

### 2. Deploy บน Render (ฟรี)
1. ไปที่ [render.com](https://render.com) → Sign in with GitHub
2. กด **New → Web Service**
3. เลือก repo `programthanarich-web/shopeebot-callback`
4. ตั้งค่า:
   - Name: `shopee-sim-manager`
   - Runtime: `Python 3`
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120`
5. Environment Variables → เพิ่ม:
   - `DATABASE_URL` = (connection string จาก Supabase)
6. กด **Create Web Service** → รอ build เสร็จ

### 3. เปิดใช้งาน
- เปิด URL ที่ได้จาก Render เช่น `https://shopee-sim-manager.onrender.com`
- ไปที่ **ตั้งค่า** → ใส่ Shop ID และ Access Token

## หมายเหตุ
- Render ฟรี: app จะ sleep เมื่อไม่มีการใช้งาน 15 นาที (ตื่นใน ~30 วินาที)
- ข้อมูลทั้งหมดเก็บใน Supabase PostgreSQL — **ไม่หายแม้ restart**
- Supabase ฟรี: 500MB, 2 projects
