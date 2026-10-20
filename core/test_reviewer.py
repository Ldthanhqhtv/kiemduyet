"""
Unit test cho core/reviewer.py
==============================

Chay: & ".venv\\Scripts\\python.exe" -m unittest core.test_reviewer -v

Cac test nay KHONG goi API that: chung dung mot FakeClient de mo phong phan hoi
cua Gemini, nho do kiem tra duoc toan bo logic prompt/parse/chuan hoa.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List
from unittest import mock

from core import reviewer
from core import skills_manager
from core.rate_limiter import MODEL_LIMITS
from core.reviewer import (
    AIReviewer,
    EDITOR_SYSTEM_INSTRUCTION,
    build_safety_settings,
    build_tools,
    extract_json_from_text,
    normalize_review_result,
    read_article_file,
)
from core.skills_manager import save_skill

from google.genai import types


# ---------------------------------------------------------------------------
# Fake response / fake client
# ---------------------------------------------------------------------------


def make_fake_response(text: str, *, with_grounding: bool = True) -> Any:
    """Tao doi tuong phan hoi gia lap giong cau truc cua google-genai."""
    chunks: List[Any] = []
    queries: List[str] = []
    if with_grounding:
        chunks = [
            SimpleNamespace(
                web=SimpleNamespace(
                    uri="https://vnexpress.net/example-1",
                    title="VnExpress - Kiem chung",
                    domain="vnexpress.net",
                )
            ),
            SimpleNamespace(
                web=SimpleNamespace(
                    uri="https://tuoitre.vn/example-2",
                    title="Tuoi Tre - Su kien",
                    domain="tuoitre.vn",
                )
            ),
            SimpleNamespace(
                web=SimpleNamespace(
                    uri="https://vnexpress.net/example-1",  # trung lap -> phai bi loai
                    title="VnExpress - Kiem chung",
                    domain="vnexpress.net",
                )
            ),
        ]
        queries = ["Su kien X nam nao", "Nhan vat Y la ai"]

    grounding = SimpleNamespace(grounding_chunks=chunks, web_search_queries=queries)
    candidate = SimpleNamespace(grounding_metadata=grounding, finish_reason="STOP")
    usage = SimpleNamespace(
        prompt_token_count=123,
        candidates_token_count=456,
        total_token_count=579,
    )
    return SimpleNamespace(text=text, candidates=[candidate], usage_metadata=usage)


class FakeModels:
    """Gia lap client.models, ghi lai config de kiem tra."""

    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: List[dict] = []

    def generate_content(self, *, model: str, contents: str, config: Any) -> Any:
        self.calls.append({"model": model, "contents": contents, "config": config})
        return self.response


class FakeClient:
    def __init__(self, response: Any) -> None:
        self.models = FakeModels(response)


VALID_JSON = json.dumps(
    {
        "spelling_errors": [
            {
                "loi_sai": "xảy ra vào ngày 30/2",
                "vi_tri": "câu thứ 2",
                "goi_y_sua": "xảy ra vào ngày 28/2",
                "ly_do": "ngày 30/2 không tồn tại",
            }
        ],
        "fact_checks": [
            {
                "thong_tin_can_check": "Sự kiện X diễn ra năm 2024",
                "ket_qua_xac_minh": "ĐÚNG",
                "trich_dan_nguon": "https://vnexpress.net/example-1",
                "do_tin_cay": "Cao",
            }
        ],
        "edited_text": "Toàn văn bài báo đã được sửa sạch sẽ.",
    },
    ensure_ascii=False,
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSafetyAndTools(unittest.TestCase):
    def test_safety_settings_are_block_only_high(self) -> None:
        settings = build_safety_settings()
        self.assertEqual(len(settings), 4)
        for setting in settings:
            self.assertEqual(setting.threshold, types.HarmBlockThreshold.BLOCK_ONLY_HIGH)

        categories = {setting.category for setting in settings}
        expected = {
            types.HarmCategory.HARM_CATEGORY_HARASSMENT,
            types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
            types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
            types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        }
        self.assertEqual(categories, expected)

    def test_tools_enable_google_search(self) -> None:
        tools = build_tools()
        self.assertEqual(len(tools), 1)
        self.assertIsNotNone(tools[0].google_search)


class TestJsonExtraction(unittest.TestCase):
    def test_plain_json(self) -> None:
        parsed = extract_json_from_text('{"a": 1}')
        self.assertEqual(parsed, {"a": 1})

    def test_json_in_code_fence(self) -> None:
        text = 'Duoi day la ket qua:\n```json\n{"a": 1, "b": [2, 3]}\n```\nCam on.'
        self.assertEqual(extract_json_from_text(text), {"a": 1, "b": [2, 3]})

    def test_json_with_leading_prose(self) -> None:
        text = 'Ket qua kiem duyet nhu sau: {"spelling_errors": [], "fact_checks": [], "edited_text": "ok"}'
        parsed = extract_json_from_text(text)
        self.assertEqual(parsed["edited_text"], "ok")

    def test_json_with_trailing_comma(self) -> None:
        parsed = extract_json_from_text('{"a": 1, "b": 2,}')
        self.assertEqual(parsed, {"a": 1, "b": 2})

    def test_nested_braces_in_string(self) -> None:
        parsed = extract_json_from_text('{"text": "gia tri {long nhau}", "n": 1}')
        self.assertEqual(parsed["text"], "gia tri {long nhau}")

    def test_empty_raises(self) -> None:
        with self.assertRaises(ValueError):
            extract_json_from_text("")


class TestNormalize(unittest.TestCase):
    def test_missing_keys_get_defaults(self) -> None:
        result = normalize_review_result({})
        self.assertEqual(result["spelling_errors"], [])
        self.assertEqual(result["fact_checks"], [])
        self.assertEqual(result["edited_text"], "")

    def test_list_entry_mapped_by_position(self) -> None:
        result = normalize_review_result(
            {"spelling_errors": [["sai", "cau 1", "sua", "ly do"]]}
        )
        entry = result["spelling_errors"][0]
        self.assertEqual(entry["loi_sai"], "sai")
        self.assertEqual(entry["goi_y_sua"], "sua")
        self.assertEqual(entry["ly_do"], "ly do")

    def test_dict_entry_all_fields_present(self) -> None:
        result = normalize_review_result(
            {"fact_checks": [{"thong_tin_can_check": "A"}]}
        )
        entry = result["fact_checks"][0]
        for field in ("thong_tin_can_check", "ket_qua_xac_minh", "trich_dan_nguon", "do_tin_cay"):
            self.assertIn(field, entry)


class TestAIReviewerWithFakeClient(unittest.TestCase):
    def test_full_review_flow(self) -> None:
        response = make_fake_response(VALID_JSON)
        client = FakeClient(response)
        reviewer_obj = AIReviewer(client=client, model="gemini-2.5-flash")

        result = reviewer_obj.review("Noi dung bai bao tho can kiem duyet.")

        # 1) Ket qua chuan hoa dung 3 phan.
        self.assertEqual(len(result["spelling_errors"]), 1)
        self.assertEqual(result["spelling_errors"][0]["loi_sai"], "xảy ra vào ngày 30/2")
        self.assertEqual(len(result["fact_checks"]), 1)
        self.assertIn("sửa sạch", result["edited_text"])

        # 2) Grounding sources duoc thu thap va loai trung lap.
        uris = [source["uri"] for source in result["grounding_sources"]]
        self.assertEqual(
            uris,
            ["https://vnexpress.net/example-1", "https://tuoitre.vn/example-2"],
        )
        self.assertEqual(len(result["search_queries"]), 2)

        # 3) Config gui len API co google_search + safety BLOCK_ONLY_HIGH.
        config = client.models.calls[0]["config"]
        self.assertIsNotNone(config.tools[0].google_search)
        self.assertEqual(len(config.safety_settings), 4)
        self.assertEqual(
            config.safety_settings[0].threshold,
            types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
        )
        self.assertEqual(config.response_mime_type, "application/json")

        # 4) Usage duoc chuyen tiep.
        self.assertEqual(result["usage"]["total_token_count"], 579)

    def test_review_returns_error_dict_when_not_raising(self) -> None:
        response = make_fake_response("day khong phai JSON")
        client = FakeClient(response)
        reviewer_obj = AIReviewer(client=client)

        result = reviewer_obj.review("Noi dung.", raise_on_error=False)
        self.assertIn("error", result)
        self.assertEqual(result["spelling_errors"], [])

    def test_empty_article_raises(self) -> None:
        client = FakeClient(make_fake_response(VALID_JSON))
        reviewer_obj = AIReviewer(client=client)
        with self.assertRaises(ValueError):
            reviewer_obj.review("   ")

    def test_retry_on_json_mode_conflict(self) -> None:
        """Lan dau loi do xung dot tool/json, lan sau thanh cong khong json mode."""

        class FlakyModels:
            def __init__(self) -> None:
                self.calls = 0
                self.configs = []

            def generate_content(self, *, model: str, contents: str, config: Any) -> Any:
                self.calls += 1
                self.configs.append(config)
                if self.calls == 1:
                    raise RuntimeError(
                        "response_mime_type is not supported with tool google_search"
                    )
                return make_fake_response(VALID_JSON)

        flaky = FlakyModels()
        client = SimpleNamespace(models=flaky)
        reviewer_obj = AIReviewer(client=client)

        result = reviewer_obj.review("Noi dung bai bao.")
        self.assertEqual(flaky.calls, 2)
        self.assertEqual(flaky.configs[0].response_mime_type, "application/json")
        self.assertIsNone(flaky.configs[1].response_mime_type)
        self.assertEqual(len(result["spelling_errors"]), 1)


class TestReadArticleFile(unittest.TestCase):
    def test_read_txt(self) -> None:
        tmp = Path(__file__).parent / "_tmp_article.txt"
        try:
            tmp.write_text("Bai bao thu nghiem.", encoding="utf-8")
            self.assertEqual(read_article_file(tmp), "Bai bao thu nghiem.")
        finally:
            tmp.unlink(missing_ok=True)

    def test_missing_file_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            read_article_file(Path(__file__).parent / "_khong_ton_tai_.txt")


class TestBuildSystemInstruction(unittest.TestCase):
    """Prompt Merging: custom instruction + rang buoc JSON (Part 2)."""

    def setUp(self) -> None:
        self.reviewer_obj = AIReviewer(client=FakeClient(make_fake_response(VALID_JSON)))

    def test_default_without_custom_instruction(self) -> None:
        """Khong co custom instruction → giu nguyen EDITOR_SYSTEM_INSTRUCTION."""
        self.assertEqual(
            self.reviewer_obj.build_system_instruction(),
            EDITOR_SYSTEM_INSTRUCTION,
        )
        self.assertEqual(
            self.reviewer_obj.build_system_instruction("   "),
            EDITOR_SYSTEM_INSTRUCTION,
        )

    def test_custom_instruction_on_top_json_constraint_below(self) -> None:
        """Custom instruction len tren, rang buoc JSON (3 khoa) phia duoi."""
        custom = "Bạn là biên tập viên mảng giáo dục, chỉ soát lỗi chính tả."
        merged = self.reviewer_obj.build_system_instruction(custom)

        self.assertTrue(merged.startswith(custom))
        # Rang buoc JSON phia duoi voi 3 khoa co dinh.
        self.assertIn("spelling_errors", merged)
        self.assertIn("fact_checks", merged)
        self.assertIn("edited_text", merged)
        # Van bat buoc dung Google Search.
        self.assertIn("Google Search", merged)
        # Khong duoc ra chuoi rong.
        self.assertNotEqual(merged.strip(), "")
        # Custom instruction thay the mac dinh, khong concatenate.
        self.assertNotIn("TỔNG BIÊN TẬP kiêm chuyên gia FACT-CHECK", merged)

    def test_merged_instruction_keeps_skill_text_verbatim(self) -> None:
        skill_text = skills_manager.DEFAULT_SKILL_SEED["instructions"]
        merged = self.reviewer_obj.build_system_instruction(skill_text)
        self.assertTrue(merged.startswith(skill_text))
        self.assertIn("SOÁT LỖI CHÍNH TẢ & NGỮ PHÁP TIẾNG VIỆT", merged)


class TestReviewWithCustomSkill(unittest.TestCase):
    """AIReviewer.review ho tro custom_instruction / skill_id (Part 2)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.skills_file = Path(self._tmp.name) / "skills.json"

    def _make_reviewer(self, response: Any = None) -> AIReviewer:
        return AIReviewer(
            client=FakeClient(response or make_fake_response(VALID_JSON))
        )

    def test_review_passes_custom_instruction_to_config(self) -> None:
        client = FakeClient(make_fake_response(VALID_JSON))
        reviewer_obj = AIReviewer(client=client)

        result = reviewer_obj.review(
            "Bai bao thu nghiem.",
            custom_instruction="Chuyên gia kiểm chứng mảng y tế.",
        )

        config = client.models.calls[0]["config"]
        system_instruction = str(config.system_instruction)
        self.assertTrue(system_instruction.startswith("Chuyên gia kiểm chứng mảng y tế."))
        self.assertIn("spelling_errors", system_instruction)
        self.assertEqual(len(result["spelling_errors"]), 1)

    def test_review_with_skill_id_loads_instructions(self) -> None:
        """skill_id → lay instructions tu skills_manager (patch SKILLS_FILE tam)."""
        skills_manager.save_skill(
            {
                "id": "giao-duc",
                "name": "Giao duc",
                "instructions": "Bien tap vien chuyen muc giao duc toi thieu.",
            },
            self.skills_file,
        )
        client = FakeClient(make_fake_response(VALID_JSON))
        reviewer_obj = AIReviewer(client=client)

        with mock.patch.object(skills_manager, "SKILLS_FILE", self.skills_file):
            reviewer_obj.review("Bai bao ve truong hoc.", skill_id="giao-duc")

        config = client.models.calls[0]["config"]
        system_instruction = str(config.system_instruction)
        self.assertTrue(
            system_instruction.startswith("Bien tap vien chuyen muc giao duc toi thieu.")
        )

    def test_review_with_unknown_skill_id_falls_back_to_default(self) -> None:
        """Skill khong ton tai → dung EDITOR_SYSTEM_INSTRUCTION, khong raise."""
        with mock.patch.object(skills_manager, "SKILLS_FILE", self.skills_file):
            client = FakeClient(make_fake_response(VALID_JSON))
            reviewer_obj = AIReviewer(client=client)
            result = reviewer_obj.review("Noi dung.", skill_id="khong-ton-tai")

        config = client.models.calls[0]["config"]
        self.assertEqual(str(config.system_instruction), EDITOR_SYSTEM_INSTRUCTION)
        self.assertEqual(len(result["spelling_errors"]), 1)

    def test_custom_instruction_takes_precedence_over_skill_id(self) -> None:
        skills_manager.save_skill(
            {
                "id": "giao-duc",
                "name": "Giao duc",
                "instructions": "Chi dan cua ky nang giao duc.",
            },
            self.skills_file,
        )
        client = FakeClient(make_fake_response(VALID_JSON))
        reviewer_obj = AIReviewer(client=client)

        with mock.patch.object(skills_manager, "SKILLS_FILE", self.skills_file):
            reviewer_obj.review(
                "Noi dung.",
                custom_instruction="Chi dan tuy chinh truc tiep.",
                skill_id="giao-duc",
            )

        system_instruction = str(client.models.calls[0]["config"].system_instruction)
        self.assertTrue(system_instruction.startswith("Chi dan tuy chinh truc tiep."))
        self.assertNotIn("Chi dan cua ky nang giao duc.", system_instruction)

    def test_default_review_still_uses_editor_instruction(self) -> None:
        """Backward compatible: khong truyen gi → EDITOR_SYSTEM_INSTRUCTION."""
        client = FakeClient(make_fake_response(VALID_JSON))
        reviewer_obj = AIReviewer(client=client)
        reviewer_obj.review("Bai bao thu nghiem.")
        config = client.models.calls[0]["config"]
        self.assertEqual(str(config.system_instruction), EDITOR_SYSTEM_INSTRUCTION)

    def test_review_article_helper_forwards_skill_kwargs(self) -> None:
        """review_article() tien loi truyen tiep custom_instruction."""
        client = FakeClient(make_fake_response(VALID_JSON))
        reviewer_obj = AIReviewer(client=client)
        result = reviewer_obj.review_file(
            self._write_tmp_article(),
            custom_instruction="Phong su dieu tra.",
        )
        system_instruction = str(client.models.calls[0]["config"].system_instruction)
        self.assertTrue(system_instruction.startswith("Phong su dieu tra."))
        self.assertIn("edited_text", result)

    def _write_tmp_article(self) -> Path:
        path = Path(self._tmp.name) / "bai.txt"
        path.write_text("Bai bao thu nghiem cho review_file.", encoding="utf-8")
        return path


