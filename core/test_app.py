"""Kiem thu don vi cho cac ham xu ly du lieu cua ``app.py`` (Task 3).

Chay:
    & "E:\\kiem duyet bao\\.venv\\Scripts\\python.exe" -m unittest core.test_app -v

Cac test nay khong can API key, khong goi mang — chi kiem tra phan "thuan"
(tach van ban tu file upload, dung bang du lieu, chon API key, dem so tu,
xem diff, quan ly ky nang) va giao dien Streamlit thong qua ``AppTest``.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest import mock

from docx import Document
from streamlit.testing.v1 import AppTest

from app import (
    AVAILABLE_MODELS,
    DEFAULT_MODEL,
    FACT_FIELDS,
    FACT_URL_COLUMN,
    SAMPLE_ARTICLE,
    SPELLING_FIELDS,
    STATE_ARTICLE_TEXT_AREA,
    STATE_MOMENT,
    STATE_ORIGINAL,
    STATE_RESULT,
    STATE_REVIEW_ID,
    STATE_SOURCE,
    STATE_USED_SKILL,
    build_diff_html,
    count_words_chars,
    entries_to_dataframe,
    extract_first_url,
    extract_uploaded_text,
    format_int_vi,
    format_word_char_caption,
    handle_review_click,
    merge_edited_text,
    resolve_api_key,
    run_review,
    skill_option_label,
)
from core.clipboard_utils import (
    build_clipboard_payload,
    build_cf_html_fragment,
    write_clipboard_windows,
)
from core import skills_manager

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


class FakeUploadedFile(io.BytesIO):
    """Gia lap doi tuong file cua Streamlit (co ``name`` + ``getvalue``)."""

    def __init__(self, data: bytes, name: str) -> None:
        super().__init__(data)
        self.name = name


def _make_docx_bytes(paragraphs, table_rows=None) -> bytes:
    """Tao file .docx trong bo nho de test phan doc file Word."""
    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    if table_rows:
        table = document.add_table(rows=len(table_rows), cols=len(table_rows[0]))
        for row_index, row in enumerate(table_rows):
            for col_index, value in enumerate(row):
                table.cell(row_index, col_index).text = value
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


class ExtractUploadedTextTests(unittest.TestCase):
    def test_docx_paragraphs_are_extracted(self):
        data = _make_docx_bytes(["Tieu de bai bao", "Noi dung doan 1", "Noi dung doan 2"])
        text = extract_uploaded_text(FakeUploadedFile(data, "bai.docx"))

        self.assertIn("Tieu de bai bao", text)
        self.assertIn("Noi dung doan 1", text)
        self.assertIn("Noi dung doan 2", text)
        # Cac doan phai duoc noi bang xuong dong.
        self.assertEqual(text.count("\n"), 2)

    def test_docx_table_content_is_extracted(self):
        data = _make_docx_bytes(
            ["Bai bao co bang so lieu"],
            table_rows=[["Chi tieu", "Gia tri"], ["Doanh thu", "100"]],
        )
        text = extract_uploaded_text(FakeUploadedFile(data, "bai.docx"))

        self.assertIn("Chi tieu | Gia tri", text)
        self.assertIn("Doanh thu | 100", text)

    def test_txt_file_is_read_as_utf8(self):
        raw = "Bài báo tiếng Việt có dấu".encode("utf-8")
        text = extract_uploaded_text(FakeUploadedFile(raw, "bai.txt"))

        self.assertEqual(text, "Bài báo tiếng Việt có dấu")

    def test_utf8_bom_is_stripped(self):
        raw = "﻿Noi dung".encode("utf-8")
        text = extract_uploaded_text(FakeUploadedFile(raw, "bai.md"))

        self.assertEqual(text, "Noi dung")

    def test_text_is_stripped(self):
        raw = "\n\n   Bai bao   \n\n".encode("utf-8")
        text = extract_uploaded_text(FakeUploadedFile(raw, "bai.txt"))

        self.assertEqual(text, "Bai bao")

    def test_plain_text_file_like_object_without_getvalue(self):
        class Reader:
            name = "bai.txt"

            def read(self) -> bytes:
                return b"Noi dung tu read()"

        self.assertEqual(extract_uploaded_text(Reader()), "Noi dung tu read()")


class EntriesToDataframeTests(unittest.TestCase):
    def test_columns_follow_declared_order(self):
        df = entries_to_dataframe(
            [{"ly_do": "L", "loi_sai": "A", "vi_tri": "1", "goi_y_sua": "B"}],
            SPELLING_FIELDS,
        )

        self.assertEqual(list(df.columns), [label for _, label in SPELLING_FIELDS])
        self.assertEqual(df.iloc[0]["Lỗi sai"], "A")
        self.assertEqual(df.iloc[0]["Lý do"], "L")

    def test_missing_or_none_fields_become_empty_strings(self):
        df = entries_to_dataframe([{"loi_sai": None}], SPELLING_FIELDS)

        self.assertEqual(df.iloc[0]["Lỗi sai"], "")
        self.assertEqual(df.iloc[0]["Gợi ý sửa"], "")

    def test_empty_entries_keep_all_columns(self):
        df = entries_to_dataframe([], FACT_FIELDS)

        self.assertEqual(len(df), 0)
        self.assertEqual(list(df.columns), [label for _, label in FACT_FIELDS])

    def test_non_dict_entry_falls_back_to_first_column(self):
        df = entries_to_dataframe(["thong tin la"], FACT_FIELDS)

        self.assertEqual(df.iloc[0]["Thông tin cần kiểm chứng"], "thong tin la")


class MergeEditedTextTests(unittest.TestCase):
    """Ban Word phai dung phan bien tap vien sua tay (khong duoc bo qua)."""

    def test_keeps_model_text_when_widget_not_changed(self):
        result = {"edited_text": "Ban cua model"}
        self.assertIs(merge_edited_text(result, "Ban cua model"), result)
        self.assertIs(merge_edited_text(result, "   Ban cua model  "), result)

    def test_uses_widget_text_when_changed(self):
        result = {"edited_text": "Ban cua model", "fact_checks": ["x"]}
        merged = merge_edited_text(result, "Ban da sua tay")

        self.assertEqual(merged["edited_text"], "Ban da sua tay")
        self.assertEqual(merged["fact_checks"], ["x"])
        # Khong duoc sua truc tiep dict goc.
        self.assertEqual(result["edited_text"], "Ban cua model")

    def test_empty_or_missing_widget_value_keeps_model_text(self):
        result = {"edited_text": "Ban cua model"}
        for value in ("", "   ", None):
            self.assertIs(merge_edited_text(result, value), result)


class ResolveApiKeyTests(unittest.TestCase):
    def test_sidebar_value_wins_over_environment(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "env-key"}, clear=False):
            self.assertEqual(resolve_api_key("  typed-key  "), "typed-key")

    def test_falls_back_to_gemini_env(self):
        with mock.patch.dict(
            os.environ, {"GEMINI_API_KEY": "env-key"}, clear=False
        ):
            with mock.patch.dict(os.environ, {"GOOGLE_API_KEY": ""}, clear=False):
                self.assertEqual(resolve_api_key(""), "env-key")

    def test_falls_back_to_google_env(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=False):
            with mock.patch.dict(os.environ, {"GOOGLE_API_KEY": "google-key"}, clear=False):
                self.assertEqual(resolve_api_key(None), "google-key")

    def test_returns_none_when_nothing_available(self):
        with mock.patch.dict(
            os.environ, {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}, clear=False
        ):
            self.assertIsNone(resolve_api_key("   "))


class CountWordsCharsTests(unittest.TestCase):
    """So tu / so ky tu dung tren giao dien (Task 3)."""

    def test_counts_words_and_chars(self):
        self.assertEqual(count_words_chars("Xin chao The Gioi"), (4, 17))

    def test_strips_surrounding_whitespace(self):
        # Chi dem phan trong sau khi strip dau/cuoi.
        self.assertEqual(count_words_chars("  Xin chao  "), (2, 8))

    def test_empty_returns_zero_pair(self):
        self.assertEqual(count_words_chars(""), (0, 0))
        self.assertEqual(count_words_chars("   \n  "), (0, 0))
        self.assertEqual(count_words_chars(None), (0, 0))

    def test_format_int_vi_thousands_separator(self):
        self.assertEqual(format_int_vi(0), "0")
        self.assertEqual(format_int_vi(999), "999")
        self.assertEqual(format_int_vi(1234567), "1.234.567")

    def test_format_word_char_caption(self):
        self.assertEqual(format_word_char_caption("Xin chao"), "2 từ (8 ký tự)")
        self.assertEqual(format_word_char_caption(""), "0 từ (0 ký tự)")


class ExtractFirstUrlTests(unittest.TestCase):
    """Lay URL dau tien tu trich dan nguon (dung cho LinkColumn)."""

    def test_extracts_first_url(self):
        text = "Theo https://vnexpress.net/a-b.html, tinh hinh dang tot."
        self.assertEqual(extract_first_url(text), "https://vnexpress.net/a-b.html")

    def test_ignores_trailing_punctuation(self):
        text = "nguon: https://example.com/tin-tuc, doi chieu them."
        self.assertEqual(extract_first_url(text), "https://example.com/tin-tuc")

    def test_returns_empty_when_no_url(self):
        self.assertEqual(extract_first_url("khong co url nao"), "")
        self.assertEqual(extract_first_url(None), "")


class BuildDiffHtmlTests(unittest.TestCase):
    """Xem diem khac biet word-level giua ban goc va ban bien tap (Task 3)."""

    def test_identical_text_has_no_marks(self):
        out = build_diff_html("mot hai ba", "mot hai ba")
        self.assertNotIn("<del", out)
        self.assertNotIn("<ins", out)
        self.assertIn("mot hai ba", out)

    def test_changed_word_produces_del_and_ins(self):
        out = build_diff_html("Hoi nghi tai Ha Noi", "Hoi nghi tai Sai Gon")
        self.assertIn('<del class="diff-del">Ha Noi</del>', out)
        self.assertIn('<ins class="diff-ins">Sai Gon</ins>', out)

    def test_added_word_produces_only_ins(self):
        out = build_diff_html("mot hai", "mot hai ba")
        self.assertIn('<ins class="diff-ins">ba</ins>', out)
        self.assertNotIn("<del", out)

    def test_removed_word_produces_only_del(self):
        out = build_diff_html("mot hai ba", "mot hai")
        self.assertIn('<del class="diff-del">ba</del>', out)
        self.assertNotIn("<ins", out)

    def test_html_in_content_is_escaped(self):
        out = build_diff_html("<script>alert(1)</script> x", "y")
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_both_empty_shows_placeholder(self):
        out = build_diff_html("", "")
        self.assertIn("Chưa có nội dung", out)


class SkillOptionLabelTests(unittest.TestCase):
    """Nhan hien thi tren o chon ky nang."""

    def test_default_skill_gets_star(self):
        label = skill_option_label(
            {"name": "A", "description": "", "is_default": True}
        )
        self.assertTrue(label.endswith("⭐"))

    def test_description_appended_when_present(self):
        label = skill_option_label(
            {"name": "B", "description": "Kinh te", "is_default": False}
        )
        self.assertIn("— Kinh te", label)
        self.assertNotIn("⭐", label)

    def test_long_description_truncated(self):
        label = skill_option_label({"name": "C", "description": "x" * 100})
        self.assertIn("...", label)


class RunReviewTests(unittest.TestCase):
    def test_run_review_passes_key_and_model_to_reviewer(self):
        fake_result = {
            "spelling_errors": [],
            "fact_checks": [],
            "edited_text": "da sua",
            "grounding_sources": [],
            "search_queries": [],
            "model": "gemini-test",
            "usage": {},
        }
        reviewer = mock.MagicMock()
        reviewer.review.return_value = fake_result

        with mock.patch("app.AIReviewer", return_value=reviewer) as reviewer_cls:
            result = run_review("Noi dung bai", "key-abc", "gemini-test")

        reviewer_cls.assert_called_once_with(api_key="key-abc", model="gemini-test")
        # Khong truyen skill → van truyen skill_id=None (mac dinh).
        reviewer.review.assert_called_once_with("Noi dung bai", skill_id=None)
        self.assertIs(result, fake_result)

    def test_run_review_passes_skill_id(self):
        reviewer = mock.MagicMock()
        reviewer.review.return_value = {}

        with mock.patch("app.AIReviewer", return_value=reviewer):
            run_review("Noi dung", "key", skill_id="duyet-bai-bao")

        reviewer.review.assert_called_once_with("Noi dung", skill_id="duyet-bai-bao")

    def test_default_model_constant(self):
        # Model mac dinh: gemini-3.8-flash theo yeu cau cua toa soan.
        self.assertEqual(DEFAULT_MODEL, "gemini-3.8-flash")

    def test_available_models_puts_lite_first(self):
        self.assertEqual(AVAILABLE_MODELS[0], "gemini-3.8-flash")
        self.assertIn("gemini-3.1-flash-lite", AVAILABLE_MODELS)

    def test_run_review_wraps_errors_in_runtime_error(self):
        # TASK_06: loi tu AIReviewer duoc boc thanh RuntimeError sach (khong crash server).
        with mock.patch("app.AIReviewer", side_effect=ValueError("api key sai")):
            with self.assertRaises(RuntimeError) as ctx:
                run_review("Noi dung", "key-abc", model="gemini-test")
        self.assertIn("Lỗi khi gọi bộ kiểm duyệt", str(ctx.exception))
        self.assertIn("api key sai", str(ctx.exception))

    def test_handle_review_click_swallows_unexpected_error(self):
        # TASK_06: keo cuoi — ngoai le bat ngo khong duoc raise ra khoi handle_review_click.
        with mock.patch(
            "app._handle_review_click_impl", side_effect=RuntimeError("boom")
        ):
            with mock.patch("app.st.error") as mocked_error:
                handle_review_click(
                    "Noi dung bai",
                    "Nguon",
                    {"engine_mode": "api", "api_key": "key-abc", "model": "m"},
                )
        mocked_error.assert_called_once()
        self.assertIn("boom", mocked_error.call_args[0][0])


SAMPLE_RESULT = {
    "spelling_errors": [
        {
            "loi_sai": "thăm quan",
            "vi_tri": "đoạn 2",
            "goi_y_sua": "tham quan",
            "ly_do": "Sai chính tả theo từ điển tiếng Việt",
        }
    ],
    "fact_checks": [
        {
            "thong_tin_can_check": "Hội nghị khai mạc ngày 17/10 tại Hà Nội",
            "ket_qua_xac_minh": "Đúng theo thông cáo của ban tổ chức",
            "trich_dan_nguon": "https://example.com/thong-cao",
            "do_tin_cay": "cao",
        }
    ],
    "edited_text": "Hội nghị khoa học quốc tế về AI khai mạc tại Hà Nội.",
    "grounding_sources": [
        {
            "title": "Thông cáo ban tổ chức",
            "uri": "https://example.com/thong-cao",
            "domain": "example.com",
        }
    ],
    "search_queries": ["hội nghị AI Hà Nội 17/10"],
    "model": "gemini-2.5-flash",
    "usage": {"total_token_count": 1234},
}


def _make_app() -> AppTest:
    """Tao AppTest cho app.py voi moi truong khong co API key."""
    with mock.patch.dict(
        os.environ, {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}, clear=False
    ):
        app = AppTest.from_file(str(APP_PATH), default_timeout=120)
        app.run()
    return app


class StreamlitAppTests(unittest.TestCase):
    """Chay that script ``app.py`` bang Streamlit AppTest (khong can API key)."""

    def test_app_runs_and_warns_without_api_key(self):
        app = _make_app()

        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        warnings = " ".join(item.value for item in app.sidebar.warning)
        self.assertIn("API key", warnings)
        labels = [b.label for b in app.button]
        # TASK_10: mac dinh la che do "Gemini Web Session" (RPC ngam, 1 NUT).
        self.assertTrue(
            any("Bắt Đầu Kiểm Duyệt Qua Gemini Web" in label for label in labels)
        )
        self.assertTrue(any("Bắt đầu bài mới" in label for label in labels))

    def test_sidebar_shows_rpm_rpd_quota_widget(self):
        # TASK_06: Sidebar phai hien han muc RPM/RPD cua model dang chon.
        app = _make_app()

        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        metric_labels = [metric.label for metric in app.sidebar.metric]
        self.assertIn("RPM (60 giây)", metric_labels)
        self.assertIn("RPD (ngày)", metric_labels)

    def test_two_main_tabs_render(self):
        app = _make_app()

        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        tab_labels = [tab.label for tab in app.tabs]
        self.assertTrue(any("Kiểm duyệt bài báo" in label for label in tab_labels))
        self.assertTrue(any("Quản lý Kỹ năng" in label for label in tab_labels))

    def test_skill_selector_defaults_to_default_skill(self):
        app = _make_app()

        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        chk = app.checkbox(key="skill_chk_duyet-bai-bao")
        self.assertTrue(chk.value)

    def test_demo_sample_button_fills_article_text_area(self):
        app = _make_app()
        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")

        app.button(key="demo_sample_button").click()
        with mock.patch.dict(
            os.environ, {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}, clear=False
        ):
            app.run()

        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        self.assertEqual(
            app.text_area(key=STATE_ARTICLE_TEXT_AREA).value, SAMPLE_ARTICLE
        )

    def test_results_and_download_buttons_render_from_session_state(self):
        app = AppTest.from_file(str(APP_PATH), default_timeout=120)
        app.session_state[STATE_RESULT] = SAMPLE_RESULT
        app.session_state[STATE_ORIGINAL] = "Toàn văn bài báo gốc để xuất Word."
        app.session_state[STATE_SOURCE] = "Văn bản dán trực tiếp"
        app.session_state[STATE_MOMENT] = None
        app.session_state[STATE_REVIEW_ID] = "lan-1"
        with mock.patch.dict(
            os.environ, {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}, clear=False
        ):
            app.run()

        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        self.assertTrue(any("Đã duyệt xong" in s.value for s in app.success))
        # Bang loi chinh ta + bang kiem chung phai duoc ve.
        self.assertGreaterEqual(len(app.dataframe), 2)
        # Nut tai bao cao .docx
        download_labels = [d.label for d in app.download_button]
        self.assertTrue(any(".docx" in label for label in download_labels))

    def test_fact_table_has_clickable_url_column(self):
        """Bang kiem chung phai co cot URL (LinkColumn) lay tu trich dan."""
        app = AppTest.from_file(str(APP_PATH), default_timeout=120)
        app.session_state[STATE_RESULT] = SAMPLE_RESULT
        app.session_state[STATE_ORIGINAL] = "goc"
        app.session_state[STATE_REVIEW_ID] = "lan-url"
        with mock.patch.dict(
            os.environ, {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}, clear=False
        ):
            app.run()

        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        url_frames = [
            df.value for df in app.dataframe if FACT_URL_COLUMN in list(df.value.columns)
        ]
        self.assertEqual(len(url_frames), 1)
        self.assertEqual(
            url_frames[0].iloc[0][FACT_URL_COLUMN], "https://example.com/thong-cao"
        )

    @unittest.skip("Da luoc bo theo yeu cau: chi liet ke loi, khong in lai bai moi va diff")
    def test_diff_renders_between_original_and_edited(self):
        """Tab xem diem phai hien HTML diff (co <del>/<ins>)."""
        app = AppTest.from_file(str(APP_PATH), default_timeout=120)
        app.session_state[STATE_RESULT] = SAMPLE_RESULT
        app.session_state[STATE_ORIGINAL] = "Bai goc khac hoan toan voi ban bien tap."
        app.session_state[STATE_REVIEW_ID] = "lan-diff"
        with mock.patch.dict(
            os.environ, {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}, clear=False
        ):
            app.run()

        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        markdown_values = " ".join(m.value for m in app.markdown)
        self.assertIn("diff-del", markdown_values)
        self.assertIn("diff-ins", markdown_values)

    def test_word_char_count_metric_shows(self):
        app = AppTest.from_file(str(APP_PATH), default_timeout=120)
        app.session_state[STATE_RESULT] = SAMPLE_RESULT
        app.session_state[STATE_ORIGINAL] = "Một hai ba bốn năm."
        app.session_state[STATE_REVIEW_ID] = "lan-metric"
        with mock.patch.dict(
            os.environ, {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}, clear=False
        ):
            app.run()

        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        metrics = {m.label: m.value for m in app.metric}
        self.assertIn("Bài gốc", metrics)
        self.assertIn("5 từ", metrics["Bài gốc"])

    def test_used_skill_is_shown_in_results(self):
        """Ket qua hien thi ten ky nang da dung cho lan duyet."""
        app = AppTest.from_file(str(APP_PATH), default_timeout=120)
        app.session_state[STATE_RESULT] = SAMPLE_RESULT
        app.session_state[STATE_ORIGINAL] = "goc"
        app.session_state[STATE_REVIEW_ID] = "lan-skill"
        app.session_state[STATE_USED_SKILL] = "duyet-bai-bao"
        with mock.patch.dict(
            os.environ, {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}, clear=False
        ):
            app.run()

        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        captions = " ".join(c.value for c in app.caption)
        self.assertIn("Kỹ năng áp dụng", captions)
        self.assertIn("duyet-bai-bao", captions)

    @unittest.skip("Da luoc bo theo yeu cau: chi liet ke loi, khong in lai bai moi")
    def test_new_review_replaces_edited_text_of_previous_article(self):
        """Hoi quy: o van ban da bien tap khong duoc giu noi dung bai cu."""
        first = dict(SAMPLE_RESULT, edited_text="NOI DUNG BAI A")
        second = dict(SAMPLE_RESULT, edited_text="NOI DUNG BAI B")

        app = AppTest.from_file(str(APP_PATH), default_timeout=120)
        app.session_state[STATE_RESULT] = first
        app.session_state[STATE_ORIGINAL] = "goc A"
        app.session_state[STATE_REVIEW_ID] = "lan-a"
        with mock.patch.dict(
            os.environ, {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}, clear=False
        ):
            app.run()
        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        edited = [
            area
            for area in app.text_area
            if (area.key or "").startswith("edited_text_area")
        ]
        self.assertEqual(edited[0].value, "NOI DUNG BAI A")

        # Bai thu hai: ket qua va ma lan duyet deu doi -> o nhap phai la noi dung moi.
        app.session_state[STATE_RESULT] = second
        app.session_state[STATE_ORIGINAL] = "goc B"
        app.session_state[STATE_REVIEW_ID] = "lan-b"
        with mock.patch.dict(
            os.environ, {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}, clear=False
        ):
            app.run()
        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        edited = [
            area
            for area in app.text_area
            if (area.key or "").startswith("edited_text_area")
        ]
        self.assertEqual(edited[0].value, "NOI DUNG BAI B")

    @unittest.skip("Da luoc bo theo yeu cau: chi liet ke loi, khong in lai bai moi")
    def test_edited_text_from_widget_is_used_for_word_export(self):
        """Hoi quy: ban Word phai lay noi dung bien tap vien sua trong o nhap."""
        captured: Dict[str, Any] = {}

        def fake_export(original_text, review_result, **kwargs):
            captured["review_result"] = review_result
            return b"PK-fake-docx"

        app = AppTest.from_file(str(APP_PATH), default_timeout=120)
        app.session_state[STATE_RESULT] = dict(SAMPLE_RESULT)
        app.session_state[STATE_ORIGINAL] = "goc"
        app.session_state[STATE_REVIEW_ID] = "lan-edit"
        with mock.patch(
            "core.docx_exporter.export_review_to_bytes", side_effect=fake_export
        ):
            with mock.patch.dict(
                os.environ, {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}, clear=False
            ):
                app.run()
                edited = [
                    area
                    for area in app.text_area
                    if (area.key or "").startswith("edited_text_area")
                ]
                edited[0].set_value("BAN DA SUA TAY CUA BIEN TAP VIEN").run()

        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        self.assertEqual(
            captured["review_result"]["edited_text"], "BAN DA SUA TAY CUA BIEN TAP VIEN"
        )

    def test_reset_button_clears_results_and_article_input(self):
        """Nut 'Bat dau bai moi' xoa ket qua va lam trong o nhap bai."""
        app = AppTest.from_file(str(APP_PATH), default_timeout=120)
        app.session_state[STATE_RESULT] = SAMPLE_RESULT
        app.session_state[STATE_ORIGINAL] = "goc"
        app.session_state[STATE_REVIEW_ID] = "lan-reset"
        with mock.patch.dict(
            os.environ, {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}, clear=False
        ):
            app.run()
        self.assertTrue(STATE_RESULT in app.session_state and app.session_state[STATE_RESULT])

        app.button(key="reset_review_button").click()
        with mock.patch.dict(
            os.environ, {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}, clear=False
        ):
            app.run()

        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        self.assertNotIn(STATE_RESULT, app.session_state)
        self.assertNotIn(STATE_REVIEW_ID, app.session_state)


class HighlightClipboardTests(unittest.TestCase):
    """TASK_05: ham chuan bi du lieu clipboard + CF_HTML."""

    def test_build_clipboard_payload_returns_html_and_text(self):
        highlight = {
            "rich_clipboard_html": '<span style="color: red;">Sai</span>',
            "plain_text": "Sai [NGHI VẤN: ...]",
        }
        payload = build_clipboard_payload(highlight)
        self.assertEqual(payload["html"], highlight["rich_clipboard_html"])
        self.assertEqual(payload["text"], highlight["plain_text"])

    def test_build_clipboard_payload_handles_missing_keys(self):
        payload = build_clipboard_payload({})
        self.assertEqual(payload, {"html": "", "text": ""})

    def test_cf_html_fragment_has_valid_offsets(self):
        fragment = build_cf_html_fragment('<span style="color: red;">lỗi</span>')
        self.assertIn("Version:0.9", fragment)
        self.assertIn("<!--StartFragment-->", fragment)
        self.assertIn("<!--EndFragment-->", fragment)
        # Tinh lai offset: header co dinh 4 so 10 chu so.
        lines = fragment.split("\r\n")
        fields = dict(
            line.split(":", 1) for line in lines if ":" in line and line[0].isupper()
        )
        raw = fragment.encode("utf-8")
        start_html = int(fields["StartHTML"])
        end_html = int(fields["EndHTML"])
        start_frag = int(fields["StartFragment"])
        end_frag = int(fields["EndFragment"])
        self.assertEqual(raw[start_html : start_html + 6], b"<html>")
        self.assertLess(start_html, start_frag)
        self.assertLess(start_frag, end_frag)
        self.assertLessEqual(end_frag, end_html)
        self.assertLessEqual(end_html, len(raw))
        # Noi dung fragment nam dung giua StartFragment va EndFragment.
        self.assertIn("lỗi", raw[start_frag:end_frag].decode("utf-8"))

    def test_write_clipboard_windows_returns_bool(self):
        # Moi truong test co the khong co clipboard -> chi kiem tra kieu tra ve.
        result = write_clipboard_windows("<span>x</span>", "x")
        self.assertIsInstance(result, bool)


HIGHLIGHT_ORIGINAL = (
    "Hội nghị khoa học quốc tế về AI khai mạc tại Hà Nội\n\n"
    "Sng ngày 17/10, tại Hà Nội đã diễn ra Hội nghị.\n"
    "Phát biểu tại hội nghị, GS Nguyễn Văn A đã trình bày báo cáo.\n"
)

HIGHLIGHT_SAMPLE_RESULT = {
    "spelling_errors": [
        {
            "loi_sai": "Sng",
            "vi_tri": "đoạn 2",
            "goi_y_sua": "Sáng",
            "ly_do": "Thiếu chữ 'á'.",
        }
    ],
    "fact_checks": [
        {
            "thong_tin_can_check": "GS Nguyễn Văn A",
            "ket_qua_xac_minh": "SAI - không có trong danh sách",
            "trich_dan_nguon": "https://example.com/danh-sach",
            "do_tin_cay": "cao",
        }
    ],
    "edited_text": "Hội nghị khoa học quốc tế về AI khai mạc tại Hà Nội.",
    "grounding_sources": [],
    "search_queries": [],
    "model": "gemini-2.5-flash",
}


@unittest.skip("Da luoc bo theo yeu cau: chi liet ke loi, khong boi do")
class HighlightTabTests(unittest.TestCase):
    """TASK_05: Tab 'Bài gốc bôi đỏ điểm nghi ngờ' + nut Sao chep Rich Text."""

    def _run_with_result(self, result, original):
        app = AppTest.from_file(str(APP_PATH), default_timeout=120)
        app.session_state[STATE_RESULT] = result
        app.session_state[STATE_ORIGINAL] = original
        app.session_state[STATE_REVIEW_ID] = "lan-highlight"
        with mock.patch.dict(
            os.environ, {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}, clear=False
        ):
            app.run()
        return app

    def test_highlight_tab_renders_with_priority_label(self):
        app = self._run_with_result(HIGHLIGHT_SAMPLE_RESULT, HIGHLIGHT_ORIGINAL)

        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        tab_labels = [tab.label for tab in app.tabs]
        self.assertTrue(
            any("Bài gốc bôi đỏ điểm nghi ngờ" in label for label in tab_labels)
        )

    def test_highlight_badge_shows_error_count(self):
        app = self._run_with_result(HIGHLIGHT_SAMPLE_RESULT, HIGHLIGHT_ORIGINAL)

        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        infos = " ".join(i.value for i in app.info)
        # 2 diem: "Sng" (loi chinh ta) + "GS Nguyễn Văn A" (fact-check SAI).
        self.assertIn("Đã đánh dấu 2 điểm nghi ngờ sai", infos)

    def test_highlight_html_contains_red_mark(self):
        app = self._run_with_result(HIGHLIGHT_SAMPLE_RESULT, HIGHLIGHT_ORIGINAL)

        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        markdown_values = " ".join(m.value for m in app.markdown)
        self.assertIn("<mark", markdown_values)
        self.assertIn("#b91c1c", markdown_values)

    def test_copy_button_renders_and_calls_clipboard(self):
        captured: Dict[str, Any] = {}

        def fake_clipboard(html_payload, text_payload):
            captured["html"] = html_payload
            captured["text"] = text_payload
            return True

        app = AppTest.from_file(str(APP_PATH), default_timeout=120)
        app.session_state[STATE_RESULT] = HIGHLIGHT_SAMPLE_RESULT
        app.session_state[STATE_ORIGINAL] = HIGHLIGHT_ORIGINAL
        app.session_state[STATE_REVIEW_ID] = "lan-copy"
        with mock.patch.dict(
            os.environ, {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}, clear=False
        ):
            app.run()
        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")

        copy_buttons = [
            b for b in app.button if "Sao chép bài báo bôi đỏ" in b.label
        ]
        self.assertEqual(len(copy_buttons), 1)

        with mock.patch(
            "core.clipboard_utils.write_clipboard_windows", side_effect=fake_clipboard
        ):
            copy_buttons[0].click()
            app.run()

        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        # Clipboard nhan duoc HTML chua the do + van ban thuan.
        self.assertIn("color: red", captured["html"])
        self.assertIn("Sng", captured["text"])
        successes = " ".join(s.value for s in app.success)
        self.assertIn("Ctrl + V", successes)

    def test_no_errors_shows_success_and_disables_copy(self):
        clean_result = dict(
            HIGHLIGHT_SAMPLE_RESULT,
            spelling_errors=[],
            fact_checks=[],
        )
        app = self._run_with_result(clean_result, HIGHLIGHT_ORIGINAL)

        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        infos = " ".join(i.value for i in app.info)
        successes = " ".join(s.value for s in app.success)
        self.assertIn("Không phát hiện điểm nghi ngờ sai", infos + successes)
        copy_buttons = [
            b for b in app.button if "Sao chép bài báo bôi đỏ" in b.label
        ]
        self.assertEqual(len(copy_buttons), 1)
        self.assertTrue(copy_buttons[0].disabled)

    def test_plain_text_fallback_available(self):
        app = self._run_with_result(HIGHLIGHT_SAMPLE_RESULT, HIGHLIGHT_ORIGINAL)

        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        plain_areas = [
            area
            for area in app.text_area
            if (area.key or "").startswith("highlight_plain_text")
        ]
        self.assertEqual(len(plain_areas), 1)
        self.assertIn("[NGHI VẤN:", plain_areas[0].value)


class SkillsManagerTabTests(unittest.TestCase):
    """Tab Quan ly Ky nang: xem danh sach, dat mac dinh, xoa, khoi phuc.

    Patch ``skills_manager.SKILLS_FILE`` sang thu muc tam de khong lam thay doi
    file ``skills/skills.json`` that cua du an.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.skills_file = Path(tmp.name) / "skills.json"
        patcher = mock.patch.object(skills_manager, "SKILLS_FILE", self.skills_file)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, app: AppTest) -> AppTest:
        with mock.patch.dict(
            os.environ, {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}, clear=False
        ):
            app.run()
        return app

    def test_default_skill_is_listed_in_skills_tab(self):
        app = self._run(AppTest.from_file(str(APP_PATH), default_timeout=120))

        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        markdown_values = " ".join(m.value for m in app.markdown)
        self.assertIn("duyet-bai-bao", markdown_values)

    def test_save_new_skill_via_form(self):
        app = AppTest.from_file(str(APP_PATH), default_timeout=120)
        self._run(app)
        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")

        # Truong "new" vi chua chon sua ky nang nao.
        app.text_input(key="skill_form_name_new").set_value("Kinh te")
        app.text_area(key="skill_form_instructions_new").set_value(
            "Chi dan nghiep vu kinh te."
        )
        app.button(key="save_skill_button").click()
        self._run(app)

        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        names = [s["name"] for s in skills_manager.get_skills(self.skills_file)]
        self.assertIn("Kinh te", names)

    def test_save_skill_with_empty_name_shows_error(self):
        app = AppTest.from_file(str(APP_PATH), default_timeout=120)
        self._run(app)

        app.text_input(key="skill_form_name_new").set_value("   ")
        app.text_area(key="skill_form_instructions_new").set_value("Chi dan.")
        app.button(key="save_skill_button").click()
        self._run(app)

        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        errors = " ".join(e.value for e in app.error)
        # App hien thi truc tiep thong bao loi tu skills_manager.
        self.assertIn("Ky nang bat buoc phai co ten (name).", errors)

    def test_set_custom_skill_as_default(self):
        saved = skills_manager.save_skill(
            {"name": "The thao", "instructions": "Chi dan the thao."},
            self.skills_file,
        )

        app = AppTest.from_file(str(APP_PATH), default_timeout=120)
        self._run(app)
        app.button(key=f"default_skill_{saved['id']}").click()
        self._run(app)

        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        default = skills_manager.get_default_skill(self.skills_file)
        self.assertEqual(default["id"], saved["id"])

    def test_delete_custom_skill_via_form(self):
        saved = skills_manager.save_skill(
            {"name": "Am nhac", "instructions": "Chi dan am nhac."},
            self.skills_file,
        )

        app = AppTest.from_file(str(APP_PATH), default_timeout=120)
        self._run(app)
        # Bam "Sua" de mo bien mau ky nang, sau do xoa.
        app.button(key=f"edit_skill_{saved['id']}").click()
        self._run(app)
        app.button(key="delete_skill_button").click()
        self._run(app)

        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        ids = [s["id"] for s in skills_manager.get_skills(self.skills_file)]
        self.assertNotIn(saved["id"], ids)

    def test_cannot_delete_last_default_skill(self):
        app = AppTest.from_file(str(APP_PATH), default_timeout=120)
        self._run(app)
        # Mo bien mau ky nang mac dinh duy nhat va thu xoa.
        app.button(key="edit_skill_duyet-bai-bao").click()
        self._run(app)
        app.button(key="delete_skill_button").click()
        self._run(app)

        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        errors = " ".join(e.value for e in app.error)
        self.assertIn("Không thể xóa", errors)
        ids = [s["id"] for s in skills_manager.get_skills(self.skills_file)]
        self.assertIn("duyet-bai-bao", ids)

    def test_reset_skills_restores_default_only(self):
        skills_manager.save_skill(
            {"name": "Am nhac", "instructions": "Chi dan."}, self.skills_file
        )
        self.assertEqual(len(skills_manager.get_skills(self.skills_file)), 2)

        app = AppTest.from_file(str(APP_PATH), default_timeout=120)
        self._run(app)
        app.button(key="reset_skills_button").click()
        self._run(app)

        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        skills = skills_manager.get_skills(self.skills_file)
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0]["id"], "duyet-bai-bao")
        self.assertTrue(skills[0]["is_default"])


