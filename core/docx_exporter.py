"""
core/docx_exporter.py
=====================

Module xuat Ban Word Toa Soan cho he thong "Kiem Duyet Bai Bao AI".

Nhiem vu
--------
Tu ket qua kiem duyet cua ``core.reviewer`` (một dict/``ReviewResult`` gom
``spelling_errors``, ``fact_checks``, ``edited_text``) tao ra mot file Word
``.docx`` chuan toa soan, gom:

1. Trang bia / Header toa soan: ten toa soan, tieu de bao cao, ngay gio xuat,
   tieu de bai bao va cac chi so tong hop.
2. Bang 1 - Thong ke loi chinh ta (STT, Tu loi, Vi tri, Goi y sua, Ly do)
   co dinh dang vien bang, do dam tieu de, ke soc xen ke.
3. Bang 2 - Kiem chung su kien & Nhan vat (Thong tin, Ket qua xac minh,
   Do tin cay, Nguon URL tham chieu) + phu luc nguon Google Search.
4. Ngat trang (page break).
5. Tieu de "TOÀN VĂN BÀI BÁO ĐÃ BIÊN TẬP" va toan van bai bao da sua sach,
   font Times New Roman chuan bao chi.

Rang buoc kien truc (TencentDB - SPEC_02)
-----------------------------------------
- Cau truc file Word dung theo ``docx_structure``.
- Font Times New Roman chuan bao chi; ho tro day du tieng Viet co dau.
- Moi thu luu tren o E.

Ghi chu: cot "Vi tri" va "Do tin cay" duoc bo sung them so voi danh sach toi
thieu trong dac ta, vi du lieu kiem duyet da cung cap san va chung giup bien
tap vien dinh vi loi / danh gia do chinh xac nhanh hon.
"""

from __future__ import annotations

import io
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

from docx import Document
from docx.document import Document as DocumentObject
from docx.enum.section import WD_SECTION_START
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from docx.table import Table
from docx.text.paragraph import Paragraph

# ---------------------------------------------------------------------------
# Hang so trinh bay
# ---------------------------------------------------------------------------

#: Font chuan bao chi.
BODY_FONT_NAME = "Times New Roman"

#: Tieu de bao cao tren trang bia.
REPORT_TITLE = "BÁO CÁO KIỂM DUYỆT BÀI BÁO - BAN BIÊN TẬP"

#: Tieu de phan toan van bai bao da bien tap.
EDITED_TEXT_HEADING = "TOÀN VĂN BÀI BÁO ĐÃ BIÊN TẬP"

#: Tieu de phan ban thao goc danh dau loi nghi ngo (TASK_05).
HIGHLIGHT_DRAFT_HEADING = "BẢN THẢO GỐC ĐÁNH DẤU LỖI NGHI NGỜ"

#: Tieu de cac muc.
SPELLING_SECTION_HEADING = "I. THỐNG KÊ LỖI CHÍNH TẢ"
FACT_SECTION_HEADING = "II. KIỂM CHỨNG SỰ KIỆN & NHÂN VẬT (FACT-CHECK)"
GROUNDING_SECTION_HEADING = "Nguồn tham chiếu tra cứu (Google Search)"

#: Tieu de cot cac bang.
SPELLING_TABLE_HEADERS: tuple = ("STT", "Từ lỗi", "Vị trí", "Gợi ý sửa", "Lý do")
FACT_TABLE_HEADERS: tuple = (
    "STT",
    "Thông tin cần kiểm chứng",
    "Kết quả xác minh",
    "Độ tin cậy",
    "Nguồn URL tham chiếu",
)

#: Do rong cot (cm) - tong bang chieu rong vung in (A4, le 2cm => ~17cm).
SPELLING_COL_WIDTHS_CM: tuple = (1.1, 3.3, 2.9, 3.4, 6.3)
FACT_COL_WIDTHS_CM: tuple = (1.1, 4.2, 4.6, 2.1, 5.0)

