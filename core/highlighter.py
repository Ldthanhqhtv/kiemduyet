"""
core/highlighter.py
===================

Module danh dau do cong cac diem nghi ngo sai tren BAN THAO GOC (TASK_05).

Nghiep vu
---------
Yeu cau cua Ban bien tap: "copy nguyen ven bai bao, chi duoc boi do phan nghi
ngo sai". Module nay giu nguyen 100% van ban, bo cuc, xuong dong cua bai goc,
chi boc cac cum tu nghi ngo sai (loi chinh ta + ket qua kiem chung) bang the
mau do:

- ``html``: chuoi HTML hien thi tren Streamlit/Desktop (the ``<mark>``).
- ``rich_clipboard_html``: HTML inline-style don gian de dan vao Microsoft
  Word / CMS VOV2 (the ``<span style="color: red; ...">``).
- ``plain_text``: bai goc kem ghi chu ``[NGHI VẤN: ...]`` cho ban text thuan.
- ``error_count``: tong so diem da boi do.

Thuat toan
---------
1. Quet vi tri loi chinh ta: tim moi lan xuat hien cua ``loi_sai`` trong bai
   goc (khop hoa chu, ho tro tieng Viet co dau).
2. Quet loi kiem chung: chi lay cac ``fact_checks`` co ``ket_qua_xac_minh`` la
   SAI / CHUA XAC MINH / CANH BAO... (xem ``is_negative_fact_result``). Vi tri
   can boi do lay tu ``thong_tin_can_check`` (truoc het khop toan bo, neu khong
   thay thi thu cac doan trong dau ngoac kep).
3. Gop cac doan chồng cheo (overlapping intervals) de khong bi lo the HTML vao
   nhau.
4. An toan HTML: escape ky tu dac biet (<, >, &, ") truoc khi boc the.

Rang buoc kien truc (TASK_05 / Part 6)
--------------------------------------
- Khong doi mot ky tu nao trong bai goc (plain_text chi THEM ghi chu).
- khong co placeholder / TODO; moi ham deu day du va co test di kem.
"""

from __future__ import annotations

import html
import re
import unicodedata
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Hang so hien thi
# ---------------------------------------------------------------------------

#: The <mark> dung cho giao dien Streamlit (mau do dam, nen do nhat).
DISPLAY_MARK_STYLE = (
    "color: #b91c1c; background-color: #fee2e2; font-weight: 600; "
    "text-decoration: underline;"
)

#: The <span> inline-style dung cho clipboard -> paste vao Word / CMS VOV2.
CLIPBOARD_SPAN_STYLE = "color: red; font-weight: bold; text-decoration: underline;"

#: Nhan ghi chu trong ban plain_text.
NOTE_PREFIX = "[NGHI VẤN: "
NOTE_SUFFIX = "]"

#: Cac tu khoa ket qua xac minh duoc coi la "nghi ngo sai" (da bo dau de
#: khop linh hoat, vi du "Chua xac minh" ~ "CHƯA XÁC MINH").
NEGATIVE_RESULT_MARKERS: Tuple[str, ...] = (
    "SAI",
    "CHUA XAC MINH",
    "CANH BAO",
    "KHONG DUNG",
    "NGHI NGO",
    "SAI LECH",
    "CHUA CHAC CHAN",
    "KHONG CHINH XAC",
    "FALSE",
    "UNVERIFIED",
    "WARNING",
    "MISLEADING",
)

#: Nhan loai diem danh dau.
CATEGORY_SPELLING = "chinh_ta"
CATEGORY_FACTCHECK = "kiem_chung"


# ---------------------------------------------------------------------------
# Tien ich
# ---------------------------------------------------------------------------


def strip_accents(text: str) -> str:
    """Bo dau tieng Viet (phuc vu so khop tu khoa khong dau).

    ``"CHƯA XÁC MINH" -> "CHUA XAC MINH"``.
    """
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    # NFD tach 'đ' thanh 'd' + dau, nhung 'đ' (d co gach) la mot ky tu rieng
    # khong co dau combining -> giu nguyen, nen doi thanh 'd' thu cong.
    return stripped.replace("đ", "d").replace("Đ", "D")


