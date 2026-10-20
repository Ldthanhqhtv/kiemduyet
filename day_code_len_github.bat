@echo off
chcp 65001 >nul
title Day Code Va Cookie Ma Hoa Len GitHub - Toa Soan VOV2

echo ======================================================================
echo   DANG DAY CODE VA PHIEN COOKIE GEMINI LEN GITHUB (STREAMLIT CLOUD)
echo ======================================================================
echo.

cd /d "E:\kiem duyet bao"
git status -s
echo.
echo Dang day len nhanh main cua GitHub: https://github.com/Ldthanhqhtv/kiemduyet ...
echo (Neu co cua so trinh duyet hoi dang nhap GitHub, ban chi can bam Authorize)
echo.

git push -u origin main --force

echo.
echo ======================================================================
if %errorlevel% equ 0 (
    echo   [OK] DA DAY LEN GITHUB THANH CONG!
    echo   Streamlit Cloud se tu dong nhan phien moi sau 30-60 giay.
) else (
    echo   [CANH BAO] Chua day duoc. Vui long kiem tra lai ket noi hoac dang nhap GitHub.
)
echo ======================================================================
echo   Nhan phim bat ky de thoat...
pause >nul