class TestGeminiWebReviewerParser(unittest.TestCase):
    """Kiem tra bo boc tach phan hoi Gemini Web va su dong bo prompt."""

    def test_build_full_prompt_matches_standalone_prompt(self) -> None:
        """Kiem tra build_full_prompt dong bo 100% voi build_standalone_gemini_prompt."""
        from core.gemini_web_reviewer import GeminiWebReviewer, build_standalone_gemini_prompt
        reviewer_web = GeminiWebReviewer()
        prompt_full = reviewer_web.build_full_prompt("Noi dung bai bao", skill_id="duyet-bai-bao")
        prompt_standalone = build_standalone_gemini_prompt("Noi dung bai bao", skill_id="duyet-bai-bao")
        self.assertEqual(prompt_full, prompt_standalone)
        self.assertIn("/t3", prompt_full)
        self.assertIn("Hãy dùng Google Tìm kiếm để tra cứu", prompt_full)
        self.assertNotIn("2026", prompt_full.split("sau")[0])

    def test_parse_gemini_web_response_markdown(self) -> None:
        """Kiem tra parse thanh cong Markdown bang loi va fact check tu Skill T3."""
        from core.gemini_web_reviewer import parse_gemini_web_response
        sample_markdown = """
## KẾT QUẢ DUYỆT BÀI VÀ KIỂM CHỨNG

### 1. Khai Thác & Xác Minh Dữ Liệu Chức Danh
- **Nhân sự đương nhiệm**: Bí thư Thành ủy TP.HCM hiện nay là đồng chí Nguyễn Văn Nên.
- **Thời gian cập nhật**: Hiện tại.
- **Nguồn xác thực**: https://chinhphu.vn/thanh-uy-tphcm

### 2. Soát Lỗi Chính Tả & Ngữ Pháp
| Lỗi sai | Vị trí / Ngữ cảnh | Gợi ý sửa chuẩn |
| :--- | :--- | :--- |
| GDRP | Đoạn 3: GDRP tăng 10,2% | GRDP |
| uốc hội | Đoạn cuối: Chủ tịch uốc hội | Chủ tịch Quốc hội |

### 3. Kiểm Chứng Nhân Vật & Sự Kiện (Fact-Checking)
- **Nhân vật**: Ghi Trần Lưu Quang là Bí thư Thành ủy là SAI.
- **Cảnh báo**: Sai lệch chức danh lãnh đạo.

### 4. Kết Luận Biên Tập
**Cần biên tập lại / Đình bản** do sai lệch chức danh.
"""
        article = "GDRP và Chủ tịch uốc hội thảo luận."
        result = parse_gemini_web_response(sample_markdown, original_article=article)
        self.assertEqual(len(result["spelling_errors"]), 2)
        self.assertEqual(result["spelling_errors"][0]["loi_sai"], "GDRP")
        self.assertEqual(result["spelling_errors"][0]["goi_y_sua"], "GRDP")
        self.assertEqual(result["spelling_errors"][1]["loi_sai"], "uốc hội")
        self.assertEqual(result["spelling_errors"][1]["goi_y_sua"], "Chủ tịch Quốc hội")
        self.assertTrue(len(result["fact_checks"]) >= 2)
        self.assertIn("https://chinhphu.vn/thanh-uy-tphcm", [s["uri"] for s in result["grounding_sources"]])
        self.assertEqual(result["edited_text"], "GRDP và Chủ tịch Chủ tịch Quốc hội thảo luận.")

    def test_parse_gemini_web_response_json(self) -> None:
        """Kiem tra van ho tro parse JSON neu Gemini tra ve JSON."""
        from core.gemini_web_reviewer import parse_gemini_web_response
        sample_json = json.dumps({
            "spelling_errors": [{"loi_sai": "sai", "vi_tri": "1", "goi_y_sua": "dung", "ly_do": ""}],
            "fact_checks": [{"thong_tin_can_check": "su kien", "ket_qua_xac_minh": "DUNG", "trich_dan_nguon": "https://google.com", "do_tin_cay": "Cao"}],
            "edited_text": "Bai da sua",
        })
        result = parse_gemini_web_response(sample_json, original_article="Bai sai")
        self.assertEqual(len(result["spelling_errors"]), 1)
        self.assertEqual(result["edited_text"], "Bai da sua")


