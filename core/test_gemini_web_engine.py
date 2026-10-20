"""Kiem thu don vi cho ``core.gemini_web_engine`` (TASK_10).

Chay:
    & "E:\\kiem duyet bao\\.venv\\Scripts\\python.exe" -m pytest core/test_gemini_web_engine.py -v

Toan bo test la **hermetic**: khong goi mang, khong mo trinh duyet, khong dung
GUI. ``GeminiClient`` duoc mock hoan toan.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

from core import gemini_web_engine as gwe
from core.gemini_web_engine import (
    GeminiWebEngine,
    apply_authuser,
    current_authuser,
    load_gemini_session,
    save_gemini_session,
)
from core.reviewer import normalize_review_result

# ---------------------------------------------------------------------------
# Du lieu mau
# ---------------------------------------------------------------------------

RAW_PAYLOAD: Dict[str, Any] = {
    "spelling_errors": [
        {
            "loi_sai": "đạo đực",
            "vi_tri": "đoạn 4",
            "goi_y_sua": "đạo đức",
            "ly_do": "Sai chính tả",
        }
    ],
    "fact_checks": [
        {
            "thong_tin_can_check": "Hội nghị diễn ra ngày 17/10",
            "ket_qua_xac_minh": "Đúng",
            "trich_dan_nguon": "https://example.com/nguon",
            "do_tin_cay": "cao",
        }
    ],
    "edited_text": "Bản đã biên tập.",
}


class FakeResponse:
    """Gia lap ``ModelOutput`` cua ``gemini_webapi``."""

    def __init__(self, text: str, citations: Optional[List[Dict[str, str]]] = None) -> None:
        self.text = text
        self.citations = citations or []


class FakeClient:
    """Gia lap ``GeminiClient`` (khong goi mang)."""

    def __init__(self, response_text: str = "", raise_on_generate: Optional[Exception] = None) -> None:
        self.response_text = response_text
        self.raise_on_generate = raise_on_generate
        self.prompts: List[str] = []
        self.models: List[Any] = []
        self.init_kwargs: Dict[str, Any] = {}
        self.closed = False

    async def init(self, **kwargs: Any) -> "FakeClient":
        self.init_kwargs = kwargs
        return self

    async def generate_content(self, prompt: str, **kwargs: Any) -> FakeResponse:
        self.prompts.append(prompt)
        self.models.append(kwargs.get("model"))
        if self.raise_on_generate is not None:
            raise self.raise_on_generate
        return FakeResponse(self.response_text)

    async def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# 1. Credentials & multi-account loading
# ---------------------------------------------------------------------------


class SessionConfigTests(unittest.TestCase):
    """``test_credentials_and_multi_account_loading``."""

    def test_credentials_and_multi_account_loading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "gemini_session.json"
            ok = save_gemini_session(
                "PSID_VALUE_123",
                "PSIDTS_VALUE_456",
                account_email="luuthanh.quochoi@gmail.com",
                authuser="1",
                config_path=config_path,
            )
            self.assertTrue(ok)

            config = load_gemini_session(config_path)

        self.assertEqual(config["secure_1psid"], "PSID_VALUE_123")
        self.assertEqual(config["secure_1psidts"], "PSIDTS_VALUE_456")
        self.assertEqual(config["account_email"], "luuthanh.quochoi@gmail.com")
        self.assertEqual(config["authuser"], "1")
        self.assertTrue(config["auto_granted"])
        self.assertTrue(config["updated_at"])

    def test_engine_reads_session_from_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "gemini_session.json"
            save_gemini_session("PSID", "PSIDTS", "a@b.com", "1", config_path=config_path)
            engine = GeminiWebEngine(config_path=config_path)
            self.assertTrue(engine.is_granted())
            self.assertEqual(engine.get_session_config()["authuser"], "1")

    def test_engine_not_granted_without_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            engine = GeminiWebEngine(config_path=Path(tmp_dir) / "missing.json")
            with mock.patch.dict("os.environ", {}, clear=True):
                self.assertFalse(engine.is_granted())

    def test_fallback_to_environment_variables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = {
                "GEMINI_1PSID": "ENV_PSID",
                "GEMINI_1PSIDTS": "ENV_PSIDTS",
                "GEMINI_ACCOUNT_EMAIL": "env@user.com",
                "GEMINI_AUTHUSER": "2",
            }
            with mock.patch.dict("os.environ", env, clear=True):
                config = load_gemini_session(Path(tmp_dir) / "missing.json")

        self.assertEqual(config["secure_1psid"], "ENV_PSID")
        self.assertEqual(config["secure_1psidts"], "ENV_PSIDTS")
        self.assertEqual(config["account_email"], "env@user.com")
        self.assertEqual(config["authuser"], "2")

    def test_save_rejects_empty_psid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "gemini_session.json"
            self.assertFalse(save_gemini_session("", "", "a@b.com", "0", config_path=path))
            self.assertFalse(path.exists())

    def test_save_creates_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "nested" / "deep" / "gemini_session.json"
            self.assertTrue(save_gemini_session("P", "", "a@b.com", "0", config_path=path))
            self.assertTrue(path.is_file())

    def test_load_handles_corrupted_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "gemini_session.json"
            path.write_text("{ khong phai JSON", encoding="utf-8")
            with mock.patch.dict("os.environ", {"GEMINI_1PSID": "ENV"}, clear=True):
                config = load_gemini_session(path)
        self.assertEqual(config["secure_1psid"], "ENV")


# ---------------------------------------------------------------------------
# 2. Endpoint shim /u/N/
# ---------------------------------------------------------------------------


class ApplyAuthuserTests(unittest.TestCase):
    """``test_apply_authuser_shim``."""

    def test_apply_authuser_shim(self) -> None:
        import gemini_webapi.client as client_module

        original = client_module.Endpoint
        try:
            apply_authuser("1")
            self.assertEqual(
                client_module.Endpoint.GENERATE,
                "https://gemini.google.com/u/1/_/BardChatUi/data/"
                "assistant.lamda.BardFrontendService/StreamGenerate",
            )
            self.assertEqual(client_module.Endpoint.INIT, "https://gemini.google.com/u/1/app")
            self.assertEqual(
                client_module.Endpoint.BATCH_EXEC,
                "https://gemini.google.com/u/1/_/BardChatUi/data/batchexecute",
            )
            self.assertEqual(current_authuser(), "1")
        finally:
            client_module.Endpoint = original
            apply_authuser("0")

    def test_apply_authuser_patches_all_endpoint_modules(self) -> None:
        import sys

        # Luu y: ``import gemini_webapi.utils.get_access_token as m`` se bind
        # *ham* (package __init__ re-export) — phai lay module object tu
        # ``sys.modules``.
        client_module = sys.modules["gemini_webapi.client"]
        gat_module = sys.modules["gemini_webapi.utils.get_access_token"]
        rot_module = sys.modules["gemini_webapi.utils.rotate_1psidts"]

        originals = [
            client_module.Endpoint,
            gat_module.Endpoint,
            rot_module.Endpoint,
        ]
        try:
            apply_authuser("3")
            for module in (client_module, gat_module, rot_module):
                self.assertIn("/u/3", module.Endpoint.GENERATE)
            self.assertEqual(current_authuser(), "3")
        finally:
            client_module.Endpoint, gat_module.Endpoint, rot_module.Endpoint = originals
            apply_authuser("0")

    def test_apply_authuser_zero_restores_default_endpoints(self) -> None:
        import gemini_webapi.client as client_module

        original = client_module.Endpoint
        try:
            apply_authuser("1")
            self.assertIn("/u/1", client_module.Endpoint.GENERATE)
            apply_authuser("0")
            self.assertNotIn("/u/0", client_module.Endpoint.GENERATE)
            self.assertEqual(client_module.Endpoint.INIT, "https://gemini.google.com/app")
            self.assertIsNone(current_authuser())
        finally:
            client_module.Endpoint = original

    def test_apply_authuser_none_is_noop(self) -> None:
        import gemini_webapi.client as client_module

        original = client_module.Endpoint
        try:
            apply_authuser(None)
            self.assertNotIn("/u/", client_module.Endpoint.GENERATE)
            self.assertIsNone(current_authuser())
        finally:
            client_module.Endpoint = original

    def test_apply_authuser_empty_string_is_noop(self) -> None:
        import gemini_webapi.client as client_module

        original = client_module.Endpoint
        try:
            apply_authuser("")
            self.assertNotIn("/u/", client_module.Endpoint.GENERATE)
        finally:
            client_module.Endpoint = original


# ---------------------------------------------------------------------------
# 3. Review bai bao (mock GeminiClient)
# ---------------------------------------------------------------------------


class ReviewArticleTests(unittest.TestCase):
    """``test_review_article_json_extraction_mocked``."""

    def _engine(self, tmp_dir: str, client: FakeClient) -> GeminiWebEngine:
        config_path = Path(tmp_dir) / "gemini_session.json"
        save_gemini_session("PSID", "PSIDTS", "a@b.com", "0", config_path=config_path)
        engine = GeminiWebEngine(config_path=config_path)
        engine._client = client  # ghep san, khong goi mang
        return engine

    def test_review_article_json_extraction_mocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            client = FakeClient(json.dumps(RAW_PAYLOAD, ensure_ascii=False))
            engine = self._engine(tmp_dir, client)
            result = engine.review_article_sync("Bài báo có lỗi đạo đực.")

        # Ket qua phai dung cau truc 3 phan cua he thong.
        self.assertIn("spelling_errors", result)
        self.assertIn("fact_checks", result)
        self.assertIn("edited_text", result)
        self.assertEqual(len(result["spelling_errors"]), 1)
        self.assertEqual(result["spelling_errors"][0]["loi_sai"], "đạo đực")
        self.assertEqual(len(result["fact_checks"]), 1)
        self.assertEqual(result["fact_checks"][0]["trich_dan_nguon"], "https://example.com/nguon")
        self.assertEqual(result["edited_text"], "Bản đã biên tập.")
        self.assertEqual(result["model"], gwe.GEMINI_WEB_MODEL_NAME)

    def test_review_result_matches_pipeline_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            client = FakeClient(json.dumps(RAW_PAYLOAD, ensure_ascii=False))
            engine = self._engine(tmp_dir, client)
            result = engine.review_article_sync("Bài báo.")
            expected = normalize_review_result(RAW_PAYLOAD, model=gwe.GEMINI_WEB_MODEL_NAME)

        self.assertEqual(result["spelling_errors"], expected["spelling_errors"])
        self.assertEqual(result["fact_checks"], expected["fact_checks"])
        self.assertEqual(result["edited_text"], expected["edited_text"])

    def test_prompt_contains_article_and_json_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            client = FakeClient(json.dumps(RAW_PAYLOAD, ensure_ascii=False))
            engine = self._engine(tmp_dir, client)
            engine.review_article_sync("Bài báo có lỗi đạo đực.")

            self.assertEqual(len(client.prompts), 1)
            prompt = client.prompts[0]
        self.assertIn("Bài báo có lỗi đạo đực.", prompt)
        self.assertIn("spelling_errors", prompt)
        self.assertIn("fact_checks", prompt)
        self.assertNotIn("edited_text", prompt)

    def test_citations_are_collected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            client = FakeClient(
                json.dumps(RAW_PAYLOAD, ensure_ascii=False),
            )
            client_response = FakeResponse(
                json.dumps(RAW_PAYLOAD, ensure_ascii=False),
                citations=[{"uri": "https://vnexpress.net", "title": "VnExpress"}],
            )

            class _CitedClient(FakeClient):
                async def generate_content(self, prompt: str, **kwargs: Any) -> FakeResponse:
                    self.prompts.append(prompt)
                    return client_response

            engine = self._engine(tmp_dir, _CitedClient())
            result = engine.review_article_sync("Bài báo.")

        self.assertEqual(len(result["grounding_sources"]), 1)
        self.assertEqual(result["grounding_sources"][0]["uri"], "https://vnexpress.net")

    def test_retry_reinitialises_client_on_auth_error(self) -> None:
        from gemini_webapi.exceptions import AuthError

        with tempfile.TemporaryDirectory() as tmp_dir:
            failing = FakeClient(raise_on_generate=AuthError("het hieu luc"))
            engine = self._engine(tmp_dir, failing)
            good = FakeClient(json.dumps(RAW_PAYLOAD, ensure_ascii=False))

            async def _fake_init(force_refresh: bool = False) -> Any:
                if force_refresh:
                    engine._client = good
                return engine._client

            with mock.patch.object(engine, "init_client", side_effect=_fake_init):
                result = engine.review_article_sync("Bài báo.")

        self.assertEqual(result["edited_text"], "Bản đã biên tập.")

    def test_empty_article_raises_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            engine = self._engine(tmp_dir, FakeClient("{}"))
            with self.assertRaises(ValueError):
                engine.review_article_sync("   ")

    def test_oversize_article_raises_value_error(self) -> None:
        from core.reviewer import MAX_ARTICLE_CHARS

        with tempfile.TemporaryDirectory() as tmp_dir:
            engine = self._engine(tmp_dir, FakeClient("{}"))
            with self.assertRaises(ValueError):
                engine.review_article_sync("x" * (MAX_ARTICLE_CHARS + 1))


# ---------------------------------------------------------------------------
# 4. AuthError khi thieu cookie
# ---------------------------------------------------------------------------


class MissingCookieTests(unittest.TestCase):
    """``test_missing_cookie_raises_auth_error``."""

    def test_missing_cookie_raises_auth_error(self) -> None:
        from gemini_webapi.exceptions import AuthError

        with tempfile.TemporaryDirectory() as tmp_dir:
            engine = GeminiWebEngine(config_path=Path(tmp_dir) / "missing.json")
            with mock.patch.dict("os.environ", {}, clear=True):
                with self.assertRaises(AuthError) as ctx:
                    engine._run_async(engine.init_client())

        self.assertIn("Cookie", str(ctx.exception))

    def test_test_connection_reports_missing_cookie(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            engine = GeminiWebEngine(config_path=Path(tmp_dir) / "missing.json")
            with mock.patch.dict("os.environ", {}, clear=True):
                ok, message = engine.test_connection_sync()

        self.assertFalse(ok)
        self.assertIn("xác thực Cookie", message)


# ---------------------------------------------------------------------------
# 5. Khoi tao client (auto_refresh=False — chong vang session Gmail)
# ---------------------------------------------------------------------------


class InitClientTests(unittest.TestCase):
    def test_init_disables_auto_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "gemini_session.json"
            save_gemini_session("PSID", "PSIDTS", "a@b.com", "1", config_path=config_path)
            client = FakeClient()
            engine = GeminiWebEngine(config_path=config_path)

            with mock.patch.object(gwe, "GeminiClient", return_value=client):
                await_engine = engine._run_async(engine.init_client())

        self.assertIs(await_engine, client)
        self.assertFalse(client.init_kwargs.get("auto_refresh", True))
        self.assertEqual(client.init_kwargs.get("timeout"), 120)

    def test_init_applies_authuser_index(self) -> None:
        import gemini_webapi.client as client_module

        original = client_module.Endpoint
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "gemini_session.json"
            save_gemini_session("PSID", "PSIDTS", "a@b.com", "2", config_path=config_path)
            client = FakeClient()
            engine = GeminiWebEngine(config_path=config_path)

            with mock.patch.object(gwe, "GeminiClient", return_value=client):
                engine._run_async(engine.init_client())

            self.assertIn("/u/2", client_module.Endpoint.GENERATE)
            client_module.Endpoint = original


if __name__ == "__main__":
    unittest.main()