#: Mau sac giao dien.
HEADER_FILL = "1F4E79"        # xanh dam cho dong tieu de bang
HEADER_TEXT_COLOR = RGBColor(0xFF, 0xFF, 0xFF)
ZEBRA_FILL = "EAF1FB"         # xanh nhat ke soc chan
BORDER_COLOR = "808080"
EMPTY_ROW_COLOR = RGBColor(0x80, 0x80, 0x80)

#: Mau do danh dau loi nghi ngo tren ban thao goc (TASK_05) — RGB(220, 38, 38).
ERROR_TEXT_COLOR = RGBColor(220, 38, 38)

#: Dai toi da cua tieu de bai bao suy ra tu dong dau tien (ky tu).
MAX_TITLE_CHARS = 160

#: Cac khoa bat buoc cua ket qua kiem duyet.
SPELLING_FIELDS = ("loi_sai", "vi_tri", "goi_y_sua", "ly_do")
FACT_CHECK_FIELDS = (
    "thong_tin_can_check",
    "ket_qua_xac_minh",
    "trich_dan_nguon",
    "do_tin_cay",
)


# ---------------------------------------------------------------------------
# Tien ich XML thap (low-level) de dinh dang bang / doan van
# ---------------------------------------------------------------------------


def _set_run_font(
    run: Any,
    name: str = BODY_FONT_NAME,
    size: float = 12.0,
    bold: bool = False,
    italic: bool = False,
    color: Optional[RGBColor] = None,
) -> None:
    """Dat font (ca Latin lan CJK/eastAsia de hien chu Viet) cho mot ``run``."""
    run.font.name = name
    run.font.size = Pt(size)
    run.bold = bold
    run.italic = italic
    if color is not None:
        run.font.color.rgb = color

    # python-docx khong tu dat truong eastAsia -> phai them thu cong de chu
    # Viet co dau hien dung tren moi phien ban Word.
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(qn(attr), name)


def _set_cell_shading(cell: Any, fill: str) -> None:
    """To mau nen cho mot o trong bang."""
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def _set_table_borders(table: Table, color: str = BORDER_COLOR, size: int = 6) -> None:
    """Ke vien day du (ngoai + trong) cho bang."""
    tbl_pr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), str(size))
        element.set(qn("w:space"), "0")
        element.set(qn("w:color"), color)
        borders.append(element)
    tbl_pr.append(borders)


def _set_repeat_table_header(row: Any) -> None:
    """Lap lai dong tieu de khi bang tran sang trang."""
    tr_pr = row._tr.get_or_add_trPr()
    tbl_header = OxmlElement("w:tblHeader")
    tbl_header.set(qn("w:val"), "true")
    tr_pr.append(tbl_header)


def _set_paragraph_bottom_border(
    paragraph: Paragraph, color: str = HEADER_FILL, size: int = 8
) -> None:
    """Ke duong ke duoi mot doan van (dung lam duong phan cach)."""
    p_pr = paragraph._p.get_or_add_pPr()
    pbdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(size))
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), color)
    pbdr.append(bottom)
    p_pr.append(pbdr)


def _add_field_run(paragraph: Paragraph, field_code: str) -> None:
    """Chen mot Word field (vi du PAGE) vao doan van - dung cho footer."""
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = field_code
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.append(begin)
    run._r.append(instr)
    run._r.append(end)


def _set_cell_widths(table: Table, widths_cm: Sequence[float]) -> None:
    """Dat do rong cot (cm) cho toan bo bang va tat autofit."""
    table.autofit = False
    for column_index, width_cm in enumerate(widths_cm):
        for row in table.rows:
            if column_index < len(row.cells):
                row.cells[column_index].width = Cm(width_cm)