class GeminiWebSessionUiTests(unittest.TestCase):
    """TASK_10 - Giao dien Gemini Web Session RPC (1 nut, khong GUI)."""

    def test_sidebar_has_gemini_web_session_expander(self):
        app = _make_app()

        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        labels = [expander.label for expander in app.sidebar.expander]
        self.assertTrue(
            any("Phiên Gemini Web" in label for label in labels),
            msg=f"Khong thay expander phiên Gemini Web: {labels}",
        )

    def test_default_engine_is_gemini_web_session(self):
        app = _make_app()

        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        radios = [radio.label for radio in app.sidebar.radio]
        self.assertIn("Phương thức duyệt bài", radios)
        self.assertIn("Gemini Web Session", str(app.sidebar.radio[0].value))

    def test_main_button_is_single_click_gemini_web(self):
        app = _make_app()

        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        labels = [button.label for button in app.button]
        self.assertTrue(any("Bắt Đầu Kiểm Duyệt Qua Gemini Web" in label for label in labels))
        # Khong con nut PWA (GUI) nao trong luong chinh.
        self.assertFalse(any("App Gemini" in label for label in labels))

    def test_session_panel_has_grant_controls(self):
        with mock.patch("core.gemini_web_engine.load_gemini_session", return_value={"secure_1psid": "", "secure_1psidts": ""}), \
             mock.patch("app.load_gemini_session", return_value={"secure_1psid": "", "secure_1psidts": ""}):
            app = _make_app()

            self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
            expander = app.sidebar.expander[0]
            keys = [field.key for field in expander.text_input]
            self.assertIn("gw_psid", keys)
            self.assertIn("gw_psidts", keys)
            self.assertIn("gw_email", keys)
            self.assertIn("gw_authuser", keys)
            button_labels = [button.label for button in expander.button]
            self.assertTrue(any("Lưu Cấu Hình" in label for label in button_labels))

    def test_one_click_review_stores_result(self):
        from core import gemini_web_engine as gwe

        app = _make_app()
        app.text_area(key="article_text_area").input("Bài báo cần kiểm duyệt.")
        app.run()

        payload = {
            "spelling_errors": [
                {
                    "loi_sai": "đạo đực",
                    "vi_tri": "đoạn 1",
                    "goi_y_sua": "đạo đức",
                    "ly_do": "Sai chính tả",
                }
            ],
            "fact_checks": [],
            "edited_text": "Bản đã biên tập.",
        }
        normalized = gwe.normalize_review_result(payload, model=gwe.GEMINI_WEB_MODEL_NAME)

        fake_engine = mock.MagicMock()
        fake_engine.is_granted.return_value = True
        fake_engine.build_prompt.return_value = "Mock prompt"
        fake_engine.review_article_sync.return_value = normalized

        with mock.patch.object(gwe, "GeminiWebEngine", return_value=fake_engine):
            app.button(key="run_review_button").click()
            app.run()

        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        self.assertTrue(fake_engine.review_article_sync.called)
        self.assertIn("review_result", app.session_state)
        self.assertEqual(
            app.session_state["review_result"]["edited_text"], "Bản đã biên tập."
        )

    def test_review_without_grant_shows_warning(self):
        from core import gemini_web_engine as gwe

        app = _make_app()
        app.text_area(key="article_text_area").input("Bài báo cần kiểm duyệt.")
        app.run()

        fake_engine = mock.MagicMock()
        fake_engine.is_granted.return_value = False

        with mock.patch.object(gwe, "GeminiWebEngine", return_value=fake_engine):
            app.button(key="run_review_button").click()
            app.run()

        self.assertFalse(app.exception, msg=f"App loi: {app.exception}")
        self.assertIn("review_result", app.session_state)
        self.assertTrue(app.session_state["review_result"].get("auth_error"))
        errors = " ".join(item.value for item in app.error)
        self.assertIn("liên hệ Admin", errors)


if __name__ == "__main__":
    unittest.main(verbosity=2)
