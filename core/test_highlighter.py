"""
Unit test cho core/highlighter.py (TASK_05)
===========================================

Chay:
    & ".venv\\Scripts\\python.exe" -m unittest core.test_highlighter -v

Bo sung 20+ test cases: khop tieng Viet co dau, khop hoa chu, bai khong co
loi, nhieu loi tren mot dong, doan chồng cheo, escape HTML, ket qua kiem
chung dung/sai, ghi chu plain_text, rich clipboard HTML va tinh toan doan
(cat theo dong) dung cho module xuat Word.
"""

from __future__ import annotations

import html
import unittest

from core.highlighter import (
    CATEGORY_FACTCHECK,
    CATEGORY_SPELLING,
    CLIPBOARD_SPAN_STYLE,
    NOTE_PREFIX,
    collect_error_spans,
    collect_quoted_fragments,
    highlight_original_article,
    is_negative_fact_result,
    iter_segments,
    merge_spans,
    segments_for_range,
    strip_accents,
    to_display_html,
    to_plain_text,
    to_rich_clipboard_html,
)

ORIGINAL = """Hội nghị khoa học quốc tế về AI khai mạc tại Hà Nội

Sng ngày 17/10, tại Hà Nội đã diễn ra Hội nghị khoa học quốc tế.
GS Nguyễn Văn A cho biết đạo đực là quan trọng.
"""

SPELLING_ERRORS = [
    {
        "loi_sai": "Sng",
        "vi_tri": "đoạn 2, dòng 1",
        "goi_y_sua": "Sáng",
        "ly_do": "Thiếu chữ 'á' trong 'Sáng'.",
    },
    {
        "loi_sai": "đạo đực",
        "vi_tri": "đoạn 3",
        "goi_y_sua": "đạo đức",
        "ly_do": "Sai chính tả.",
    },
]

FACT_CHECKS_OK = [
    {
        "thong_tin_can_check": "Hội nghị khai mạc ngày 17/10 tại Hà Nội",
        "ket_qua_xac_minh": "ĐÚNG theo thông cáo ban tổ chức",
        "trich_dan_nguon": "https://example.com/thong-cao",
        "do_tin_cay": "cao",
    }
]

FACT_CHECKS_BAD = [
    {
        "thong_tin_can_check": "GS Nguyễn Văn A",
        "ket_qua_xac_minh": "SAI - danh sách GS của Bộ không có người này",
        "trich_dan_nguon": "https://example.com/danh-sach",
        "do_tin_cay": "cao",
    }
]


class StripAccentsTests(unittest.TestCase):
    def test_strips_vietnamese_accents(self):
        self.assertEqual(strip_accents("CHƯA XÁC MINH"), "CHUA XAC MINH")

    def test_d_stroke_becomes_d(self):
        self.assertEqual(strip_accents("đạo đức"), "dao duc")

    def test_empty_string(self):
        self.assertEqual(strip_accents(""), "")


class IsNegativeFactResultTests(unittest.TestCase):
    def test_sai_is_negative(self):
        self.assertTrue(is_negative_fact_result("SAI hoàn toàn"))

    def test_chua_xac_minh_is_negative(self):
        self.assertTrue(is_negative_fact_result("CHƯA XÁC MINH được"))

    def test_canh_bao_is_negative(self):
        self.assertTrue(is_negative_fact_result("CẢNH BÁO: số liệu cũ"))

    def test_dung_is_not_negative(self):
        self.assertFalse(is_negative_fact_result("ĐÚNG theo thông cáo"))

    def test_empty_is_not_negative(self):
        self.assertFalse(is_negative_fact_result(""))
        self.assertFalse(is_negative_fact_result(None))

    def test_english_markers(self):
        self.assertTrue(is_negative_fact_result("UNVERIFIED claim"))
        self.assertFalse(is_negative_fact_result("Verified by source"))


class CollectQuotedFragmentsTests(unittest.TestCase):
    def test_extracts_double_quoted_fragment(self):
        fragments = collect_quoted_fragments('Vụ cháy "khu công nghiệp Tân Bình" tháng 2/2024')
        self.assertEqual(fragments, ["khu công nghiệp Tân Bình"])

    def test_no_quotes_returns_empty(self):
        self.assertEqual(collect_quoted_fragments("Không có ngoac kep"), [])

    def test_empty_string(self):
        self.assertEqual(collect_quoted_fragments(""), [])


