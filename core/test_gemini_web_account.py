"""Kiem thu don vi cho ``core.gemini_web_account`` (TASK_10 - Multi-Account Shield).

Chay:
    & "E:\\kiem duyet bao\\.venv\\Scripts\\python.exe" -m pytest core/test_gemini_web_account.py -v

Test tao fixture Chrome gia lap tren thu muc tam nen **khong cham vao** Chrome
that cua nguoi dung.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest import mock

from core import gemini_web_account as ga


def _make_chrome_fixture(
    root: Path,
    profiles: Dict[str, str],
    account_info: Dict[str, list],
    cookies: bool = True,
) -> Path:
    """Tao mot thu muc ``User Data`` gia lap co ``Local State`` + ``Preferences``."""
    user_data = root / "User Data"
    user_data.mkdir(parents=True, exist_ok=True)

    info_cache = {
        name: {"user_name": email, "gaia_name": email.split("@")[0]}
        for name, email in profiles.items()
    }
    (user_data / "Local State").write_text(
        json.dumps({"profile": {"info_cache": info_cache}}, indent=2),
        encoding="utf-8",
    )

    for name, emails in account_info.items():
        profile_dir = user_data / name
        (profile_dir / "Network").mkdir(parents=True, exist_ok=True)
        (profile_dir / "Preferences").write_text(
            json.dumps(
                {
                    "account_info": [
                        {"email": email, "gaia_name": email.split("@")[0]}
                        for email in emails
                    ]
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        if cookies:
            store = profile_dir / "Network" / "Cookies"
            connection = sqlite3.connect(str(store))
            try:
                connection.execute(
                    "CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT)"
                )
                connection.execute(
                    "INSERT INTO cookies VALUES (?, ?, ?)",
                    (".google.com", "__Secure-1PSID", f"psid-{name}"),
                )
                connection.commit()
            finally:
                connection.close()
    return user_data


class ProfileAccountsTests(unittest.TestCase):
    def test_reads_accounts_from_local_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            user_data = _make_chrome_fixture(
                root,
                {"Default": "congviec@gmail.com", "Profile 1": "canhan@gmail.com"},
                {"Default": ["congviec@gmail.com"], "Profile 1": ["canhan@gmail.com"]},
            )
            accounts = ga.profile_accounts(user_data)

        self.assertEqual(
            accounts, {"Default": "congviec@gmail.com", "Profile 1": "canhan@gmail.com"}
        )

    def test_returns_empty_when_local_state_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            self.assertEqual(ga.profile_accounts(Path(tmp_dir)), {})

    def test_browser_profile_dirs_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            user_data = _make_chrome_fixture(
                root,
                {"Default": "a@gmail.com", "Profile 2": "c@gmail.com", "Profile 1": "b@gmail.com"},
                {"Default": ["a@gmail.com"], "Profile 1": ["b@gmail.com"], "Profile 2": ["c@gmail.com"]},
            )
            names = [p.name for p in ga.browser_profile_dirs(user_data)]

        self.assertEqual(names, ["Default", "Profile 1", "Profile 2"])

    def test_profile_account_emails_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            user_data = _make_chrome_fixture(
                root,
                {"Default": "a@gmail.com"},
                {"Default": ["first@gmail.com", "second@gmail.com"]},
            )
            emails = ga.profile_account_emails(user_data / "Default")

        self.assertEqual(emails, ["first@gmail.com", "second@gmail.com"])


class AuthuserIndexTests(unittest.TestCase):
    """Chi so ``/u/N/`` — tranh tro nham tai khoan Gmail cong viec."""

    def _fixture(self, root: Path) -> Path:
        return _make_chrome_fixture(
            root,
            {"Default": "congviec@gmail.com", "Profile 1": "canhan@gmail.com"},
            {
                "Default": ["congviec@gmail.com", "canhan@gmail.com"],
                "Profile 1": ["canhan@gmail.com", "congviec@gmail.com"],
            },
        )

    def test_account_order_dedupes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            user_data = self._fixture(Path(tmp_dir))
            order = ga.account_order(user_data)

        self.assertEqual(order, ["congviec@gmail.com", "canhan@gmail.com"])

    def test_resolve_authuser_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            user_data = self._fixture(Path(tmp_dir))
            self.assertEqual(ga.resolve_authuser_index("congviec@gmail.com", user_data), "0")
            self.assertEqual(ga.resolve_authuser_index("canhan@gmail.com", user_data), "1")

    def test_resolve_is_case_insensitive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            user_data = self._fixture(Path(tmp_dir))
            self.assertEqual(ga.resolve_authuser_index("CANHAN@GMAIL.COM", user_data), "1")

    def test_unknown_email_falls_back_to_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            user_data = self._fixture(Path(tmp_dir))
            self.assertEqual(ga.resolve_authuser_index("khong@co.com", user_data), "0")

    def test_empty_email_falls_back_to_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            user_data = self._fixture(Path(tmp_dir))
            self.assertEqual(ga.resolve_authuser_index("", user_data), "0")


class CookieStoreTests(unittest.TestCase):
    def test_pins_the_profile_of_the_requested_email(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            user_data = _make_chrome_fixture(
                root,
                {"Default": "congviec@gmail.com", "Profile 1": "canhan@gmail.com"},
                {"Default": ["congviec@gmail.com"], "Profile 1": ["canhan@gmail.com"]},
            )
            profile = ga.find_profile_for_email("canhan@gmail.com", user_data)
            store = ga.find_cookie_store("canhan@gmail.com", user_data)

        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.name, "Profile 1")
        self.assertIsNotNone(store)
        assert store is not None
        self.assertIn("Profile 1", str(store))

    def test_read_cookie_names_from_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            user_data = _make_chrome_fixture(
                root,
                {"Default": "a@gmail.com"},
                {"Default": ["a@gmail.com"]},
            )
            store = ga.find_cookie_store("a@gmail.com", user_data)
            assert store is not None
            names = ga.read_cookie_names(store)

        self.assertIn("__Secure-1PSID", names)

    def test_copy_cookie_store_creates_temp_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            user_data = _make_chrome_fixture(
                root,
                {"Default": "a@gmail.com"},
                {"Default": ["a@gmail.com"]},
            )
            store = ga.find_cookie_store("a@gmail.com", user_data)
            assert store is not None
            copied = ga.copy_cookie_store(store)

        self.assertIsNotNone(copied)
        assert copied is not None
        self.assertTrue(copied.is_file())


class CaptureCookiesTests(unittest.TestCase):
    def test_environment_variables_take_priority(self) -> None:
        env = {"GEMINI_1PSID": "ENV_PSID", "GEMINI_1PSIDTS": "ENV_PSIDTS"}
        with mock.patch.dict("os.environ", env, clear=True):
            with mock.patch.object(ga, "capture_cookies_from_chrome") as chrome:
                cookies = ga.capture_cookies("canhan@gmail.com")

        chrome.assert_not_called()
        self.assertEqual(cookies["__Secure-1PSID"], "ENV_PSID")
        self.assertEqual(cookies["__Secure-1PSIDTS"], "ENV_PSIDTS")

    def test_falls_back_to_chrome(self) -> None:
        env = {}
        found = {"__Secure-1PSID": "CHROME_PSID", "__Secure-1PSIDTS": "CHROME_PSIDTS"}
        with mock.patch.dict("os.environ", env, clear=True):
            with mock.patch.object(
                ga, "capture_cookies_from_chrome", return_value=found
            ) as chrome:
                cookies = ga.capture_cookies("canhan@gmail.com")

        chrome.assert_called_once()
        self.assertEqual(cookies["__Secure-1PSID"], "CHROME_PSID")

    def test_falls_back_to_cdp_when_chrome_fails(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch.object(ga, "capture_cookies_from_chrome", return_value={}):
                with mock.patch.object(
                    ga, "capture_cookies_via_cdp", return_value={"__Secure-1PSID": "CDP_PSID"}
                ) as cdp:
                    cookies = ga.capture_cookies("canhan@gmail.com", cdp_url="http://127.0.0.1:9222")

        cdp.assert_called_once()
        self.assertEqual(cookies["__Secure-1PSID"], "CDP_PSID")

    def test_returns_empty_when_nothing_available(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch.object(ga, "capture_cookies_from_chrome", return_value={}):
                with mock.patch.object(ga, "capture_cookies_via_cdp", return_value={}):
                    cookies = ga.capture_cookies("canhan@gmail.com")

        self.assertEqual(cookies, {})

    def test_cdp_without_url_returns_empty(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(ga.capture_cookies_via_cdp(""), {})


class ChromeDirTests(unittest.TestCase):
    def test_env_override_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with mock.patch.dict("os.environ", {"GEMINI_CHROME_DIR": tmp_dir}, clear=True):
                self.assertEqual(ga.chrome_user_data_dir(), Path(tmp_dir))

    def test_env_override_invalid_returns_none(self) -> None:
        with mock.patch.dict(
            "os.environ", {"GEMINI_CHROME_DIR": r"E:\khong\ton\tai"}, clear=True
        ):
            self.assertIsNone(ga.chrome_user_data_dir())

    def test_detects_windows_default_location(self) -> None:
        import os as _os

        with mock.patch.dict(_os.environ, {"LOCALAPPDATA": r"C:\Users\test\AppData\Local"}, clear=True):
            with mock.patch.object(Path, "is_dir", return_value=False):
                self.assertIsNone(ga.chrome_user_data_dir())


if __name__ == "__main__":
    unittest.main()
