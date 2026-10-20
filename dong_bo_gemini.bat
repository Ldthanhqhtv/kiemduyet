@echo off
chcp 65001 >nul
title Dong Bo Phien Gemini Web - Toa Soan VOV2

echo ======================================================================
echo   DONG BO PHIEN GOOGLE GEMINI WEB LEN APP TU XA (STREAMLIT CLOUD)
echo ======================================================================
echo.

cd /d "E:\kiem duyet bao"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "core\gemini_capture_sync.py"
) else (
    python "core\gemini_capture_sync.py"
)

echo.
echo ======================================================================
echo   Nhan phim bat ky de dong cua so nay...
echo ======================================================================
pause >nul