def is_negative_fact_result(ket_qua_xac_minh: Any) -> bool:
    """Kiem tra ket qua kiem chung co dang "nghi ngo sai" hay khong.

    Chap nhan cac dau hieu: SAI, CHUA XAC MINH, CANH BAO, KHONG DUNG, NGHI NGO,
    SAI LECH, CHUA CHAC CHAN, KHONG CHINH XAC (va tuc danh Anh tuong ung).

    Examples
    --------
    >>> is_negative_fact_result("SAI - nguon khac cho so lieu khac")
    True
    >>> is_negative_fact_result("ĐÚNG theo thông cáo ban tổ chức")
    False
    >>> is_negative_fact_result("")
    False
    """
    normalized = strip_accents(str(ket_qua_xac_minh or "")).upper()
    if not normalized.strip():
        return False
    return any(marker in normalized for marker in NEGATIVE_RESULT_MARKERS)


def collect_quoted_fragments(text: str) -> List[str]:
    """Lay cac doan van ben trong dau ngoac kep tu mot chuoi mo ta.

    Vi du: ``'Vụ cháy "khu công nghiệp Tân Bình" tháng 2/2024'`` ->
    ``["khu công nghiệp Tân Bình"]``. Dung khi mo ta fact-check khong khop
    truc tiep voi bai goc nhung co trich dan cum tu nguyen ban.
    """
    if not text:
        return []
    return re.findall(r"[“\"']([^”\"'\n]{2,})[”\"']", text)


def merge_spans(
    spans: Sequence[Tuple[int, int, Dict[str, Any]]],
) -> List[Tuple[int, int, Dict[str, Any]]]:
    """Gop cac doan danh dau bi chồng cheo (overlapping).

    Input la danh sach ``(start, end, meta)``. Sau khi sap xep theo ``start``,
    moi doan moi giao voi doan truoc (``start < end_truoc``) se bi gop lai:
    doan moi nhan gia tri ``end`` lon nhat, ``note`` cac doan bi gop noi bang
    "; " (loai trung), ``category`` giu lai cua doan xuat hien truoc.

    Returns
    -------
    list
        Danh sach cac doan da gop, da sap xep tang dan, khong con chồng cheo.
    """
    if not spans:
        return []

    ordered = sorted(spans, key=lambda item: (item[0], item[1]))
    merged: List[Tuple[int, int, Dict[str, Any]]] = [ordered[0]]

    for start, end, meta in ordered[1:]:
        prev_start, prev_end, prev_meta = merged[-1]
        if start < prev_end:  # chồng cheo -> gop vao doan truoc
            merged[-1] = (
                prev_start,
                max(prev_end, end),
                _combine_meta(prev_meta, meta),
            )
        else:  # khong giao -> giu rieng (khong gop doan ke nhau de giu 2 ghi chu)
            merged.append((start, end, meta))

    return merged


def _combine_meta(first: Dict[str, Any], second: Dict[str, Any]) -> Dict[str, Any]:
    """Gop metadata cua hai doan bi chồng cheo (noi ghi chu, giu loai dau tien)."""
    notes: List[str] = []
    for meta in (first, second):
        note = str(meta.get("note") or "").strip()
        if note and note not in notes:
            notes.append(note)
    return {
        "category": first.get("category") or second.get("category") or "",
        "note": "; ".join(notes),
    }


# ---------------------------------------------------------------------------
# Thu vi tri danh dau (span matching)
# ---------------------------------------------------------------------------


def _find_occurrences(text: str, needle: str) -> List[Tuple[int, int]]:
    """Tim tat ca vi tri xuat hien cua ``needle`` trong ``text`` (khop hoa chu).

    Khong lap lai vi tri (cac lan khop cheo se bi bo qua) de moi cum tu chi
    duoc danh dau mot lan.
    """
    if not needle:
        return []
    pattern = re.compile(re.escape(needle), re.IGNORECASE)
    found: List[Tuple[int, int]] = []
    last_end = -1
    for match in pattern.finditer(text):
        if match.start() >= last_end:
            found.append((match.start(), match.end()))
            last_end = match.end()
    return found


