"""core/gemini_web_account.py - Ghim tai khoan Google & thu thap cookie Gemini Web.

TASK_10 — Multi-Account Shield
===============================
Nguoi dung thuong dang nhap nhieu tai khoan Google cung luc tren may (vi du:
Gmail dung tai khoan cong viec, Gemini dung tai khoan ca nhan). Neu app goi
RPC ngam ma khong ghim tai khoan, request co the nhay sang tai khoan cong viec
— va viec tu rotate token co the lam **vang phien dang nhap Gmail** tren trinh
duyet.

Module nay giai quyet 3 van de:

1. **Ghim tai khoan (Account Pinning)** — doc ``Local State`` cua Chrome de biet
   profile nao dang dang nhap email nao, roi chi lay cookie cua DUNG profile do.
   Khong bao gio dung "profile vua dung nhat" (nguon gay chon nham tai khoan).
2. **Chi so ``/u/N/`` (Multi-login Routing)** — Google phan biet cac tai khoan
   da dang nhap bang *chi so* trong duong dan (``/u/0/``, ``/u/1/``...). Chi so
   nay suy ra tu thu tu tai khoan trong cac profile Chrome. **Mac dinh ``0``
   co the tro toi tai khoan Gmail cong viec** — phai ghim dung.
3. **Thu thap cookie** — theo thu tu: bien moi truong -> Chrome ( qua
   ``browser_cookie3``, doc store cua profile da ghim) -> Chrome DevTools
   Protocol (neu ``GEMINI_CDP_URL`` duoc dat).

.. note::
   Module nay **khong** dung GUI (khong ``ctypes.windll.user32``, khong bam
   phim, khong click chuot) va **khong** tu cai dat lai trinh duyet.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

LOGGER = logging.getLogger(__name__)

#: Ten cookie phi dang nhap Gemini can dung.
AUTH_COOKIE_NAMES: Tuple[str, ...] = ("__Secure-1PSID", "__Secure-1PSIDTS")

#: Ten cac tep cookie trong mot profile Chrome (ban moi dung ``Network/Cookies``).
_COOKIE_FILE_NAMES: Tuple[str, ...] = ("Network/Cookies", "Cookies")

#: Kieu cookie can lay (chi lay cookie cua Google).
_GOOGLE_COOKIE_DOMAINS: Tuple[str, ...] = (".google.com", "google.com")


# ---------------------------------------------------------------------------
# 1. Tim thu muc user-data cua trinh duyet
# ---------------------------------------------------------------------------


def chrome_user_data_dir() -> Optional[Path]:
    """Tra ve thu muc ``User Data`` cua Chrome (uu tien) hoac Edge.

    Bien moi truong ``GEMINI_CHROME_DIR`` ghi de tat ca (dung khi may dong
    bang hoac khi dung profile Playwright).
    """
    override = os.getenv("GEMINI_CHROME_DIR", "").strip()
    if override:
        candidate = Path(override)
        return candidate if candidate.is_dir() else None

    candidates: List[Path] = []
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        candidates.append(Path(local_app_data) / "Google" / "Chrome" / "User Data")
        candidates.append(Path(local_app_data) / "Microsoft" / "Edge" / "User Data")
    return next((path for path in candidates if path.is_dir()), None)


def browser_profile_dirs(user_data_dir: Optional[Path] = None) -> List[Path]:
    """Liet ke cac thu muc profile cua trinh duyet theo thu tu uu tien.

    ``Default`` luon dung truoc; cac profile ``Profile N`` theo thu tu so.
    """
    root = user_data_dir or chrome_user_data_dir()
    if root is None or not root.is_dir():
        return []

    profiles: List[Path] = []
    default = root / "Default"
    if default.is_dir():
        profiles.append(default)

    numbered: List[Tuple[int, Path]] = []
    for entry in root.iterdir():
        if not entry.is_dir() or not entry.name.startswith("Profile "):
            continue
        suffix = entry.name.split(" ", 1)[1]
        if suffix.isdigit():
            numbered.append((int(suffix), entry))
    profiles.extend(path for _, path in sorted(numbered, key=lambda item: item[0]))
    return profiles


# ---------------------------------------------------------------------------
# 2. Ghim tai khoan: profile -> email
# ---------------------------------------------------------------------------


def profile_accounts(
    user_data_dir: Optional[Path] = None,
) -> Dict[str, str]:
    """Tra ve ``{ten_profile: email_dang_dang_nhap}`` doc tu ``Local State``.

    ``Local State`` la file JSON o goc thu muc ``User Data``, chua
    ``profile.info_cache`` — moi profile mot ``user_name`` (email).
    """
    root = user_data_dir or chrome_user_data_dir()
    if root is None:
        return {}
    local_state = root / "Local State"
    try:
        with open(local_state, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    cache = (data or {}).get("profile", {}).get("info_cache", {}) or {}
    accounts: Dict[str, str] = {}
    for profile, meta in cache.items():
        if not isinstance(meta, dict):
            continue
        email = str(meta.get("user_name") or "").strip()
        if email:
            accounts[str(profile)] = email
    return accounts


def profile_account_emails(
    profile_dir: Path,
) -> List[str]:
    """Danh sach email da dang nhap trong mot profile (theo thu tu ``account_info``).

    Thu tu nay quyet dinh **chi so ``/u/N/``** cua tai khoan.
    """
    prefs = profile_dir / "Preferences"
    try:
        with open(prefs, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []

    emails: List[str] = []
    for entry in (data or {}).get("account_info", []) or []:
        if not isinstance(entry, dict):
            continue
        email = str(entry.get("email") or entry.get("gaia_name") or "").strip()
        if email and email not in emails:
            emails.append(email)
    return emails


def account_order(user_data_dir: Optional[Path] = None) -> List[str]:
    """Thu tu toan bo tai khoan Google da dang nhap (de-dup theo email).

    Cach Google quyet dinh chi so multi-login ``/u/N/``: duyet cac profile theo
    thu tu, trong moi profile duyet ``account_info`` theo thu tu, va **bo qua
    tai khoan da gap** (cung mot email chi co mot entry).
    """
    accounts = profile_accounts(user_data_dir)
    order: List[str] = []

    # Thu tu profile: thu muc thuc te (Default truoc, roi Profile N) — cung la
    # thu tu Chrome dung khi lap danh sach tai khoan. Profile nao chi co trong
    # `Local State` (khong con thu muc) duoc them vao cuoi.
    profile_names: List[str] = []
    for profile_dir in browser_profile_dirs(user_data_dir):
        if profile_dir.name not in profile_names:
            profile_names.append(profile_dir.name)
    for profile_name in accounts:
        if profile_name not in profile_names:
            profile_names.append(profile_name)

    root = user_data_dir or chrome_user_data_dir()
    for profile_name in profile_names:
        emails = []
        if root is not None:
            emails = profile_account_emails(root / profile_name)
        if not emails and profile_name in accounts:
            emails = [accounts[profile_name]]
        for email in emails:
            if email not in order:
                order.append(email)
    return order


def resolve_authuser_index(
    email: str,
    user_data_dir: Optional[Path] = None,
) -> str:
    """Suy ra chi so multi-login ``/u/N/`` cua mot tai khoan.

    Returns
    -------
    str
        Chuoi so (vi du ``"1"``). Neu khong tim thay tai khoan -> ``"0"``
        (tai khoan mac dinh cua trinh duyet).
    """
    target = str(email or "").strip().lower()
    if not target:
        return "0"
    for index, candidate in enumerate(account_order(user_data_dir)):
        if candidate.strip().lower() == target:
            return str(index)
    return "0"


def find_profile_for_email(
    email: str,
    user_data_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Tra ve thu muc profile dang dang nhap ``email`` (neu biet)."""
    target = str(email or "").strip().lower()
    if not target:
        return None
    accounts = profile_accounts(user_data_dir)
    for profile_name, account in accounts.items():
        if account.strip().lower() == target:
            root = user_data_dir or chrome_user_data_dir()
            return (root / profile_name) if root else None
    return None