def _write_cell(
    cell: Any,
    text: str,
    *,
    bold: bool = False,
    italic: bool = False,
    size: float = 11.0,
    color: Optional[RGBColor] = None,
    align: WD_ALIGN_PARAGRAPH = WD_ALIGN_PARAGRAPH.LEFT,
) -> None:
    """Ghi noi dung van ban vao mot o bang (xoa noi dung mac dinh truoc)."""
    cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    paragraph = cell.paragraphs[0]
    paragraph.alignment = align
    paragraph.paragraph_format.space_before = Pt(2)
    paragraph.paragraph_format.space_after = Pt(2)
    run = paragraph.add_run("" if text is None else str(text))
    _set_run_font(
        run, name=BODY_FONT_NAME, size=size, bold=bold, italic=italic, color=color
    )


def _add_paragraph(
    doc: DocumentObject,
    text: str = "",
    *,
    size: float = 12.0,
    bold: bool = False,
    italic: bool = False,
    color: Optional[RGBColor] = None,
    align: WD_ALIGN_PARAGRAPH = WD_ALIGN_PARAGRAPH.LEFT,
    space_before: float = 0.0,
    space_after: float = 6.0,
    first_line_indent: Optional[float] = None,
) -> Paragraph:
    """Them mot doan van da dinh dang vao tai lieu."""
    paragraph = doc.add_paragraph()
    paragraph.alignment = align
    fmt = paragraph.paragraph_format
    fmt.space_before = Pt(space_before)
    fmt.space_after = Pt(space_after)
    fmt.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
    fmt.line_spacing = 1.15
    if first_line_indent is not None:
        fmt.first_line_indent = Cm(first_line_indent)

    if text:
        run = paragraph.add_run(text)
        _set_run_font(
            run, name=BODY_FONT_NAME, size=size, bold=bold, italic=italic, color=color
        )
    return paragraph


# ---------------------------------------------------------------------------
# Chuan hoa du lieu dau vao
# ---------------------------------------------------------------------------