def _spell_note(error: Dict[str, Any]) -> str:
    """Goi y ghi chu cho mot loi chinh ta: ly do (neu co) sau do goi y sua."""
    ly_do = str(error.get("ly_do") or "").strip()
    goi_y = str(error.get("goi_y_sua") or "").strip()
    loi_sai = str(error.get("loi_sai") or "").strip()
    if ly_do:
        return f"{loi_sai} -> {goi_y}: {ly_do}" if goi_y else ly_do
    return f"{loi_sai} -> {goi_y}" if goi_y else loi_sai


def collect_error_spans(
    original_text: str,
    spelling_errors: Optional[Sequence[Dict[str, Any]]],
    fact_checks: Optional[Sequence[Dict[str, Any]]],
) -> List[Tuple[int, int, Dict[str, Any]]]:
    """Thu tat ca vi tri can boi do trong bai goc.

    - Loi chinh ta: tim moi lan xuat hien cua ``loi_sai`` (khop hoa chu).
    - Loi kiem chung: chi lay ket qua la SAI / CHUA XAC MINH / CANH BAO...;
      tim ``thong_tin_can_check`` (khong thay thi thu doan trong dau ngoac kep).

    Returns
    -------
    list
        Danh sach ``(start, end, meta)`` da gop boi ``merge_spans``. Trong do
        ``meta`` co ``category`` va ``note`` (su dung cho title/ghi chu/Word).
    """
    text = original_text or ""
    spans: List[Tuple[int, int, Dict[str, Any]]] = []

    for error in spelling_errors or []:
        if not isinstance(error, dict):
            continue
        loi_sai = str(error.get("loi_sai") or "").strip()
        if not loi_sai:
            continue
        note = _spell_note(error)
        for start, end in _find_occurrences(text, loi_sai):
            spans.append(
                (start, end, {"category": CATEGORY_SPELLING, "note": note})
            )

    for fact in fact_checks or []:
        if not isinstance(fact, dict):
            continue
        if not is_negative_fact_result(fact.get("ket_qua_xac_minh")):
            continue
        mo_ta = str(fact.get("thong_tin_can_check") or "").strip()
        if not mo_ta:
            continue
        note = str(fact.get("ket_qua_xac_minh") or "").strip()

        matches = _find_occurrences(text, mo_ta)
        if not matches:
            # Mo ta khong khop nguyen can -> thu cac doan trich dan trong ngoac.
            for fragment in collect_quoted_fragments(mo_ta):
                matches.extend(_find_occurrences(text, fragment))
        for start, end in matches:
            spans.append(
                (start, end, {"category": CATEGORY_FACTCHECK, "note": note})
            )

    return merge_spans(spans)


def iter_segments(
    original_text: str,
    spans: Sequence[Tuple[int, int, Dict[str, Any]]],
) -> List[Tuple[str, Optional[Dict[str, Any]]]]:
    """Cat bai goc thanh cac doan ``(van_ban, meta)`` xen ke.

    ``meta`` la ``None`` cho doan binh thuong va la dict cua doan can danh dau.
    Tong cac doan van bang nguyen ban (giu nguyen 100% bo cuc).
    """
    text = original_text or ""
    segments: List[Tuple[str, Optional[Dict[str, Any]]]] = []
    cursor = 0
    for start, end, meta in spans:
        if start > cursor:
            segments.append((text[cursor:start], None))
        segments.append((text[start:end], meta))
        cursor = end
    if cursor < len(text):
        segments.append((text[cursor:], None))
    return segments


def segments_for_range(
    original_text: str,
    spans: Sequence[Tuple[int, int, Dict[str, Any]]],
    start: int,
    end: int,
) -> List[Tuple[str, Optional[Dict[str, Any]]]]:
    """Giong ``iter_segments`` nhung chi lay doan ``[start, end)`` (cat theo dong).

    Dung cho module xuat Word: moi dong duoc goi mot doan va chi cac doan danh
    dau bi cat de chay mau do tuong ung.
    """
    clipped: List[Tuple[int, int, Dict[str, Any]]] = []
    for span_start, span_end, meta in spans:
        lo = max(span_start, start)
        hi = min(span_end, end)
        if lo < hi:
            clipped.append((lo - start, hi - start, meta))
    return iter_segments((original_text or "")[start:end], clipped)


# ---------------------------------------------------------------------------
# API chinh
# ---------------------------------------------------------------------------