class MergeSpansTests(unittest.TestCase):
    def test_overlapping_spans_are_merged(self):
        merged = merge_spans(
            [(0, 10, {"category": CATEGORY_SPELLING, "note": "a"}),
             (5, 15, {"category": CATEGORY_SPELLING, "note": "b"})]
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual((merged[0][0], merged[0][1]), (0, 15))
        self.assertIn("a", merged[0][2]["note"])
        self.assertIn("b", merged[0][2]["note"])

    def test_adjacent_spans_stay_separate(self):
        merged = merge_spans(
            [(0, 5, {"category": CATEGORY_SPELLING, "note": "a"}),
             (5, 10, {"category": CATEGORY_SPELLING, "note": "b"})]
        )
        self.assertEqual(len(merged), 2)

    def test_empty_input(self):
        self.assertEqual(merge_spans([]), [])


class HighlightArticleTests(unittest.TestCase):
    """Test ham chinh highlight_original_article."""

    def test_vietnamese_spelling_matched_with_diacritics(self):
        result = highlight_original_article(ORIGINAL, SPELLING_ERRORS, [])
        self.assertIn("đạo đực", result["html"])
        self.assertIn('<mark style=', result["html"])
        self.assertIn("Sng", result["html"])
        self.assertEqual(result["error_count"], 2)

    def test_no_errors_keeps_original_intact(self):
        result = highlight_original_article(ORIGINAL, [], [])
        self.assertEqual(result["error_count"], 0)
        self.assertNotIn("<mark", result["html"])
        # plain_text = nguyen van bai goc (khong them ghi chu).
        self.assertEqual(result["plain_text"], ORIGINAL)

    def test_multiple_errors_same_line_no_nested_tags(self):
        text = "Sng và đạo đực đều sai trên cùng một dòng."
        result = highlight_original_article(text, SPELLING_ERRORS, [])
        self.assertEqual(result["error_count"], 2)
        # Khong co the lap: moi the mark dong nghia xuong dong rieng.
        self.assertEqual(result["html"].count("<mark"), 2)
        self.assertEqual(result["html"].count("</mark>"), 2)
        self.assertNotIn("</mark></mark>", result["html"])

    def test_html_special_characters_are_escaped(self):
        text = "Bài có thẻ <script>alert('x')</script> và & ampersand."
        result = highlight_original_article(
            text,
            [{"loi_sai": "<script>", "goi_y_sua": "bỏ", "ly_do": "HTML nguy hiểm"}],
            [],
        )
        self.assertNotIn("<script>alert", result["html"])
        self.assertIn("&lt;script&gt;", result["html"])

    def test_fact_check_bad_result_is_highlighted(self):
        result = highlight_original_article(ORIGINAL, [], FACT_CHECKS_BAD)
        self.assertEqual(result["error_count"], 1)
        self.assertIn("GS Nguyễn Văn A", result["html"])
        self.assertIn("SAI", result["html"])

    def test_fact_check_ok_result_not_highlighted(self):
        result = highlight_original_article(ORIGINAL, [], FACT_CHECKS_OK)
        self.assertEqual(result["error_count"], 0)
        self.assertNotIn("<mark", result["html"])

    def test_fact_check_quoted_fragment_fallback(self):
        text = "Chi tiết về khu công nghiệp Tân Bình vẫn đang được làm rõ."
        facts = [
            {
                "thong_tin_can_check": 'Vụ cháy "khu công nghiệp Tân Bình" tháng 2/2024',
                "ket_qua_xac_minh": "CHƯA XÁC MINH",
                "trich_dan_nguon": "",
                "do_tin_cay": "thấp",
            }
        ]
        result = highlight_original_article(text, [], facts)
        self.assertEqual(result["error_count"], 1)
        self.assertIn("khu công nghiệp Tân Bình", result["html"])

    def test_case_insensitive_matching(self):
        text = "hội nghị KHAI MẠC long trọng."
        result = highlight_original_article(
            text,
            [{"loi_sai": "khai mạc", "goi_y_sua": "khai mạc", "ly_do": "hoa thuong"}],
            [],
        )
        self.assertEqual(result["error_count"], 1)

    def test_plain_text_contains_annotation(self):
        result = highlight_original_article(ORIGINAL, SPELLING_ERRORS, [])
        self.assertIn(NOTE_PREFIX, result["plain_text"])
        self.assertIn("Sng [NGHI VẤN:", result["plain_text"])
        self.assertIn("Thiếu chữ 'á'", result["plain_text"])
        self.assertIn("ngày 17/10", result["plain_text"])
        # Bài gốc không mất đoạn nào (chỉ thêm ghi chú).
        self.assertIn("GS Nguyễn Văn A cho biết", result["plain_text"])

    def test_rich_clipboard_html_uses_red_span(self):
        result = highlight_original_article(ORIGINAL, SPELLING_ERRORS, [])
        self.assertIn(f'<span style="{CLIPBOARD_SPAN_STYLE}">', result["rich_clipboard_html"])
        self.assertEqual(result["rich_clipboard_html"].count("<span"), 2)
        self.assertIn("Sng", result["rich_clipboard_html"])

    def test_rich_clipboard_html_escapes_content(self):
        text = "Nội dung <b>đậm</b> không được thực thi."
        result = highlight_original_article(
            text,
            [{"loi_sai": "<b>đậm</b>", "goi_y_sua": "đậm", "ly_do": "test"}],
            [],
        )
        self.assertNotIn("<b>đậm</b>", result["rich_clipboard_html"])
        self.assertIn("&lt;b&gt;đậm&lt;/b&gt;", result["rich_clipboard_html"])

    def test_empty_inputs_are_safe(self):
        result = highlight_original_article("", None, None)
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(result["html"], "")
        self.assertEqual(result["plain_text"], "")
        self.assertEqual(result["rich_clipboard_html"], "")

    def test_missing_error_phrase_ignored(self):
        result = highlight_original_article(
            ORIGINAL,
            [{"loi_sai": "từ không tồn tại", "goi_y_sua": "x", "ly_do": "y"}],
            [],
        )
        self.assertEqual(result["error_count"], 0)

    def test_newlines_preserved_in_html_and_plain(self):
        result = highlight_original_article(ORIGINAL, SPELLING_ERRORS, [])
        self.assertEqual(result["plain_text"].count("\n"), ORIGINAL.count("\n"))
        # Xuong dong trong html duoc bieu dien bang <br>.
        self.assertIn("<br>", result["html"])

    def test_spans_are_sorted_and_non_overlapping(self):
        result = highlight_original_article(ORIGINAL, SPELLING_ERRORS, FACT_CHECKS_BAD)
        spans = result["spans"]
        self.assertEqual(spans, sorted(spans, key=lambda s: s["start"]))
        for previous, current in zip(spans, spans[1:]):
            self.assertLessEqual(previous["end"], current["start"])


class SegmentHelpersTests(unittest.TestCase):
    def test_iter_segments_reconstructs_original(self):
        spans = collect_error_spans(ORIGINAL, SPELLING_ERRORS, [])
        rebuilt = "".join(segment for segment, _ in iter_segments(ORIGINAL, spans))
        self.assertEqual(rebuilt, ORIGINAL)

    def test_segments_for_range_cuts_per_line(self):
        spans = collect_error_spans(ORIGINAL, SPELLING_ERRORS, [])
        line_start = ORIGINAL.index("Sng")
        line_end = ORIGINAL.index("\n", line_start)
        segments = segments_for_range(ORIGINAL, spans, line_start, line_end)
        rebuilt = "".join(segment for segment, _ in segments)
        self.assertEqual(rebuilt, ORIGINAL[line_start:line_end])
        # Chi doan "Sng" la danh dau.
        marked = [meta for _, meta in segments if meta is not None]
        self.assertEqual(len(marked), 1)
        self.assertEqual(marked[0]["category"], CATEGORY_SPELLING)

    def test_display_and_plain_helpers_direct_call(self):
        spans = [(0, 3, {"category": CATEGORY_SPELLING, "note": "n"})]
        self.assertTrue(to_display_html("abc", spans).startswith("<mark"))
        self.assertEqual(to_plain_text("abc", spans), "abc [NGHI VẤN: n]")
        self.assertTrue(to_rich_clipboard_html("abc", spans).startswith('<span'))


if __name__ == "__main__":
    unittest.main(verbosity=2)
