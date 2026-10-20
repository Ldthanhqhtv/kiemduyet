"""
Script tự động đăng nhập Gemini Web và lưu phiên vĩnh viễn trên ổ E.
Profile lưu tại: E:\kiem duyet bao\gemini_profile
"""

import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

PROFILE_DIR = Path(r"E:\kiem duyet bao\gemini_profile")
PROFILE_DIR.mkdir(parents=True, exist_ok=True)

EMAIL = "luuthanh.quochoi@gmail.com"
PASS = "Ldthanhvtv0"

def auto_login(headless=False):
    print("=" * 60)
    print("  KHOI DONG TRINH DUYET DANG NHAP GEMINI WEB")
    print(f"  Profile luu tai: {PROFILE_DIR}")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="msedge",
            headless=headless,
            viewport={"width": 1280, "height": 800},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars"
            ],
            ignore_default_args=["--enable-automation"]
        )

        page = browser.pages[0] if browser.pages else browser.new_page()

        # Loai bo dau vet navigator.webdriver de tranh bi Google chan
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)

        print("[1/4] Kiem tra trang thai Gemini Web...")
        page.goto("https://gemini.google.com/app", timeout=30000)
        time.sleep(3)

        # Kiem tra neu da dang nhap roi
        current_url = page.url
        print(f"URL hien tai: {current_url}")

        # Kiem tra neu da dang nhap roi: co account info va KHONG co nut Sign in
        has_sign_in_btn = page.locator("a:has-text('Sign in'), button:has-text('Sign in'), a:has-text('Đăng nhập'), button:has-text('Đăng nhập')").count() > 0
        has_account_badge = page.locator("[aria-label*='Google Account'], [aria-label*='Tài khoản Google'], .user-avatar, img[src*='googleusercontent.com']").count() > 0
        is_logged_in = has_account_badge and not has_sign_in_btn

        if is_logged_in and "accounts.google.com" not in current_url:
            print(">>> DA DANG NHAP SAN (Tai khoan Admin Pro)! Phien Gemini Web hoat dong tot.")
            browser.close()
            return True

        print("[2/4] Chua dang nhap, tien hanh dang nhap bang tai khoan admin...")
        login_url = "https://accounts.google.com/ServiceLogin?passive=1209600&continue=https://gemini.google.com/app&followup=https://gemini.google.com/app&ec=GAZAkgU"
        page.goto(login_url, timeout=35000)
        time.sleep(4)

        # Dien Email
        print(f"[3/4] Dien email: {EMAIL}")
        email_input = page.locator("input[type='email'], input#identifierId")
        if email_input.count() > 0:
            email_input.first.fill(EMAIL)
            time.sleep(1)
            # Bam Next
            page.locator("button:has-text('Next'), button:has-text('Tiếp theo'), #identifierNext").first.click()
            time.sleep(3)

        # Dien Password
        print("[4/4] Dien mat khau...")
        try:
            page.wait_for_selector("input[type='password'], input[name='Passwd']", timeout=10000)
            pwd_input = page.locator("input[type='password'], input[name='Passwd']")
            if pwd_input.count() > 0:
                pwd_input.first.fill(PASS)
                time.sleep(1)
                page.locator("button:has-text('Next'), button:has-text('Tiếp theo'), #passwordNext").first.click()
                print("Da gui mat khau, dang cho xac thuc...")
                time.sleep(5)
        except Exception as e:
            print(f"Loi hoac khong tim thay o mat khau: {e}")

        # Cho xac thuc (2FA neu co hoac chuyen huong ve gemini)
        print("Cho chuyen huong ve Gemini...")
        for i in range(30):
            url = page.url
            if "gemini.google.com" in url and "accounts.google.com" not in url:
                print(">>> DANG NHAP THANH CONG VAO GEMINI WEB!")
                time.sleep(3)
                browser.close()
                return True
            time.sleep(2)

        print(f"Ket thuc thoi gian cho. URL cuoi cung: {page.url}")
        browser.close()
        return False

if __name__ == "__main__":
    auto_login(headless=False)