def _as_text(value: Any) -> str:
    """Ep gia tri ve chuoi an toan (None -> rong, list -> noi bang dau phay)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return ", ".join(_as_text(item) for item in value if item is not None)
    return str(value).strip()


def coerce_review_result(review_result: Any) -> Dict[str, Any]:
    """Chuan hoa nhieu dang ket qua kiem duyet ve mot dict thong nhat.

    Chap nhan:
    - ``dict`` (ket qua tu ``core.reviewer.review``).
    - Doi tuong co phuong thuc ``to_dict()`` (vi du ``ReviewResult``).
    - Doi tuong co thuoc tinh tuong ung.
    - ``None`` -> ket qua rong.
    """
    if review_result is None:
        return {
            "spelling_errors": [],
            "fact_checks": [],
            "edited_text": "",
            "grounding_sources": [],
            "search_queries": [],
            "model": "",
        }

    if isinstance(review_result, dict):
        data = review_result
    elif hasattr(review_result, "to_dict"):
        data = review_result.to_dict()
    else:
        data = {
            "spelling_errors": getattr(review_result, "spelling_errors", []),
            "fact_checks": getattr(review_result, "fact_checks", []),
            "edited_text": getattr(review_result, "edited_text", ""),
            "grounding_sources": getattr(review_result, "grounding_sources", []),
            "search_queries": getattr(review_result, "search_queries", []),
            "model": getattr(review_result, "model", ""),
        }

    spelling = data.get("spelling_errors") or []
    facts = data.get("fact_checks") or []
    if isinstance(spelling, dict):
        spelling = [spelling]
    if isinstance(facts, dict):
        facts = [facts]

    return {
        "spelling_errors": [item for item in spelling if isinstance(item, dict)],
        "fact_checks": [item for item in facts if isinstance(item, dict)],
        "edited_text": _as_text(data.get("edited_text")),
        "grounding_sources": [
            item for item in (data.get("grounding_sources") or []) if isinstance(item, dict)
        ],
        "search_queries": [
            _as_text(item) for item in (data.get("search_queries") or []) if item
        ],
        "model": _as_text(data.get("model")),
    }


def extract_article_title(original_text: str) -> str:
    """Suy ra tieu de bai bao tu dong khong trong dau tien cua van ban goc."""
    if not original_text:
        return "(Không có tiêu đề)"
    for line in original_text.splitlines():
        candidate = line.strip()
        if candidate:
            if len(candidate) > MAX_TITLE_CHARS:
                candidate = candidate[:MAX_TITLE_CHARS].rstrip() + "..."
            return candidate
    return "(Không có tiêu đề)"


def _format_export_datetime(moment: datetime) -> str:
    """Dinh dang ngay gio xuat bao cao theo van phong bao chi Viet Nam."""
    return (
        f"Ngày {moment.day:02d} tháng {moment.month:02d} năm {moment.year} "
        f"- {moment.hour:02d}:{moment.minute:02d}"
    )


# ---------------------------------------------------------------------------
# Dinh dang tai lieu
# ---------------------------------------------------------------------------


def _configure_document(doc: DocumentObject) -> None:
    """Thiet lap le trang, font mac dinh cho tai lieu."""
    section = doc.sections[0]
    section.top_margin = Cm(2.0)
    section.bottom_margin = Cm(2.0)
    section.left_margin = Cm(2.0)
    section.right_margin = Cm(2.0)

    normal = doc.styles["Normal"]
    normal.font.name = BODY_FONT_NAME
    normal.font.size = Pt(12)
    rpr = normal.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(qn(attr), BODY_FONT_NAME)


def _build_page_header(doc: DocumentObject, newspaper_name: str) -> None:
    """Tao header trang (o moi trang): ten toa soan + duong ke chan trang."""
    section = doc.sections[0]
    section.different_first_page_header_footer = False
    header = section.header
    header.is_linked_to_previous = False
    paragraph = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
    paragraph.text = ""
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run(newspaper_name.upper())
    _set_run_font(run, size=10, bold=True)
    _set_paragraph_bottom_border(paragraph)


def _build_page_footer(doc: DocumentObject, newspaper_name: str) -> None:
    """Tao footer trang: ten toa soan ben trai + so trang ben phai."""
    section = doc.sections[0]
    footer = section.footer
    footer.is_linked_to_previous = False
    paragraph = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    paragraph.text = ""
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER

    run_prefix = paragraph.add_run("Trang ")
    _set_run_font(run_prefix, size=9, italic=True)
    _add_field_run(paragraph, "PAGE")
    # Dinh dang cho run field (khong the set font truc tiep qua run.font cho field
    # nen dung run tam thoi de giu kieu chu dong nhat).
    for run in paragraph.runs:
        _set_run_font(run, size=9, italic=True)


def _add_cover_header(
    doc: DocumentObject,
    *,
    newspaper_name: str,
    article_title: str,
    editor_name: Optional[str],
    review: Dict[str, Any],
    export_datetime: datetime,
) -> None:
    """Them khoi trang bia / header toa soan."""
    title_run = _add_paragraph(
        doc,
        newspaper_name.upper(),
        size=13,
        bold=True,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        space_after=2,
    )
    _set_paragraph_bottom_border(title_run, color=HEADER_FILL, size=6)

    _add_paragraph(
        doc,
        REPORT_TITLE,
        size=16,
        bold=True,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        space_before=8,
        space_after=4,
    )
    _add_paragraph(
        doc,
        _format_export_datetime(export_datetime),
        size=11,
        italic=True,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        space_after=10,
    )

    # Thong tin boi canh.
    _add_paragraph(
        doc,
        f"Tiêu đề bài báo: {article_title}",
        size=12,
        bold=True,
        space_after=2,
    )
    if editor_name:
        _add_paragraph(doc, f"Người kiểm duyệt: {editor_name}", size=11, space_after=2)
    if review.get("model"):
        _add_paragraph(
            doc, f"Model kiểm duyệt: {review['model']}", size=11, space_after=2
        )

    summary = (
        f"Tổng lỗi chính tả: {len(review['spelling_errors'])}   |   "
        f"Mục kiểm chứng: {len(review['fact_checks'])}   |   "
        f"Nguồn tham chiếu: {len(review['grounding_sources'])}"
    )
    summary_paragraph = _add_paragraph(doc, summary, size=11, space_before=4, space_after=8)
    _set_paragraph_bottom_border(summary_paragraph, color=BORDER_COLOR, size=4)


# ---------------------------------------------------------------------------
# Bang 1 & Bang 2
# ---------------------------------------------------------------------------


def _add_spelling_table(doc: DocumentObject, errors: List[Dict[str, Any]]) -> None:
    """Them Bang 1: Thong ke loi chinh ta."""
    table = doc.add_table(rows=1, cols=len(SPELLING_TABLE_HEADERS))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_table_borders(table)

    header_row = table.rows[0]
    _set_repeat_table_header(header_row)
    for index, header_text in enumerate(SPELLING_TABLE_HEADERS):
        cell = header_row.cells[index]
        _set_cell_shading(cell, HEADER_FILL)
        _write_cell(
            cell,
            header_text,
            bold=True,
            size=11,
            color=HEADER_TEXT_COLOR,
            align=WD_ALIGN_PARAGRAPH.CENTER,
        )

    if not errors:
        empty_row = table.add_row()
        merged = empty_row.cells[0]
        for cell in empty_row.cells[1:]:
            merged = merged.merge(cell)
        _write_cell(
            merged,
            "Không phát hiện lỗi chính tả.",
            italic=True,
            color=EMPTY_ROW_COLOR,
            align=WD_ALIGN_PARAGRAPH.CENTER,
        )
    else:
        for order, item in enumerate(errors, start=1):
            row = table.add_row()
            values = [
                str(order),
                _as_text(item.get("loi_sai")),
                _as_text(item.get("vi_tri")),
                _as_text(item.get("goi_y_sua")),
                _as_text(item.get("ly_do")),
            ]
            for index, value in enumerate(values):
                cell = row.cells[index]
                align = (
                    WD_ALIGN_PARAGRAPH.CENTER
                    if index == 0
                    else WD_ALIGN_PARAGRAPH.LEFT
                )
                _write_cell(cell, value, size=11, align=align)
            if order % 2 == 0:
                for cell in row.cells:
                    _set_cell_shading(cell, ZEBRA_FILL)

    _set_cell_widths(table, SPELLING_COL_WIDTHS_CM)
    _add_paragraph(doc, "", space_after=4)


def _add_fact_table(doc: DocumentObject, facts: List[Dict[str, Any]]) -> None:
    """Them Bang 2: Kiem chung su kien & nhan vat."""
    table = doc.add_table(rows=1, cols=len(FACT_TABLE_HEADERS))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_table_borders(table)

    header_row = table.rows[0]
    _set_repeat_table_header(header_row)
    for index, header_text in enumerate(FACT_TABLE_HEADERS):
        cell = header_row.cells[index]
        _set_cell_shading(cell, HEADER_FILL)
        _write_cell(
            cell,
            header_text,
            bold=True,
            size=11,
            color=HEADER_TEXT_COLOR,
            align=WD_ALIGN_PARAGRAPH.CENTER,
        )

    if not facts:
        empty_row = table.add_row()
        merged = empty_row.cells[0]
        for cell in empty_row.cells[1:]:
            merged = merged.merge(cell)
        _write_cell(
            merged,
            "Không có thông tin nào cần kiểm chứng.",
            italic=True,
            color=EMPTY_ROW_COLOR,
            align=WD_ALIGN_PARAGRAPH.CENTER,
        )
    else:
        for order, item in enumerate(facts, start=1):
            row = table.add_row()
            values = [
                str(order),
                _as_text(item.get("thong_tin_can_check")),
                _as_text(item.get("ket_qua_xac_minh")),
                _as_text(item.get("do_tin_cay")),
                _as_text(item.get("trich_dan_nguon")),
            ]
            for index, value in enumerate(values):
                cell = row.cells[index]
                align = (
                    WD_ALIGN_PARAGRAPH.CENTER
                    if index in (0, 3)
                    else WD_ALIGN_PARAGRAPH.LEFT
                )
                _write_cell(cell, value, size=11, align=align)
            if order % 2 == 0:
                for cell in row.cells:
                    _set_cell_shading(cell, ZEBRA_FILL)

    _set_cell_widths(table, FACT_COL_WIDTHS_CM)
    _add_paragraph(doc, "", space_after=4)


def _add_grounding_appendix(
    doc: DocumentObject, sources: List[Dict[str, Any]]
) -> None:
    """Them phu luc liet ke cac nguon Google Search da tra cuu duoc."""
    if not sources:
        return

    _add_paragraph(
        doc,
        GROUNDING_SECTION_HEADING,
        size=12,
        bold=True,
        space_before=6,
        space_after=4,
    )
    for index, source in enumerate(sources, start=1):
        uri = _as_text(source.get("uri"))
        title = _as_text(source.get("title")) or _as_text(source.get("domain")) or uri
        paragraph = doc.add_paragraph()
        paragraph.paragraph_format.space_after = Pt(2)
        paragraph.paragraph_format.line_spacing = 1.15
        run = paragraph.add_run(f"{index}. {title} - {uri}")
        _set_run_font(run, size=10)


# ---------------------------------------------------------------------------
# Phan toan van bai bao
# ---------------------------------------------------------------------------


def _add_edited_text(doc: DocumentObject, edited_text: str, original_text: str) -> None:
    """Them tieu de + toan van bai bao da bien tap, font chuan bao chi."""
    heading = _add_paragraph(
        doc,
        EDITED_TEXT_HEADING,
        size=14,
        bold=True,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        space_after=8,
    )
    _set_paragraph_bottom_border(heading, color=HEADER_FILL, size=6)

    body = edited_text.strip() if edited_text else ""
    used_fallback = False
    if not body:
        body = (original_text or "").strip()
        used_fallback = True

    if used_fallback and body:
        _add_paragraph(
            doc,
            "(Chưa có bản biên tập từ AI - hiển thị toàn văn bài báo gốc.)",
            size=11,
            italic=True,
            color=EMPTY_ROW_COLOR,
            space_after=6,
        )

    if not body:
        _add_paragraph(
            doc,
            "(Không có nội dung văn bản.)",
            size=12,
            italic=True,
            color=EMPTY_ROW_COLOR,
            align=WD_ALIGN_PARAGRAPH.CENTER,
        )
        return

    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            spacer = doc.add_paragraph()
            spacer.paragraph_format.space_after = Pt(0)
            spacer.paragraph_format.line_spacing = 1.15
            continue
        _add_paragraph(
            doc,
            line,
            size=12,
            align=WD_ALIGN_PARAGRAPH.JUSTIFY,
            space_after=6,
        )


# ---------------------------------------------------------------------------
# Phan ban thao goc danh dau loi nghi ngo (TASK_05)
# ---------------------------------------------------------------------------


def _add_highlighted_draft(
    doc: DocumentObject,
    original_text: str,
    spelling_errors: List[Dict[str, Any]],
    fact_checks: List[Dict[str, Any]],
) -> None:
    """Them phan "BẢN THẢO GỐC ĐÁNH DẤU LỖI NGHI NGỜ" vao cuoi bao cao Word.

    Giu nguyen 100% van ban, bo cuc, xuong dong cua bai goc; chi cac cum tu
    nghi ngo sai (loi chinh ta + ket qua kiem chung SAI/CHUA XAC MINH/CANH BAO)
    duoc to mau do (RGB 220,38,38), dam va gach duoi — dung theo yeu cau cua
    Ban bien tap trong TASK_05.
    """
    from core.highlighter import collect_error_spans, segments_for_range

    doc.add_page_break()

    heading = _add_paragraph(
        doc,
        HIGHLIGHT_DRAFT_HEADING,
        size=14,
        bold=True,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        space_after=6,
    )
    _set_paragraph_bottom_border(heading, color=HEADER_FILL, size=6)

    body = (original_text or "").strip()
    if not body:
        _add_paragraph(
            doc,
            "(Không có nội dung văn bản gốc.)",
            size=12,
            italic=True,
            color=EMPTY_ROW_COLOR,
            align=WD_ALIGN_PARAGRAPH.CENTER,
        )
        return

    spans = collect_error_spans(body, spelling_errors, fact_checks)
    _add_paragraph(
        doc,
        f"Đã đánh dấu {len(spans)} điểm nghi ngờ sai trên bản thảo gốc "
        "(chữ màu đỏ, đậm, gạch dưới).",
        size=11,
        italic=True,
        space_after=6,
    )

    # Duyet tung dong goc de giu nguyen bo cuc xuong dong; moi dong duoc cat
    # thanh cac doan (van binh thuong / doan danh dau) va viet thanh run rieng.
    cursor = 0
    for raw_line in body.split("\n"):
        line_start = cursor
        line_end = cursor + len(raw_line)
        cursor = line_end + 1  # +1 cho ky tu xuong dong

        if not raw_line.strip():
            spacer = doc.add_paragraph()
            spacer.paragraph_format.space_after = Pt(0)
            spacer.paragraph_format.line_spacing = 1.15
            continue

        paragraph = doc.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        paragraph.paragraph_format.space_after = Pt(6)
        paragraph.paragraph_format.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
        paragraph.paragraph_format.line_spacing = 1.15

        for segment, meta in segments_for_range(body, spans, line_start, line_end):
            if not segment:
                continue
            run = paragraph.add_run(segment)
            if meta is None:
                _set_run_font(run, name=BODY_FONT_NAME, size=12)
            else:
                note = str(meta.get("note") or "").strip()
                _set_run_font(
                    run,
                    name=BODY_FONT_NAME,
                    size=12,
                    bold=True,
                    color=ERROR_TEXT_COLOR,
                )
                run.underline = True
                if note:
                    # Nen do nhat phia sau chu de de bai bao roi nhan bao.
                    rpr = run._element.get_or_add_rPr()
                    shd = OxmlElement("w:shd")
                    shd.set(qn("w:val"), "clear")
                    shd.set(qn("w:color"), "auto")
                    shd.set(qn("w:fill"), "FEE2E2")
                    rpr.append(shd)


# ---------------------------------------------------------------------------
# API cong khai
# ---------------------------------------------------------------------------


def build_report_document(
    original_text: str,
    review_result: Any,
    *,
    article_title: Optional[str] = None,
    newspaper_name: str = "TÒA SOẠN",
    editor_name: Optional[str] = None,
    export_datetime: Optional[datetime] = None,
    include_grounding_appendix: bool = True,
    include_highlighted_draft: bool = True,
) -> DocumentObject:
    """Tao doi tuong ``Document`` bao cao kiem duyet (chua luu ra file).

    Parameters
    ----------
    original_text:
        Toan van bai bao goc (dung de suy ra tieu de va lam phuong an du phong).
    review_result:
        Ket qua kiem duyet (dict tu ``core.reviewer`` hoac doi tuong ``ReviewResult``).
    article_title:
        Tieu de bai bao. Neu None se tu suy ra tu dong dau cua ``original_text``.
    newspaper_name:
        Ten toa soan hien tren header.
    editor_name:
        Ten nguoi/bo phan kiem duyet.
    export_datetime:
        Thoi diem xuat bao cao (mac dinh la thoi diem hien tai).
    include_grounding_appendix:
        Co them phu luc nguon Google Search hay khong.
    include_highlighted_draft:
        Co them phan "BẢN THẢO GỐC ĐÁNH DẤU LỖI NGHI NGỜ" (TASK_05) hay khong.

    Returns
    -------
    docx.document.Document
        Tai lieu Word da duoc dinh dang day du.
    """
    review = coerce_review_result(review_result)
    moment = export_datetime or datetime.now()
    title = article_title.strip() if article_title and article_title.strip() else (
        extract_article_title(original_text)
    )

    doc = Document()
    _configure_document(doc)
    _build_page_header(doc, newspaper_name)
    _build_page_footer(doc, newspaper_name)

    # 1. Trang bia / Header toa soan.
    _add_cover_header(
        doc,
        newspaper_name=newspaper_name,
        article_title=title,
        editor_name=editor_name,
        review=review,
        export_datetime=moment,
    )

    # 2. Bang 1 - Thong ke loi chinh ta.
    _add_paragraph(
        doc,
        SPELLING_SECTION_HEADING,
        size=13,
        bold=True,
        space_before=6,
        space_after=6,
    )
    _add_spelling_table(doc, review["spelling_errors"])

    # 3. Bang 2 - Kiem chung su kien & nhan vat.
    _add_paragraph(
        doc,
        FACT_SECTION_HEADING,
        size=13,
        bold=True,
        space_before=6,
        space_after=6,
    )
    _add_fact_table(doc, review["fact_checks"])

    if include_grounding_appendix:
        _add_grounding_appendix(doc, review["grounding_sources"])

    # 4. Ngat trang.
    doc.add_page_break()

    # 5. Toan van bai bao da bien tap.
    _add_edited_text(doc, review["edited_text"], original_text)

    # 6. Ban thao goc danh dau loi nghi ngo (TASK_05).
    if include_highlighted_draft:
        _add_highlighted_draft(
            doc, original_text, review["spelling_errors"], review["fact_checks"]
        )

    return doc


def export_review_to_docx(
    original_text: str,
    review_result: Any,
    output_path: Union[str, os.PathLike],
    *,
    article_title: Optional[str] = None,
    newspaper_name: str = "TÒA SOẠN",
    editor_name: Optional[str] = None,
    export_datetime: Optional[datetime] = None,
    include_grounding_appendix: bool = True,
    include_highlighted_draft: bool = True,
) -> str:
    """Xuat bao cao kiem duyet ra file Word ``.docx``.

    Parameters
    ----------
    original_text:
        Toan van bai bao goc.
    review_result:
        Ket qua kiem duyet (dict hoac ``ReviewResult``).
    output_path:
        Duong dan file ``.docx`` dau ra (thu muc se duoc tao neu chua co).

    Returns
    -------
    str
        Duong dan tuyet doi cua file da ghi.
    """
    doc = build_report_document(
        original_text,
        review_result,
        article_title=article_title,
        newspaper_name=newspaper_name,
        editor_name=editor_name,
        export_datetime=export_datetime,
        include_grounding_appendix=include_grounding_appendix,
        include_highlighted_draft=include_highlighted_draft,
    )

    destination = Path(output_path)
    if destination.parent and not destination.parent.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(destination))
    return str(destination.resolve())


def export_review_to_bytes(
    original_text: str,
    review_result: Any,
    **kwargs: Any,
) -> bytes:
    """Xuat bao cao thanh ``bytes`` (dung cho ``st.download_button`` o Task 3).

    Cac tham so tu khoa duoc truyen nguyen ven cho ``build_report_document``.
    """
    doc = build_report_document(original_text, review_result, **kwargs)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


__all__ = [
    "export_review_to_docx",
    "export_review_to_bytes",
    "build_report_document",
    "coerce_review_result",
    "extract_article_title",
    "EDITED_TEXT_HEADING",
    "HIGHLIGHT_DRAFT_HEADING",
    "ERROR_TEXT_COLOR",
]
