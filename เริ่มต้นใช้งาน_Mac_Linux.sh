#!/bin/bash
echo ""
echo "===================================================="
echo "  Shopee Sim Manager — ติดตั้งและเริ่มต้นใช้งาน"
echo "===================================================="
echo ""

# ตรวจสอบ Python
if ! command -v python3 &> /dev/null; then
    echo "[ข้อผิดพลาด] ไม่พบ Python3 กรุณาติดตั้งก่อน"
    exit 1
fi

# ติดตั้ง dependencies
echo "[1/3] ติดตั้ง dependencies..."
pip3 install flask flask-cors requests --quiet
echo "      เสร็จแล้ว"

# ถ้ายังไม่มี token ให้ขอก่อน
if [ ! -f "tokens.json" ]; then
    echo ""
    echo "[2/3] ขอ Shopee Access Token..."
    echo "      กำลังเปิด browser — กรุณา Authorize ใน Shopee"
    echo ""
    python3 setup_token.py
    if [ ! -f "tokens.json" ]; then
        echo "[ข้อผิดพลาด] ไม่ได้รับ token กรุณาลองใหม่"
        exit 1
    fi
else
    echo "[พบ tokens.json] ข้ามขั้นตอนขอ token"
fi

echo ""
echo "[3/3] เริ่ม Web Server..."
echo ""
echo "===================================================="
echo "  เปิดเบราว์เซอร์ไปที่: http://localhost:5000"
echo "===================================================="
echo ""

# เปิด browser อัตโนมัติ
sleep 1
if command -v open &> /dev/null; then
    open http://localhost:5000
elif command -v xdg-open &> /dev/null; then
    xdg-open http://localhost:5000
fi

python3 app.py
