@echo off
chcp 65001 >nul
title Shopee Sim Manager — Setup
echo.
echo ====================================================
echo   Shopee Sim Manager — ติดตั้งและเริ่มต้นใช้งาน
echo ====================================================
echo.

REM ตรวจสอบ Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ข้อผิดพลาด] ไม่พบ Python กรุณาติดตั้ง Python 3.9+ ก่อน
    echo https://www.python.org/downloads/
    pause
    exit /b
)

REM ติดตั้ง dependencies
echo [1/3] ติดตั้ง dependencies...
pip install flask flask-cors requests --quiet
echo       เสร็จแล้ว

REM ถามว่ามี token แล้วหรือยัง
if exist tokens.json (
    echo.
    echo [พบ tokens.json] ข้ามขั้นตอนขอ token
    goto :start_app
)

REM ขอ token
echo.
echo [2/3] ขอ Shopee Access Token...
echo       กำลังเปิด browser — กรุณา Authorize ใน Shopee
echo.
python setup_token.py
if not exist tokens.json (
    echo.
    echo [ข้อผิดพลาด] ไม่ได้รับ token กรุณาลองใหม่
    pause
    exit /b
)

:start_app
echo.
echo [3/3] เริ่ม Web Server...
echo.
echo ====================================================
echo   เปิดเบราว์เซอร์ไปที่: http://localhost:5000
echo ====================================================
echo.
start http://localhost:5000
python app.py
pause
