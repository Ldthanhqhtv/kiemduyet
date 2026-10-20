"""core/gemini_capture_sync.py - Tu dong bat Cookie Gemini Web va dong bo len may tu xa.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Tuple

# Dam bao in tieng Viet UTF-8 tren Windows CMD
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

PROFILE_DIR = BASE_DIR / "gemini_profile"
CONFIG_PATH = BASE_DIR / "config" / "gemini_session.json"
ENC_CONFIG_PATH = BASE_DIR / "config" / "gemini_session.enc"


def copy_to_clipboard(text: str) -> bool:
    """Sao chep chuoi vao khay nho tam Windows."""
    try:
        process = subprocess.Popen(["clip.exe"], stdin=subprocess.PIPE, close_fds=True)
        process.communicate(input=text.encode("utf-16le"))
        return True
    except Exception:
        return False


def run_safe_async(coro: Any) -> Any:
    """Chay coroutine trong thread rieng biet de khong bao gio bi xung dot running event loop."""
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


async def test_cookies_validity(psid: str, psidts: str = "") -> Tuple[bool, str]:
    """Kiem tra xem cap Cookie co con hieu luc that su khong bang AccountStatus."""
    try:
        from gemini_webapi import GeminiClient
        from gemini_webapi.constants import AccountStatus

        client = GeminiClient(secure_1psid=psid, secure_1psidts=psidts)
        await client.init(timeout=15, auto_refresh=False)
        status = getattr(client, "account_status", None)
        await client.close()

        if status == AccountStatus.AVAILABLE:
            return True, "Cookie hợp lệ, kết nối Gemini Web thành công!"
        elif status == AccountStatus.UNAUTHENTICATED or status == 1016:
            return False, "Cookie ĐÃ HẾT HẠN (UNAUTHENTICATED) - Google yêu cầu đăng nhập lại!"
        else:
            return False, f"Tài khoản không khả dụng (Trạng thái: {status})"
    except Exception as exc:
        err_msg = str(exc)
        if "UNAUTHENTICATED" in err_msg or "cookie" in err_msg.lower() or "auth" in err_msg.lower():
            return False, f"Cookie ĐÃ HẾT HẠN hoặc bị Google từ chối ({err_msg})"
        return False, f"Lỗi xác thực: {err_msg}"


def run_capture_and_sync() -> None:
    print("=" * 70)
    print("  🚀 CÔNG CỤ TỰ ĐỘNG BẮT COOKIE & ĐỒNG BỘ GEMINI WEB CHO DỰ ÁN")
    print(f"  Thư mục dữ liệu: {BASE_DIR}")
    print("=" * 70)
    print()

    from playwright.sync_api import sync_playwright
    from core.gemini_web_engine import save_gemini_session

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    print("👉 [1/4] Đang mở trình duyệt Microsoft Edge (Profile lưu an toàn trên ổ E)...")
    print("👉 Nếu chưa đăng nhập: Hãy đăng nhập tài khoản Google của bạn trên cửa sổ vừa mở.")
    print("👉 Script sẽ TỰ ĐỘNG BẮT COOKIE ngay khi bạn vào đến trang Gemini Web.")
    print("----------------------------------------------------------------------")

    captured_psid = ""
    captured_psidts = ""

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="msedge",
            headless=False,
            viewport={"width": 1280, "height": 850},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars",
            ],
            ignore_default_args=["--enable-automation"],
        )

        page = browser.pages[0] if browser.pages else browser.new_page()

        # Xoa bo navigator.webdriver de tranh Google bat bot
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)

        print("👉 Đang tải trang https://gemini.google.com/app ...")
        try:
            page.goto("https://gemini.google.com/app", timeout=45000)
        except Exception:
            pass

        print("⏳ Đang lắng nghe Cookie phiên đăng nhập (tối đa 3 phút)...")
        start_time = time.time()
        while time.time() - start_time < 180:
            cookies = browser.cookies(["https://gemini.google.com", "https://google.com"])
            cookie_dict = {c["name"]: c["value"] for c in cookies}

            if "__Secure-1PSID" in cookie_dict:
                captured_psid = cookie_dict["__Secure-1PSID"]
                captured_psidts = cookie_dict.get("__Secure-1PSIDTS", "")

                current_url = page.url
                if "gemini.google.com" in current_url and "accounts.google.com" not in current_url:
                    print("\n🎯 ĐÃ BẮT ĐƯỢC COOKIE TỪ TRÌNH DUYỆT!")
                    print(f"   - __Secure-1PSID : {captured_psid[:18]}...{captured_psid[-8:]}")
                    if captured_psidts:
                        print(f"   - __Secure-1PSIDTS: {captured_psidts[:18]}...{captured_psidts[-8:]}")
                    break

            time.sleep(2)

        browser.close()

    if not captured_psid:
        print("\n❌ HẾT THỜI GIAN CHỜ: Chưa bắt được Cookie __Secure-1PSID.")
        print("👉 Vui lòng mở lại script và đăng nhập hoàn tất vào tài khoản Google.")
        return

    print("\n👉 [2/4] Đang kiểm tra tính hợp lệ của Cookie...")
    is_valid, msg = run_safe_async(test_cookies_validity(captured_psid, captured_psidts))

    if not is_valid:
        print("=" * 70)
        print("  ⚠️ CẢNH BÁO: COOKIE VỪA LẤY ĐÃ BỊ HẾT HIỆU LỰC HOẶC LỖI!")
        print(f"  Chi tiết: {msg}")
        print("  👉 Vui lòng tải lại trang trên trình duyệt hoặc đăng nhập lại.")
        print("=" * 70)
        return

    print(f"✅ XÁC THỰC THÀNH CÔNG: {msg}")

    print("\n👉 [3/4] Lưu Cookie vào máy và mã hóa đồng bộ...")
    save_gemini_session(
        secure_1psid=captured_psid,
        secure_1psidts=captured_psidts,
        account_email="luuthanh.quochoi@gmail.com",
        authuser="0",
    )
    print("   - Đã lưu file cấu hình: config/gemini_session.json (ổ E)")
    print("   - Đã mã hóa file đồng bộ: config/gemini_session.enc (chuẩn AES)")

    # Sao chep dinh dang Secrets vao Clipboard phong khi can
    secrets_block = (
        f'SECURE_1PSID = "{captured_psid}"\n'
        f'SECURE_1PSIDTS = "{captured_psidts}"\n'
        f'GEMINI_ACCOUNT_EMAIL = "luuthanh.quochoi@gmail.com"\n'
    )
    if copy_to_clipboard(secrets_block):
        print("   - Đã copy sẵn đoạn mã cấu hình vào Clipboard (Ctrl + V)")

    print("\n👉 [4/4] Tự động đẩy file mã hóa lên GitHub để máy từ xa nhận ngay...")
    try:
        subprocess.run(["git", "add", "config/gemini_session.enc"], cwd=str(BASE_DIR), check=True)
        res_commit = subprocess.run(
            ["git", "commit", "-m", "Dong bo phien Gemini Web moi nhat [automated]"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
        )
        print(f"   Git commit: {res_commit.stdout.strip() or 'Đã commit'}")

        print("   Đang đẩy lên GitHub (git push)...")
        res_push = subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if res_push.returncode == 0:
            print("   ✅ ĐÃ ĐẨY LÊN GITHUB THÀNH CÔNG!")
        else:
            print(f"   ⚠️ Chưa tự push được: {res_push.stderr.strip() or res_push.stdout.strip()}")
            print("   👉 Bạn có thể mở CMD gõ: git push origin main")
    except Exception as exc:
        print(f"   ⚠️ Lưu ý Git: {exc}")

    print()
    print("=" * 70)
    print("  🎉 HOÀN TẤT ĐỒNG BỘ!")
    print("  - Streamlit Cloud sẽ tự động nạp phiên mới trong 30-60 giây.")
    print("  - Người dùng từ xa chỉ cần mở web -> nhập PIN là dùng được ngay,")
    print("    HOÀN TOÀN KHÔNG CẦN NHẬP COOKIE!")
    print("=" * 70)


def check_current_session() -> None:
    """Kiem tra nhanh phien hien tai con song hay da chet."""
    print("=" * 70)
    print("  🔍 KIỂM TRA HIỆU LỰC PHIÊN GEMINI WEB HIỆN TẠI")
    print("=" * 70)

    from core.gemini_web_engine import load_gemini_session

    cfg = load_gemini_session()
    psid = cfg.get("secure_1psid")
    psidts = cfg.get("secure_1psidts") or ""

    if not psid:
        print("❌ Chưa có cấu hình Cookie __Secure-1PSID nào trong máy!")
        return

    print(f"Đang kiểm tra: 1PSID={psid[:15]}... | 1PSIDTS={psidts[:15]}...")
    is_valid, msg = run_safe_async(test_cookies_validity(psid, psidts))

    if is_valid:
        print(f"✅ PHIÊN HOẠT ĐỘNG TỐT: {msg}")
    else:
        print("=" * 70)
        print("  ⚠️ CẢNH BÁO NGUY HIỂM: PHIÊN GEMINI WEB ĐÃ HẾT HẠN!")
        print(f"  Nguyên nhân: {msg}")
        print("  👉 Vui lòng chạy file 'dong_bo_gemini.bat' để đăng nhập lại!")
        print("=" * 70)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        check_current_session()
    else:
        run_capture_and_sync()