def find_cookie_store(
    email: str = "",
    user_data_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Tim file ``Cookies`` cua profile da ghim (khong dung profile vua dung nhat)."""
    candidates: List[Path] = []
    pinned = find_profile_for_email(email, user_data_dir)
    if pinned is not None:
        candidates.append(pinned)
    else:
        candidates.extend(browser_profile_dirs(user_data_dir))

    for profile_dir in candidates:
        for name in _COOKIE_FILE_NAMES:
            store = profile_dir / name
            try:
                if store.is_file():
                    return store
            except OSError:
                continue
    return None


# ---------------------------------------------------------------------------
# 3. Thu thap cookie
# ---------------------------------------------------------------------------


def _cookies_from_browser_cookie3(store: Path) -> Dict[str, str]:
    """Doc cookie ``.google.com`` tu mot file ``Cookies`` (qua ``browser_cookie3``)."""
    try:
        import browser_cookie3  # type: ignore
    except ImportError:
        return {}

    try:
        jar = browser_cookie3.chrome(cookie_file=str(store), domain_name=".google.com")
    except Exception as exc:  # noqa: BLE001 - Chrome 130+ khoa app-bound encryption
        LOGGER.warning("Khong doc duoc cookie store %s: %s", store, exc)
        return {}

    found: Dict[str, str] = {}
    for cookie in jar:
        name = getattr(cookie, "name", "")
        if name in AUTH_COOKIE_NAMES:
            found[name] = getattr(cookie, "value", "") or ""
    return found


def capture_cookies_from_chrome(
    email: str = "",
    user_data_dir: Optional[Path] = None,
) -> Dict[str, str]:
    """Lay cookie phi dang nhap tu Chrome, **ghim theo email** de tranh nham tai khoan.

    Returns
    -------
    dict
        ``{"__Secure-1PSID": ..., "__Secure-1PSIDTS": ...}`` (co the rong).
    """
    store = find_cookie_store(email, user_data_dir)
    if store is None:
        return {}
    return _cookies_from_browser_cookie3(store)


def capture_cookies_via_cdp(cdp_url: str = "") -> Dict[str, str]:
    """Lay cookie jar cua Chrome qua DevTools Protocol (khong can GUI tuong tac).

    Yeu cau Chrome duoc khoi dong voi ``--remote-debugging-port`` (dat bien moi
    truong ``GEMINI_CDP_URL``, vi du ``http://127.0.0.1:9222``).
    """
    base = (cdp_url or os.getenv("GEMINI_CDP_URL", "")).strip().rstrip("/")
    if not base:
        return {}

    try:
        import httpx
        import websockets
    except ImportError:
        LOGGER.warning("Thieu httpx/websockets de doc cookie qua CDP.")
        return {}

    try:
        version = httpx.get(f"{base}/json/version", timeout=10).json()
        ws_url = version.get("webSocketDebuggerUrl")
        if not ws_url:
            return {}
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Khong ket noi duoc CDP %s: %s", base, exc)
        return {}

    async def _run() -> Dict[str, str]:
        found: Dict[str, str] = {}
        async with websockets.connect(ws_url, max_size=None) as socket:
            await socket.send(
                json.dumps({"id": 1, "method": "Network.getAllCookies", "params": {}})
            )
            while True:
                raw = await asyncio_wait(socket)
                message = json.loads(raw)
                if message.get("id") != 1:
                    continue
                for cookie in message.get("result", {}).get("cookies", []) or []:
                    name = cookie.get("name", "")
                    domain = str(cookie.get("domain", ""))
                    if name in AUTH_COOKIE_NAMES and any(
                        domain.endswith(domain_suffix) or domain == domain_suffix
                        for domain_suffix in _GOOGLE_COOKIE_DOMAINS
                    ):
                        found[name] = cookie.get("value", "") or ""
                break
        return found

    try:
        return asyncio_run(_run())
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("CDP getAllCookies that bai: %s", exc)
        return {}


def capture_cookies(
    email: str = "",
    cdp_url: str = "",
    user_data_dir: Optional[Path] = None,
) -> Dict[str, str]:
    """Chuoi uu tien thu thap cookie phi dang nhap Gemini.

    1. ``GEMINI_1PSID`` / ``GEMINI_1PSIDTS`` (bien moi truong).
    2. Chrome qua ``browser_cookie3`` (profile da ghim theo ``email``).
    3. Chrome DevTools Protocol (neu co ``GEMINI_CDP_URL``).
    """
    env_psid = os.getenv("GEMINI_1PSID", "").strip()
    if env_psid:
        return {
            "__Secure-1PSID": env_psid,
            "__Secure-1PSIDTS": os.getenv("GEMINI_1PSIDTS", "").strip(),
        }

    found = capture_cookies_from_chrome(email, user_data_dir)
    if found.get("__Secure-1PSID"):
        return found

    cdp = capture_cookies_via_cdp(cdp_url)
    if cdp.get("__Secure-1PSID"):
        return cdp
    return found


# ---------------------------------------------------------------------------
# 4. Tien ich
# ---------------------------------------------------------------------------


def asyncio_wait(socket: Any) -> Any:
    """Cho ``websockets`` nhan message (bo wrapper de de test)."""
    import asyncio

    return asyncio.wait_for(socket.recv(), timeout=15)


def asyncio_run(coro: Any) -> Any:
    """Chay coroutine an toan tu code dong bo (khong dung giay co san)."""
    import asyncio

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result()
    return loop.run_until_complete(coro)


def copy_cookie_store(store: Path) -> Optional[Path]:
    """Copy file ``Cookies`` sang thu muc tam (tranh khoa file dang dung)."""
    try:
        if not store.is_file():
            return None
        temp_dir = Path(tempfile.mkdtemp(prefix="gemini_cookies_"))
        target = temp_dir / "Cookies"
        shutil.copy2(store, target)
        return target
    except OSError:
        return None


def read_cookie_names(store: Path) -> List[str]:
    """Doc (khong giai ma) danh sach ten cookie trong file SQLite — phuc vu chan doan."""
    copied = copy_cookie_store(store)
    if copied is None:
        return []
    try:
        connection = sqlite3.connect(str(copied))
        try:
            rows = connection.execute(
                "SELECT name FROM cookies WHERE host_key LIKE '%google.com'"
            ).fetchall()
            return [str(row[0]) for row in rows]
        finally:
            connection.close()
    except sqlite3.Error:
        return []
    finally:
        try:
            shutil.rmtree(copied.parent, ignore_errors=True)
        except OSError:
            pass


__all__ = [
    "AUTH_COOKIE_NAMES",
    "chrome_user_data_dir",
    "browser_profile_dirs",
    "profile_accounts",
    "profile_account_emails",
    "account_order",
    "resolve_authuser_index",
    "find_profile_for_email",
    "find_cookie_store",
    "capture_cookies",
    "capture_cookies_from_chrome",
    "capture_cookies_via_cdp",
    "copy_cookie_store",
    "read_cookie_names",
]