class TestKeepToolsAndRateLimitTASK06(unittest.TestCase):
    """TASK_06: giu Google Search khi retry/fallback + bo dem RPM/RPD."""

    def test_default_and_fallback_models(self) -> None:
        self.assertEqual(reviewer.DEFAULT_MODEL, "gemini-3.1-flash-lite")
        self.assertEqual(reviewer.FALLBACK_MODEL, "gemini-3.5-flash-lite")

    def test_prompt_directs_single_request_batching(self) -> None:
        # Spec [single_request_grounding]: gom toan bo fact-check vao 1 request.
        self.assertIn("MỘT LƯỢT TRA CỨU DUY NHẤT", EDITOR_SYSTEM_INSTRUCTION)

        client = FakeClient(make_fake_response(VALID_JSON))
        reviewer_obj = AIReviewer(client=client)
        prompt = reviewer_obj.build_prompt("Noi dung bai bao")
        self.assertIn("GỘP TOÀN BỘ KIỂM CHỨNG VÀO MỘT LƯỢT", prompt)

    def test_429_falls_back_but_keeps_google_search(self) -> None:
        """Spec [anti_hallucination_leader_titles]: 429 -> fallback model, VAN GIU tool."""

        class QuotaExhaustedModels:
            def __init__(self) -> None:
                self.calls = 0
                self.configs: List[Any] = []
                self.models: List[str] = []

            def generate_content(self, *, model: str, contents: str, config: Any) -> Any:
                self.calls += 1
                self.configs.append(config)
                self.models.append(model)
                if self.calls == 1:
                    raise Exception("429 ResourceExhausted: quota exceeded")
                return make_fake_response(VALID_JSON)

        fake = QuotaExhaustedModels()
        reviewer_obj = AIReviewer(client=SimpleNamespace(models=fake), retry_backoff=0.0)
        result = reviewer_obj.review("Noi dung bai bao.")

        self.assertEqual(fake.calls, 2)
        # Lan 2 phai la model du phong.
        self.assertEqual(fake.models[1], reviewer.FALLBACK_MODEL)
        self.assertEqual(len(result["spelling_errors"]), 1)
        # TUYET DOI: MOI loi goi (ke ca sau khi fallback) deu co Google Search.
        for index, config in enumerate(fake.configs):
            self.assertIsNotNone(
                config.tools[0].google_search,
                msg=f"Loi goi #{index + 1} bi mat Google Search tool!",
            )

    def test_tool_error_never_strips_google_search(self) -> None:
        """Phan hoi rong/loi tool -> van GIU Google Search, khong bao gio gỡ."""

        class EmptyResponseModels:
            def __init__(self) -> None:
                self.configs: List[Any] = []
                self.models: List[str] = []

            def generate_content(self, *, model: str, contents: str, config: Any) -> Any:
                self.configs.append(config)
                self.models.append(model)
                return make_fake_response("")  # text rong -> khong co JSON

        fake = EmptyResponseModels()
        reviewer_obj = AIReviewer(
            client=SimpleNamespace(models=fake), max_retries=1, retry_backoff=0.0
        )

        with self.assertRaises(RuntimeError) as ctx:
            reviewer_obj.review("Noi dung bai bao.")
        self.assertIn("Google Search", str(ctx.exception))

        # Moi loi goi deu van co Google Search (tu DEFAULT_MODEL -> FALLBACK_MODEL).
        self.assertGreaterEqual(len(fake.configs), 1)
        for config in fake.configs:
            self.assertIsNotNone(config.tools[0].google_search)
        self.assertEqual(fake.models[0], reviewer.DEFAULT_MODEL)
        self.assertEqual(fake.models[-1], reviewer.FALLBACK_MODEL)

    def test_rate_limiter_pre_switches_exhausted_model(self) -> None:
        """Ham dem RPM cham han muc -> chuyen model TRUOC loi goi dau tien."""
        from core.rate_limiter import RateLimiter

        clock_now = [1_000_000.0]
        limiter = RateLimiter(clock=lambda: clock_now[0])
        # Het 15 RPM cua gemini-3.1-flash-lite.
        for _ in range(MODEL_LIMITS["gemini-3.1-flash-lite"].rpm):
            limiter.record_call(reviewer.DEFAULT_MODEL)

        class GoodModels:
            def __init__(self) -> None:
                self.models: List[str] = []

            def generate_content(self, *, model: str, contents: str, config: Any) -> Any:
                self.models.append(model)
                return make_fake_response(VALID_JSON)

        fake = GoodModels()
        reviewer_obj = AIReviewer(
            client=SimpleNamespace(models=fake), rate_limiter=limiter
        )
        reviewer_obj.review("Noi dung bai bao.")

        # Loi goi dau tien da phai la model du phong (khong goi vao model het han).
        self.assertEqual(fake.models[0], "gemini-3.5-flash-lite")

    def test_rate_limiter_records_calls_during_review(self) -> None:
        from core.rate_limiter import RateLimiter

        limiter = RateLimiter(clock=lambda: 1_000_000.0)
        client = FakeClient(make_fake_response(VALID_JSON))
        reviewer_obj = AIReviewer(client=client, rate_limiter=limiter)

        reviewer_obj.review("Noi dung bai bao.")

        status = limiter.get_status(reviewer.DEFAULT_MODEL)
        self.assertEqual(status["rpm_used"], 1)
        self.assertEqual(status["rpd_used"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
