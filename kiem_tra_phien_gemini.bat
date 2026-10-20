@echo off
chcp 65001 >nul
title Kiem Tra Hieu Luc Phien Gemini Web

cd /d "E:\kiem duyet bao"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "core\gemini_capture_sync.py" --check
) else (
    python "core\gemini_capture_sync.py" --check
)

echo.
echo ======================================================================
echo   Nhan phim bat ky de thoat...
echo ======================================================================
pause >nul