def to_display_html(original_text: str, spans: Sequence[Tuple[int, int, Dict[str, Any]]]) -> str:
    """Sinh HTML hien thi tren giao dien (the ``<mark>`` inline-style).

    Xuong dong trong bai goc duoc giu bang ``<br>`` de van de hien dung.
    """
    parts: List[str] = []
    for segment, meta in iter_segments(original_text, spans):
        escaped = html.escape(segment).replace("\n", "<br>\n")
        if meta is None:
            parts.append(escaped)
        else:
            note = html.escape(str(meta.get("note") or ""), quote=True)
            parts.append(
                f'<mark style="{DISPLAY_MARK_STYLE}" title="{note}">{escaped}</mark>'
            )
    return "".join(parts)


def to_rich_clipboard_html(
    original_text: str, spans: Sequence[Tuple[int, int, Dict[str, Any]]]
) -> str:
    """Sinh HTML inline-style don gian cho clipboard (Word / CMS VOV2).

    Chi dung the ``<span style="color: red; font-weight: bold; text-decoration:
    underline;">`` — khop chuan HTML cua Microsoft Word de khi paste chu lien
    tuc co mau do, dam, gach duoi (khong can dinh dang lai).
    """
    parts: List[str] = []
    for segment, meta in iter_segments(original_text, spans):
        escaped = html.escape(segment).replace("\n", "<br>\n")
        if meta is None:
            parts.append(escaped)
        else:
            parts.append(f'<span style="{CLIPBOARD_SPAN_STYLE}">{escaped}</span>')
    return "".join(parts)


def to_plain_text(
    original_text: str, spans: Sequence[Tuple[int, int, Dict[str, Any]]]
) -> str:
    """Ban van ban thuan: bai goc kem ghi chu ``[NGHI VẤN: ...]`` sau moi cum tu sai.

    Khong xoa ky tu nao trong bai goc — chi them ghi chu (dung cho ban in/phan
    tich nhanh khi khong can mau do).
    """
    parts: List[str] = []
    for segment, meta in iter_segments(original_text, spans):
        parts.append(segment)
        if meta is not None:
            note = str(meta.get("note") or "").strip()
            parts.append(f" {NOTE_PREFIX}{note}{NOTE_SUFFIX}")
    return "".join(parts)


def highlight_original_article(
    original_text: str,
    spelling_errors: Optional[Sequence[Dict[str, Any]]],
    fact_checks: Optional[Sequence[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Danh dau do cong cac diem nghi ngo sai tren ban thao goc.

    Parameters
    ----------
    original_text:
        Toan van bai bao goc (giu nguyen 100% bo cuc).
    spelling_errors:
        Danh sach loi chinh ta ``[{loi_sai, vi_tri, goi_y_sua, ly_do}, ...]``.
    fact_checks:
        Danh sach kiem chung ``[{thong_tin_can_check, ket_qua_xac_minh,
        trich_dan_nguon, do_tin_cay}, ...]`` — chi cac ket qua SAI / CHUA XAC
        MINH / CANH BAO... moi duoc boi do.

    Returns
    -------
    dict
        ``html`` (hien thi Streamlit), ``rich_clipboard_html`` (dan Word/CMS),
        ``plain_text`` (ban thuan kem ghi chu), ``error_count`` (so diem da
        danh dau) va ``spans`` (vi tri raw, dung cho module xuat Word).
    """
    spans = collect_error_spans(original_text, spelling_errors, fact_checks)
    text = original_text or ""
    return {
        "html": to_display_html(text, spans),
        "rich_clipboard_html": to_rich_clipboard_html(text, spans),
        "plain_text": to_plain_text(text, spans),
        "error_count": len(spans),
        "spans": [
            {"start": start, "end": end, **meta} for start, end, meta in spans
        ],
    }


__all__ = [
    "highlight_original_article",
    "collect_error_spans",
    "collect_quoted_fragments",
    "is_negative_fact_result",
    "iter_segments",
    "merge_spans",
    "segments_for_range",
    "strip_accents",
    "to_display_html",
    "to_plain_text",
    "to_rich_clipboard_html",
    "CATEGORY_SPELLING",
    "CATEGORY_FACTCHECK",
    "CLIPBOARD_SPAN_STYLE",
    "DISPLAY_MARK_STYLE",
    "NOTE_PREFIX",
]
