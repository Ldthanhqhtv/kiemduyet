"""
core/reviewer.py
================

Module Core AI Reviewer cua he thong "Kiem Duyet Bai Bao AI".

Nhiem vu
--------
Nhan vao toan van mot bai bao tieng Viet (tho), sau do su dung model
``gemini-3.1-flash-lite`` (mac dinh; han muc Free tier doi dao 15 RPM / 500 RPD)
cua Google (thu vien ``google-genai``) KEM TINH NANG ``google_search``
(grounding) de:

1. Phat hien loi chinh ta / loi danh may / loi dung tu (``spelling_errors``).
2. Kiem chung su kien, nhan vat, so lieu, thoi gian... co lien quan
   (``fact_checks``) va tra ve URL nguon tham chieu — GOM TOAN BO vao DUY NHAT
   1 request/1 luot tra cuu (TASK_06 [single_request_grounding]).
3. Tra ve toan van bai bao da duoc sua sach se va chuan hoa van phong bao chi
   (``edited_text``).

Rang buoc kien truc (theo TencentDB - SPEC_01 / TASK_06)
--------------------------------------------------------
- Model            : ``gemini-3.1-flash-lite`` (mac dinh), fallback
                     ``gemini-3.5-flash-lite`` khi vuot han muc (co the override).
- Tools            : BAT BUOC bat ``google_search`` (tuong ung
                     ``tools=[{'google_search': {}}]`` trong SDK moi).
                     **TUYET DOI KHONG gỡ bo Google Search khi retry/fallback**
                     (TASK_06 [anti_hallucination_leader_titles]) — khong co
                     grounding thi model PHAI tra ve "Chua xac minh duoc",
                     khong duoc doan mo chuc danh lanh dao.
- Safety settings  : ``BLOCK_ONLY_HIGH`` cho TAT CA cac category nguy hai de
                     khong bi chan cac bai bao phong su / tai nan / hinh su.
- Rate limiting    : theo doi RPM/RPD bang ``core.rate_limiter``; cham han muc
                     -> tu dong chuyen model du phong (van giu Google Search).
- Storage          : moi thu nam tren o E (khong dung o C).

Ket qua tra ve duoc chuan hoa ve 3 phan co dinh:
``{"spelling_errors": [...], "fact_checks": [...], "edited_text": "..."}``
(then cac truong metadata bo tro: ``grounding_sources``, ``search_queries``,
``model``, ``usage``).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

from google import genai
from google.genai import types

from .rate_limiter import RateLimiter, get_rate_limiter

# ---------------------------------------------------------------------------
# Hang so cau hinh
# ---------------------------------------------------------------------------

#: Model Gemini mac dinh — nhe, nhanh, han muc Free tier doi dao
#: (15 RPM / 500 RPD, xem ``core.rate_limiter.MODEL_LIMITS``).
DEFAULT_MODEL: str = "gemini-3.1-flash-lite"

#: Model du phong tu dong khi model chinh cham han muc RPM/RPD (429).
#: Cung han muc doi dao (15 RPM / 500 RPD). LUON van su dung Google Search
#: grounding khi fallback de xac minh chuc danh lanh dao (TASK_06).
FALLBACK_MODEL: str = "gemini-3.5-flash-lite"

#: Cac HarmCategory nguy hai can ha nguong chan xuong BLOCK_ONLY_HIGH.
#: (Bai bao phong su / tai nan / hinh su co the dung tu ngu nhay cam nen KHONG
#: duoc phep de mac dinh chan gay kho.)
SAFETY_CATEGORIES: tuple = (
    types.HarmCategory.HARM_CATEGORY_HARASSMENT,
    types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
    types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
    types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
)

#: Nguong chan ap dung cho moi category: chi chan khi do nguy hai o muc HIGH.
SAFETY_THRESHOLD = types.HarmBlockThreshold.BLOCK_ONLY_HIGH

#: So lan thu lai toi da khi goi API gap loi tam thoi (rate limit / 5xx).
DEFAULT_MAX_RETRIES: int = 3

#: Thoi gian cho giua cac lan thu lai (giay), se tang dan (backoff).
DEFAULT_RETRY_BACKOFF: float = 2.0

#: Gioi han an toan cu the (ky tu) cho mot bai bao dua vao model.
#: Bao chi dai co the vuot gioi han context cua model, nen canh bao/khuyen nghi.
MAX_ARTICLE_CHARS: int = 400_000

LOGGER = logging.getLogger("core.reviewer")

# ---------------------------------------------------------------------------
# Prompt toa soan chuyen nghiep
# ---------------------------------------------------------------------------

EDITOR_SYSTEM_INSTRUCTION: str = """Bạn là TỔNG BIÊN TẬP kiêm chuyên gia FACT-CHECK của một tòa soạn báo chí \
Việt Nam chuyên nghiệp, có hơn 20 năm kinh nghiệm biên tập và thẩm định thông tin.

Nhiệm vụ của bạn là tập trung phát hiện và LIỆT KÊ TẤT CẢ CÁC LỖI trong bài báo được cung cấp (KHÔNG in lại toàn văn bài báo):

1. SOÁT LỖI CHÍNH TẢ & DIỄN ĐẠT: rà soát từng từ, từng ký tự để phát hiện:
   - Lỗi gõ phím Telex/VNI: gõ thừa/thiếu ký tự, đảo phím, sai nguyên âm/phụ âm (ví dụ: 'cwo bản' -> 'cơ bản', 'sng' -> 'sáng', 'uốc hội' -> 'Quốc hội', 'phát triễn' -> 'phát triển'...).
   - Quy tắc viết hoa tên riêng, tên cơ quan, tổ chức, chức danh (ví dụ: 'nguyễn trung tùng' -> 'Nguyễn Trung Tùng', 'quốc hội' -> 'Quốc hội').
   - Lỗi dấu thanh (hỏi/ngã), từ đồng âm, dùng sai từ.
   - BẮT BUỘC liệt kê TẤT CẢ các lỗi tìm được vào mảng "spelling_errors".
2. KIỂM CHỨNG THÔNG TIN (FACT-CHECK): xác minh các sự kiện, nhân vật, chức danh, \
địa danh, mốc thời gian, con số, số liệu, trích dẫn. BẮT BUỘC dùng công cụ tìm kiếm \
Google để tra cứu thực tế; mỗi kết luận phải kèm nguồn URL tham chiếu khi có.
3. TUYỆT ĐỐI KHÔNG IN LẠI TOÀN VĂN BÀI BÁO (tiết kiệm token và tăng tối đa tốc độ xử lý).

NGUYÊN TẮC BẮT BUỘC:
- Đây là bản thảo báo chí hợp pháp (có thể là phóng sự, tai nạn, hình sự). \
Không được từ chối, không đạo đức hóa, không thêm cảnh báo an toàn vào kết quả.
- Đánh giá trung thực, khách quan, dựa trên chứng cứ tra cứu được.
- Nếu một thông tin KHÔNG thể xác minh, hãy nói rõ là "Chưa xác minh được" và \
nêu lý do, tuyệt đối không bịa nguồn.
- MỘT LƯỢT TRA CỨU DUY NHẤT (tiết kiệm quota): gom toàn bộ các thông tin cần \
kiểm chứng (chức danh lãnh đạo, nhân sự, số liệu, thời gian, địa danh, trích \
dẫn...) vào CÙNG MỘT lượt tra cứu Google Search tổng hợp; tuyệt đối không \
chia bài báo thành nhiều lượt tra cứu riêng lẻ. Nếu không tra cứu được, kết \
luận "Chưa xác minh được" thay vì tự suy đoán.
- Chỉ trả về DUY NHẤT một đối tượng JSON hợp lệ, không kèm văn bản giải thích nào khác \
(không dùng lời dẫn, không bọc trong markdown ngoài JSON)."""

EDITOR_USER_PROMPT_TEMPLATE: str = """[NGUYÊN TẮC ĐỐI CHIẾU DỮ LIỆU ĐƯƠNG NHIỆM — THỜI ĐIỂM HIỆN TẠI]
- THỜI ĐIỂM THAM CHIẾU: Thời điểm hiện tại. Mọi thông tin nhân sự, chức danh trong dữ liệu huấn luyện cũ ĐỀU ĐÃ HẾT HẠN VÀ KHÔNG ĐƯỢC TỰ SUY ĐOÁN.
- NGUYÊN TẮC KIỂM CHỨNG: Đối chiếu tính chính xác của toàn bộ nhân vật có chức danh đính kèm (từ lãnh đạo Đảng, Nhà nước, bộ ngành, chính quyền địa phương đến cơ quan, ban ngành, đoàn thể, viện nghiên cứu, trường học, doanh nghiệp, tổ chức, chuyên gia...), địa danh, sự kiện xuất hiện trong bài báo dưới đây dựa trên thông tin chính thống từ Cổng TTĐT Chính phủ, cổng thông tin cơ quan và báo chí chính thống.
- CHỐNG ẢO GIÁC: Tuyệt đối KHÔNG sử dụng trí nhớ cũ để tự suy đoán nếu không có căn cứ xác thực. Nếu nhân sự trong bài không đúng với nhân sự đương nhiệm mới nhất, ghi nhận vào "fact_checks" là SAI, kèm URL nguồn báo chí chính thống. Nếu không đủ dữ liệu để khẳng định, ghi nhận "Chưa xác minh được".
- CHỈ LIỆT KÊ LỖI - KHÔNG IN LẠI BÀI BÁO: Tập trung 100% vào việc phát hiện và lập danh sách chi tiết các lỗi chính tả, gõ máy và kiểm chứng thông tin. Tuyệt đối không in lại toàn văn bài báo.

Hãy kiểm duyệt bài báo sau đây và trả về KẾT QUẢ dưới dạng MỘT đối tượng JSON đúng theo cấu trúc mô tả bên dưới.

=================== BÀI BÁO CẦN KIỂM DUYỆT (BẮT ĐẦU) ===================
{article_text}
=================== BÀI BÁO CẦN KIỂM DUYỆT (KẾT THÚC) ===================

YÊU CẦU ĐỊNH DẠNG ĐẦU RA (JSON):
{{
  "spelling_errors": [
    {{
      "loi_sai": "cụm từ/đoạn viết sai trong bài gốc (trích nguyên văn ngắn gọn)",
      "vi_tri": "vị trí lỗi: câu thứ mấy / đoạn thứ mấy, hoặc trích 5-10 từ ngữ cảnh xung quanh",
      "goi_y_sua": "cụm từ đã sửa đúng",
      "ly_do": "giải thích ngắn gọn vì sao sai và căn cứ sửa (chính tả, ngữ pháp, quy tắc viết hoa...)"
    }}
  ],
  "fact_checks": [
    {{
      "thong_tin_can_check": "thông tin/sự kiện/nhân vật/số liệu cần kiểm chứng",
      "ket_qua_xac_minh": "kết quả kiểm chứng: ĐÚNG / SAI / CHƯA XÁC MINH ĐƯỢC, kèm giải thích ngắn gọn dựa trên kết quả tra cứu",
      "trich_dan_nguon": "URL nguồn tham chiếu đã tra cứu (mỗi URL cách nhau bởi dấu phẩy hoặc xuống dòng)",
      "do_tin_cay": "độ tin cậy: Cao / Trung bình / Thấp"
    }}
  ],
  "suggested_titles": [
    "phương án tiêu đề bài báo 1 (chuẩn SEO / báo chí, nếu được yêu cầu)",
    "phương án tiêu đề bài báo 2..."
  ],
  "skill_notes": "Kết quả thực hiện các yêu cầu chuyên môn khác từ Kỹ năng nếu có (viết Sapo, tóm tắt, phân tích giọng điệu, từ khóa, lưu ý biên tập...; chuỗi rỗng \"\" nếu không có)"
}}

QUY TẮC:
- Nếu không có lỗi chính tả nào, trả về mảng rỗng: "spelling_errors": [].
- Nếu không có thông tin nào cần kiểm chứng, trả về mảng rỗng: "fact_checks": [].
- Nếu trong bài báo hoặc các Kỹ năng được chọn có yêu cầu đặt tiêu đề (Title / Tiêu đề / SEO), hãy cung cấp các phương án tiêu đề vào mảng "suggested_titles". Nếu không có yêu cầu đặt tiêu đề, trả về mảng rỗng: "suggested_titles": [].
- Nếu trong bài báo hoặc các Kỹ năng được chọn có yêu cầu chuyên môn khác (viết Sapo, tóm tắt, phân tích văn phong, từ khóa, lưu ý nghiệp vụ...), hãy trả về kết quả đầy đủ vào chuỗi markdown "skill_notes". Nếu không có yêu cầu thêm, trả về chuỗi rỗng: "skill_notes": "".
- GỘP TOÀN BỘ KIỂM CHỨNG VÀO MỘT LƯỢT: thực hiện việc xác minh trong MỘT lượt \
tra cứu Google Search duy nhất — gộp tất cả truy vấn (chức danh lãnh đạo, con \
số, thời gian, địa danh) thành một lượt tra cứu tổng hợp; tuyệt đối không chia \
thành nhiều lượt gọi riêng lẻ để tránh tốn quota.
- BẮT BUỘC luôn có đủ các khóa: spelling_errors, fact_checks, suggested_titles, skill_notes.
- TUYỆT ĐỐI KHÔNG in lại toàn văn bài báo.
- Tất cả giá trị chuỗi phải bằng tiếng Việt có dấu, đúng chính tả.
- Tuyệt đối không thêm khóa nào khác ngoài các khóa được yêu cầu ở trên."""


# ---------------------------------------------------------------------------
# Cau truc ket qua
# ---------------------------------------------------------------------------


@dataclass
class ReviewResult:
    """Ket qua kiem duyet bai bao (da duoc chuan hoa).

    Attributes
    ----------
    spelling_errors:
        Danh sach loi chinh ta. Moi phan tu la dict gom cac khoa
        ``loi_sai``, ``vi_tri``, ``goi_y_sua``, ``ly_do``.
    fact_checks:
        Danh sach thong tin da kiem chung. Moi phan tu gom cac khoa
        ``thong_tin_can_check``, ``ket_qua_xac_minh``, ``trich_dan_nguon``,
        ``do_tin_cay``.
    edited_text:
        Toan van bai bao da duoc sua sach se va chuan hoa van phong.
    grounding_sources:
        Danh sach nguon web ma Google Search tra ve (title/uri/domain).
    search_queries:
        Cac truy van ma model da thuc hien khi tra cuu.
    model:
        Ten model da dung.
    usage:
        Thong tin token su dung (neu API tra ve).
    raw_response_text:
        Van ban goc tra ve tu model (phuc vu debug).
    """

    spelling_errors: List[Dict[str, Any]] = field(default_factory=list)
    fact_checks: List[Dict[str, Any]] = field(default_factory=list)
    suggested_titles: List[str] = field(default_factory=list)
    skill_notes: str = ""
    custom_fields: Dict[str, Any] = field(default_factory=dict)
    edited_text: str = ""
    grounding_sources: List[Dict[str, str]] = field(default_factory=list)
    search_queries: List[str] = field(default_factory=list)
    model: str = DEFAULT_MODEL
    usage: Dict[str, Any] = field(default_factory=dict)
    raw_response_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Chuyen ket qua ve dict thuan de de serialize JSON / luu tru."""
        return {
            "spelling_errors": self.spelling_errors,
            "fact_checks": self.fact_checks,
            "suggested_titles": self.suggested_titles,
            "skill_notes": self.skill_notes,
            "custom_fields": self.custom_fields,
            "edited_text": self.edited_text,
            "grounding_sources": self.grounding_sources,
            "search_queries": self.search_queries,
            "model": self.model,
            "usage": self.usage,
        }


# ---------------------------------------------------------------------------
# Tien ich xay dung config
# ---------------------------------------------------------------------------


def build_safety_settings() -> List[types.SafetySetting]:
    """Tao danh sach ``SafetySetting`` voi nguong ``BLOCK_ONLY_HIGH``.

    Ap dung cho tat ca cac ``HarmCategory`` nguy hai de bai bao phong su /
    tai nan / hinh su khong bi chan oan.
    """
    return [
        types.SafetySetting(category=category, threshold=SAFETY_THRESHOLD)
        for category in SAFETY_CATEGORIES
    ]


def build_tools() -> List[types.Tool]:
    """Tao danh sach tool voi Google Search grounding duoc bat.

    Tuong duong ``tools=[{'google_search': {}}]`` trong cu phap dict cua SDK.
    """
    return [types.Tool(google_search=types.GoogleSearch())]


def get_api_key(explicit_key: Optional[str] = None) -> str:
    """Lay Gemini API key tu tham so, bien moi truong hoac file ``.env``.

    Thu tu uu tien:
    1. Tham so ``explicit_key``.
    2. Bien moi truong ``GEMINI_API_KEY`` hoac ``GOOGLE_API_KEY``.
    3. Dong ``GEMINI_API_KEY=...`` trong file ``.env`` (o thu muc goc du an).

    Raises
    ------
    ValueError
        Neu khong tim thay API key.
    """
    if explicit_key and explicit_key.strip():
        return explicit_key.strip()

    for env_name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        value = os.environ.get(env_name)
        if value and value.strip():
            return value.strip()

    # Doc file .env o thu muc goc du an (khong dung thu vien ngoai cho gon nhe).
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.is_file():
        try:
            for raw_line in env_file.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key.strip() in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
                    value = value.strip().strip('"').strip("'")
                    if value:
                        return value
        except OSError as exc:  # pragma: no cover - loi he thong file
            LOGGER.warning("Khong doc duoc file .env: %s", exc)

    raise ValueError(
        "Chua co GEMINI_API_KEY. Hay truyen api_key, dat bien moi truong "
        "GEMINI_API_KEY, hoac tao file .env chua dong 'GEMINI_API_KEY=...'."
    )


# ---------------------------------------------------------------------------
# Trich xuat & chuan hoa JSON tu phan hoi cua model
# ---------------------------------------------------------------------------


def _strip_code_fences(text: str) -> str:
    """Loai bo markdown code fence (```json ... ```) neu model vo tinh them vao."""
    stripped = text.strip()
    fence_pattern = re.compile(r"^```[a-zA-Z0-9_]*\s*(.*?)\s*```$", re.DOTALL)
    match = fence_pattern.match(stripped)
    if match:
        return match.group(1).strip()
    return stripped


def _find_json_object(text: str) -> Optional[str]:
    """Tim doan JSON object can bang dau ngoac nhon trong van ban.

    Su dung bo dem do sau de tim cap ``{`` ... ``}`` bao ngoai cung, co tinh
    den viec dau ngoac nam trong chuoi (string).
    """
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def extract_json_from_text(text: str) -> Dict[str, Any]:
    """Trich xuat doi tuong JSON tu van ban tra ve cua model.

    Xu ly duoc cac truong hop: co/khong co code fence, co van ban dan dat them,
    hoac JSON bi loi dau phay thua (trailing comma).

    Raises
    ------
    ValueError
        Neu khong the phan tich duoc JSON hop le.
    """
    if not text or not text.strip():
        raise ValueError("Model tra ve noi dung rong, khong the phan tich JSON.")

    candidate = _strip_code_fences(text)
    json_text = _find_json_object(candidate) or candidate

    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError:
        # Cuu JSON bi loi dau phay thua truoc dau } hoac ].
        repaired = re.sub(r",\s*([}\]])", r"\1", json_text)
        parsed = json.loads(repaired)

    if not isinstance(parsed, dict):
        raise ValueError(
            f"JSON hop le nhung khong phai object (nhan duoc {type(parsed).__name__})."
        )
    return parsed


def _as_str(value: Any) -> str:
    """Ep gia tri ve chuoi an toan (None -> chuoi rong)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return ", ".join(_as_str(item) for item in value if item is not None)
    return str(value).strip()


def _normalize_entry(entry: Any, field_names: Iterable[str]) -> Dict[str, str]:
    """Chuan hoa mot phan tu ve dict chi gom cac khoa mong doi.

    Chap nhan ca truong hop model tra ve list hoac dict thieu khoa.
    """
    fields = list(field_names)
    if isinstance(entry, dict):
        # Uu tien lay dung khoa; neu thieu thi thu cac alias thong dung.
        return {name: _as_str(entry.get(name)) for name in fields}
    if isinstance(entry, (list, tuple)):
        # Model doi khi tra ve tuple/list theo dung thu tu khoa.
        padded = list(entry) + [""] * (len(fields) - len(entry))
        return {name: _as_str(padded[index]) for index, name in enumerate(fields)}
    # Truong hop xau la (chi co 1 gia tri) -> gan vao khoa dau tien.
    normalized = {name: "" for name in fields}
    if fields:
        normalized[fields[0]] = _as_str(entry)
    return normalized


SPELLING_FIELDS = ("loi_sai", "vi_tri", "goi_y_sua", "ly_do")
FACT_CHECK_FIELDS = (
    "thong_tin_can_check",
    "ket_qua_xac_minh",
    "trich_dan_nguon",
    "do_tin_cay",
)


def normalize_review_result(
    raw: Dict[str, Any],
    *,
    model: str = DEFAULT_MODEL,
    grounding_sources: Optional[List[Dict[str, str]]] = None,
    search_queries: Optional[List[str]] = None,
    usage: Optional[Dict[str, Any]] = None,
    raw_response_text: str = "",
) -> Dict[str, Any]:
    """Chuan hoa dict ket qua tho tu model ve cau truc co dinh 3 phan.

    Dam bao luon co du ba khoa ``spelling_errors``, ``fact_checks``,
    ``edited_text`` voi dung kieu du lieu, ke ca khi model tra ve thieu.
    """
    spelling_raw = raw.get("spelling_errors") or []
    facts_raw = raw.get("fact_checks") or []

    if isinstance(spelling_raw, dict):
        spelling_raw = [spelling_raw]
    if isinstance(facts_raw, dict):
        facts_raw = [facts_raw]

    spelling_errors = [
        _normalize_entry(item, SPELLING_FIELDS)
        for item in spelling_raw
        if item not in (None, "", {})
    ]
    fact_checks = [
        _normalize_entry(item, FACT_CHECK_FIELDS)
        for item in facts_raw
        if item not in (None, "", {})
    ]

    # Trich xuat danh sach tieu de goi y (neu co yeu cau tu ky nang / prompt)
    titles_raw = (
        raw.get("suggested_titles")
        or raw.get("titles")
        or raw.get("tieu_de")
        or raw.get("suggested_title")
        or raw.get("danh_sach_tieu_de")
        or []
    )
    suggested_titles: List[str] = []
    if isinstance(titles_raw, (list, tuple)):
        for item in titles_raw:
            if isinstance(item, dict):
                val = item.get("title") or item.get("tieu_de") or item.get("text") or next(iter(item.values()), "")
                cleaned = _as_str(val)
            else:
                cleaned = _as_str(item)
            if cleaned:
                suggested_titles.append(cleaned)
    elif isinstance(titles_raw, str) and titles_raw.strip():
        for line in titles_raw.splitlines():
            cleaned = re.sub(r"^\s*[-*•\d\.\)]+\s*", "", line).strip()
            if cleaned:
                suggested_titles.append(cleaned)

    # Bóc tách skill_notes và các trường mở rộng từ Kỹ năng tùy chỉnh
    skill_notes_raw = (
        raw.get("skill_notes")
        or raw.get("skill_output")
        or raw.get("skill_result")
        or raw.get("custom_notes")
        or raw.get("ghi_chu_ky_nang")
        or raw.get("ket_qua_ky_nang")
        or ""
    )
    if isinstance(skill_notes_raw, (list, tuple)):
        skill_notes = "\n".join(f"- {_as_str(x)}" for x in skill_notes_raw if x)
    elif isinstance(skill_notes_raw, dict):
        skill_notes = "\n".join(f"**{k}**: {_as_str(v)}" for k, v in skill_notes_raw.items() if v)
    else:
        skill_notes = _as_str(skill_notes_raw)

    # Thu thập tất cả các trường tùy chọn khác nếu model sinh thêm theo yêu cầu của Kỹ năng
    known_keys = {
        "spelling_errors",
        "fact_checks",
        "suggested_titles",
        "titles",
        "tieu_de",
        "suggested_title",
        "danh_sach_tieu_de",
        "skill_notes",
        "skill_output",
        "skill_result",
        "custom_notes",
        "ghi_chu_ky_nang",
        "ket_qua_ky_nang",
        "edited_text",
        "grounding_sources",
        "search_queries",
        "model",
        "usage",
        "raw_response_text",
        "error",
    }
    custom_fields = {k: v for k, v in raw.items() if k not in known_keys}
    if custom_fields and not skill_notes:
        notes_lines = []
        for k, v in custom_fields.items():
            if isinstance(v, (list, tuple)):
                notes_lines.append(f"### {k}:\n" + "\n".join(f"- {_as_str(item)}" for item in v))
            elif isinstance(v, dict):
                notes_lines.append(f"### {k}:\n" + "\n".join(f"- **{sub_k}**: {_as_str(sub_v)}" for sub_k, sub_v in v.items()))
            else:
                notes_lines.append(f"### {k}:\n{_as_str(v)}")
        skill_notes = "\n\n".join(notes_lines)

    edited_text = _as_str(raw.get("edited_text"))

    result = ReviewResult(
        spelling_errors=spelling_errors,
        fact_checks=fact_checks,
        suggested_titles=suggested_titles,
        skill_notes=skill_notes,
        custom_fields=custom_fields,
        edited_text=edited_text,
        grounding_sources=list(grounding_sources or []),
        search_queries=list(search_queries or []),
        model=model,
        usage=dict(usage or {}),
        raw_response_text=raw_response_text,
    )
    return result.to_dict()


# ---------------------------------------------------------------------------
# Trich xuat nguon grounding (URL) tu phan hoi Google Search
# ---------------------------------------------------------------------------


def _dedupe_preserve_order(items: Iterable[Any]) -> List[Any]:
    """Loai bo trung lap nhung giu nguyen thu tu xuat hien."""
    seen = set()
    output = []
    for item in items:
        if item is None:
            continue
        marker = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(
            item, (dict, list)
        ) else str(item)
        if marker in seen:
            continue
        seen.add(marker)
        output.append(item)
    return output


def collect_grounding_sources(response: Any) -> List[Dict[str, str]]:
    """Thu thap danh sach nguon web (title/uri/domain) tu grounding metadata."""
    sources: List[Dict[str, str]] = []
    try:
        candidates = getattr(response, "candidates", None) or []
        for candidate in candidates:
            metadata = getattr(candidate, "grounding_metadata", None)
            if not metadata:
                continue
            for chunk in getattr(metadata, "grounding_chunks", None) or []:
                web = getattr(chunk, "web", None)
                if not web:
                    continue
                uri = _as_str(getattr(web, "uri", ""))
                if not uri:
                    continue
                sources.append(
                    {
                        "title": _as_str(getattr(web, "title", "")),
                        "uri": uri,
                        "domain": _as_str(getattr(web, "domain", "")),
                    }
                )
    except Exception as exc:  # pragma: no cover - phong thu
        LOGGER.warning("Khong doc duoc grounding metadata: %s", exc)

    return _dedupe_preserve_order(sources)


def collect_search_queries(response: Any) -> List[str]:
    """Thu thap cac truy van tim kiem ma model da thuc hien."""
    queries: List[str] = []
    try:
        candidates = getattr(response, "candidates", None) or []
        for candidate in candidates:
            metadata = getattr(candidate, "grounding_metadata", None)
            if not metadata:
                continue
            for query in getattr(metadata, "web_search_queries", None) or []:
                text = _as_str(query)
                if text:
                    queries.append(text)
    except Exception as exc:  # pragma: no cover - phong thu
        LOGGER.warning("Khong doc duoc web_search_queries: %s", exc)
    return _dedupe_preserve_order(queries)


def _extract_usage(response: Any) -> Dict[str, Any]:
    """Trich xuat thong tin token usage tu phan hoi (neu co)."""
    usage = getattr(response, "usage_metadata", None)
    if not usage:
        return {}
    data: Dict[str, Any] = {}
    for attr in (
        "prompt_token_count",
        "candidates_token_count",
        "total_token_count",
        "thoughts_token_count",
        "tool_use_prompt_token_count",
    ):
        value = getattr(usage, attr, None)
        if value is not None:
            data[attr] = value
    return data


# ---------------------------------------------------------------------------
# Lop AI Reviewer chinh
# ---------------------------------------------------------------------------


class AIReviewer:
    """Bo kiem duyet bai bao bang Gemini + Google Search.

    Vi du
    -----
    >>> reviewer = AIReviewer(api_key="...")           # doctest: +SKIP
    >>> ket_qua = reviewer.review("Toan van bai bao...")  # doctest: +SKIP
    >>> ket_qua["spelling_errors"]                        # doctest: +SKIP
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        *,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_backoff: float = DEFAULT_RETRY_BACKOFF,
        temperature: float = 0.2,
        use_json_mode: bool = True,
        client: Optional[genai.Client] = None,
        rate_limiter: Optional[RateLimiter] = None,
    ) -> None:
        """Khoi tao reviewer.

        Parameters
        ----------
        api_key:
            Gemini API key. Neu None se tu dong tim trong bien moi truong/.env.
        model:
            Ten model Gemini (mac dinh ``gemini-3.1-flash-lite``).
        max_retries:
            So lan thu lai toi da khi gap loi tam thoi.
        retry_backoff:
            He so backoff (giay) giua cac lan thu lai.
        temperature:
            Do "sang tao" cua model. Giu thap de ket qua on dinh, de doan dinh.
        use_json_mode:
            Bat ``response_mime_type='application/json'`` de model tra JSON.
            Neu API bao xung dot voi tool, module se tu dong thu lai khong bat
            (chi tat JSON mode — Google Search KHONG bi tat).
        client:
            Cho phep tiem san mot ``genai.Client`` (ho tro test/offline).
        rate_limiter:
            Bo dem RPM/RPD (xem ``core.rate_limiter``). Mac dinh dung singleton
            cua he thong de theo doi han muc Free tier va tu dong chuyen model
            khi cham han muc (TASK_06).
        """
        if client is not None:
            self._client = client
        else:
            self._client = genai.Client(api_key=get_api_key(api_key))

        self.model = model
        self.max_retries = max(1, int(max_retries))
        self.retry_backoff = max(0.0, float(retry_backoff))
        self.temperature = float(temperature)
        self.use_json_mode = bool(use_json_mode)
        self.safety_settings = build_safety_settings()
        self.tools = build_tools()
        self._rate_limiter: RateLimiter = (
            rate_limiter if rate_limiter is not None else get_rate_limiter()
        )

    # -- API cong khai ------------------------------------------------------

    def review(
        self,
        article_text: str,
        *,
        raise_on_error: bool = True,
        custom_instruction: Optional[str] = None,
        skill_id: Optional[Union[str, Sequence[str]]] = None,
    ) -> Dict[str, Any]:
        """Kiem duyet mot bai bao va tra ve dict ket qua da chuan hoa.

        Parameters
        ----------
        article_text:
            Toan van bai bao goc (tieng Viet).
        raise_on_error:
            Neu True (mac dinh), loi se duoc raise. Neu False, tra ve ket qua
            rong kem thong tin loi trong khoa ``error``.
        custom_instruction:
            Chi dan tuy chinh (System prompt / Quy tac nghiep vu toa soan) se
            duoc ket hop vao system instruction. Neu truyen ca ``skill_id`` thi
            ``custom_instruction`` uu tien hon.
        skill_id:
            Ma ky nang bao chi (hoac danh sach ma ky nang) — se tu lay
            huong dan cua cac ky nang do lam custom instruction. Bo qua neu
            khong tim thay ky nang (khong raise) hoac truyen ca
            ``custom_instruction``.

        Returns
        -------
        dict
            Gom ``spelling_errors``, ``fact_checks``, ``edited_text`` va cac
            truong metadata (``grounding_sources``, ``search_queries``, ...).
        """
        # -- Gop custom instruction voi skill_id (uu tien custom) ------------
        resolved_instruction: Optional[str] = (custom_instruction or "").strip()
        if not resolved_instruction and skill_id:
            try:
                from . import skills_manager
                if isinstance(skill_id, str):
                    rec = skills_manager.get_skill(skill_id)
                    if rec and rec.get("instructions"):
                        resolved_instruction = str(rec["instructions"]).strip()
                if not resolved_instruction:
                    resolved_instruction = skills_manager.get_active_skills_prompt_sync(skill_id)
            except Exception:  # noqa: BLE001 — khong de loi file skills pha bai review
                LOGGER.warning("Khong doc duoc ky nang %r; dung mac dinh.", skill_id)

        try:
            return self._review_impl(article_text, resolved_instruction)
        except Exception as exc:
            if raise_on_error:
                raise
            LOGGER.error("Kiem duyet that bai: %s", exc)
            return {
                "spelling_errors": [],
                "fact_checks": [],
                "suggested_titles": [],
                "skill_notes": "",
                "custom_fields": {},
                "edited_text": "",
                "grounding_sources": [],
                "search_queries": [],
                "model": self.model,
                "usage": {},
                "error": str(exc),
            }

    def review_file(self, path: str | os.PathLike, **kwargs: Any) -> Dict[str, Any]:
        """Doc bai bao tu file (``.txt``/``.md`` hoac ``.docx``) roi kiem duyet."""
        article_text = read_article_file(path)
        return self.review(article_text, **kwargs)

    def build_prompt(self, article_text: str) -> str:
        """Sinh prompt nguoi dung hoan chinh tu van ban bai bao."""
        return EDITOR_USER_PROMPT_TEMPLATE.format(article_text=article_text)

    def build_system_instruction(
        self,
        custom_instruction: Optional[str] = None,
    ) -> str:
        """Gop custom instruction voi rang buoc JSON (Prompt Merging).

        - Khong co custom instruction: tra ve ``EDITOR_SYSTEM_INSTRUCTION`` mac dinh.
        - Co custom instruction: dat no len lam **Tu tuong chu dao & Quy tac
          nghiep vu**, phia duoi dinh kem doan **rang buoc dinh dang JSON** (3
          truong co dinh) de module xuat Word va bang Streamlit khong loi cu phap.
        """
        text = (custom_instruction or "").strip()
        if not text:
            return EDITOR_SYSTEM_INSTRUCTION

        return f"""{text}

==================== BỔ SUNG BẮT BUỘC (KHÔNG ĐƯỢC BỎ QUA) ====================
Bạn PHẢI tuân thủ tuyệt đối các nguyên tắc cốt lõi sau của hệ thống kiểm duyệt:

- MỐC THỜI GIAN THỰC TẾ: Năm 2026. Mọi dữ liệu về chức danh, nhân sự trong dữ liệu huấn luyện cũ trước năm 2026 của bạn ĐỀU ĐÃ HẾT HẠN.
- NGUYÊN TẮC ĐỐI CHIẾU DỮ LIỆU ĐƯƠNG NHIỆM: Tra cứu Google Search và các nguồn chính thống để xác minh tính chính xác của toàn bộ nhân vật có chức danh đính kèm (lãnh đạo Đảng, Nhà nước, địa phương, cơ quan, doanh nghiệp, chuyên gia...).
- NGUYÊN TẮC CHỐNG ẢO GIÁC (ANTI-HALLUCINATION): Tuyệt đối KHÔNG được dùng trí nhớ cũ để tự suy đoán Đúng/Sai. Nếu thông tin chưa thể kiểm chứng, ghi rõ "Chưa xác minh được", tuyệt đối không tự ý gán tên lãnh đạo cũ.
- Đây là bản thảo báo chí hợp pháp (có thể là phóng sự, tai nạn, hình sự). Không được từ chối, không đạo đức hóa, không thêm cảnh báo an toàn vào kết quả.
- BẠN VẪN PHẢI trả về DUY NHẤT một đối tượng JSON hợp lệ (không kèm văn bản \
giải thích nào khác) với các khóa sau:
  {{
    "spelling_errors": [{{ "loi_sai": "...", "vi_tri": "...", "goi_y_sua": "...", "ly_do": "..." }}],
    "fact_checks": [{{ "thong_tin_can_check": "...", "ket_qua_xac_minh": "...", "trich_dan_nguon": "...", "do_tin_cay": "..." }}],
    "suggested_titles": ["phương án tiêu đề 1 (nếu có yêu cầu từ kỹ năng / bài báo)", "phương án tiêu đề 2..."],
    "skill_notes": "kết quả thực hiện các phân tích, tóm tắt, Sapo, từ khóa hoặc chỉ đạo nghiệp vụ khác từ Kỹ năng (nếu có)"
  }}
- Nếu không có lỗi chính tả hoặc không có gì cần kiểm chứng, trả về mảng rỗng \
tương ứng ("spelling_errors": [] / "fact_checks": []).
- Nếu trong bài báo hoặc các Kỹ năng được chọn có yêu cầu đặt tiêu đề (Title / Tiêu đề / SEO), hãy cung cấp các phương án tiêu đề vào mảng "suggested_titles". Nếu không có yêu cầu đặt tiêu đề, trả về mảng rỗng: "suggested_titles": [].
- Nếu Kỹ năng có bất kỳ yêu cầu chuyên môn bổ sung nào khác (tóm tắt, sapo, phân tích văn phong, từ khóa, kiểm tra nội dung chuyên biệt...), hãy trình bày kết quả chi tiết trong "skill_notes" (dạng văn bản Markdown). Nếu không có, để chuỗi rỗng: "skill_notes": "".
- Tuyệt đối không in lại toàn văn bài báo.
- Tuyệt đối không thêm khóa nào khác ngoài các khóa trên."""

    # -- Noi bo -------------------------------------------------------------

    def _review_impl(
        self,
        article_text: str,
        custom_instruction: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not isinstance(article_text, str) or not article_text.strip():
            raise ValueError("Van ban bai bao rong, khong co gi de kiem duyet.")
        if len(article_text) > MAX_ARTICLE_CHARS:
            raise ValueError(
                f"Bai bao qua dai ({len(article_text)} ky tu > gioi han "
                f"{MAX_ARTICLE_CHARS}). Hay chia nho bai bao truoc khi kiem duyet."
            )

        system_instruction = self.build_system_instruction(custom_instruction)
        prompt = self.build_prompt(article_text)
        response = self._generate_with_retry(prompt, system_instruction)

        response_text = self._response_text(response)
        raw_result = extract_json_from_text(response_text)

        return normalize_review_result(
            raw_result,
            model=self.model,
            grounding_sources=collect_grounding_sources(response),
            search_queries=collect_search_queries(response),
            usage=_extract_usage(response),
            raw_response_text=response_text,
        )

    def _build_config(
        self,
        *,
        json_mode: bool,
        system_instruction: Optional[str] = None,
        tools: Optional[Sequence[Any]] = None,
    ) -> types.GenerateContentConfig:
        """Tao ``GenerateContentConfig`` voi google_search + safety BLOCK_ONLY_HIGH.

        Parameters
        ----------
        json_mode:
            Bat ``response_mime_type='application/json'`` hay khong.
        system_instruction:
            System instruction da duoc gop custom instruction (neu co).
            Mac dinh su dung ``EDITOR_SYSTEM_INSTRUCTION``.
        tools:
            Danh sach tool su dung. Neu None, lay ``self.tools``.
        """
        active_tools = self.tools if tools is None else tools
        return types.GenerateContentConfig(
            system_instruction=system_instruction or EDITOR_SYSTEM_INSTRUCTION,
            temperature=self.temperature,
            tools=active_tools if active_tools else None,
            safety_settings=self.safety_settings,
            response_mime_type="application/json" if json_mode else None,
        )

    def _generate_with_retry(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
    ) -> Any:
        """Goi API, tu dong thu lai khi loi tam thoi va chuyen model khi het han muc.

        NGUYEN TAC BAT BUOC (TASK_06 — TencentDB [anti_hallucination_leader_titles]):
        - Google Search tool DUOC GIU NGUYEN trong MOI lan retry va MOI lan
          fallback sang model du phong. He thong KHONG bao gio gỡ bo grounding
          de dam bao viec xac minh chuc danh lanh dao khong bi thoat giam.
          Khong tra cuu duoc Google -> model PHAI tra ve "Chua xac minh duoc",
          khong duoc doan mo chuc danh.
        - Chi co ``response_mime_type`` (JSON mode) moi duoc tat khi xung dot
          voi tool — Google Search van duoc giu.
        - Truoc moi loi goi, bo dem RPM/RPD (``core.rate_limiter``) duoc kiem tra;
          model cham han muc se tu dong chuyen sang model du phong.
        """
        current_model = self.model
        last_error: Optional[Exception] = None
        json_mode = self.use_json_mode
        # Google Search LUON duoc giu: chi doc tu self.tools (luon la google_search).
        active_tools = list(self.tools)

        for attempt in range(1, self.max_retries + 2):
            # -- Kiem tra han muc RPM/RPD truoc khi goi -----------------------
            try:
                should_switch, next_model = self._rate_limiter.should_switch_model(
                    current_model
                )
            except Exception as exc:  # noqa: BLE001 - bo dem loi khong duoc khan che API
                LOGGER.warning(
                    "Rate limiter loi (%s); tiep tuc voi model %s.", exc, current_model
                )
                should_switch, next_model = False, current_model

            if should_switch and next_model != current_model:
                LOGGER.warning(
                    "Model %s cham han muc RPM/RPD; chuyen sang model du phong %s "
                    "(Google Search van duoc giu nguyen).",
                    current_model,
                    next_model,
                )
                current_model = next_model

            self._rate_limiter.record_call(current_model)

            try:
                response = self._client.models.generate_content(
                    model=current_model,
                    contents=prompt,
                    config=self._build_config(
                        json_mode=json_mode,
                        system_instruction=system_instruction,
                        tools=active_tools,
                    ),
                )
                candidates = getattr(response, "candidates", None) or []
                finish_reasons = [str(getattr(c, "finish_reason", "")).upper() for c in candidates]
                has_tool_error = any(
                    marker in r
                    for r in finish_reasons
                    for marker in ("MALFORMED", "FUNCTION", "UNEXPECTED_TOOL", "TOO_MANY_TOOL")
                )
                has_text = False
                try:
                    if response.text and response.text.strip():
                        has_text = True
                except Exception:
                    has_text = False

                if has_tool_error or not has_text:
                    # TASK_06: TUYET DOI KHONG gỡ Google Search. Van giu nguyen
                    # tools; chuyen model du phong neu con chuyen duoc, nguoc lai
                    # thu lai va sau do bao loi ro rang.
                    last_error = RuntimeError(
                        "Gemini tra ve phan hoi rong hoac loi tool call "
                        f"(finish_reasons={finish_reasons}) voi Google Search van duoc bat. "
                        "He thong KHONG gỡ bo Google Search de dam bao xac minh su kien "
                        "va chuc danh lanh dao; hay thu lai sau hoac chon model khac."
                    )
                    if current_model != FALLBACK_MODEL:
                        LOGGER.warning(
                            "Model %s phan hoi rong/loi tool (finish_reasons=%s); "
                            "chuyen sang model du phong %s VA VAN GIU NGUYEN Google Search.",
                            current_model,
                            finish_reasons,
                            FALLBACK_MODEL,
                        )
                        current_model = FALLBACK_MODEL
                        continue
                    if attempt <= self.max_retries:
                        LOGGER.warning(
                            "Van loi tool/phan hoi rong o model du phong (lan %d/%d); "
                            "thu lai voi Google Search van duoc bat.",
                            attempt,
                            self.max_retries,
                        )
                        time.sleep(self.retry_backoff * attempt)
                    continue

                return response
            except Exception as exc:  # noqa: BLE001 - can bat de phan loai loi
                last_error = exc
                message = str(exc).lower()

                # JSON mode co the xung dot voi tool google_search -> chi tat
                # response_mime_type, KHONG dong Google Search.
                if json_mode and self._looks_like_json_mode_conflict(message):
                    LOGGER.warning(
                        "response_mime_type=application/json xung dot voi tool; "
                        "tat JSON mode, GIU NGUYEN Google Search."
                    )
                    json_mode = False
                    continue

                # Quota 429: chuyen model du phong, VAN GIU NGUYEN Google Search
                # de xac minh chuc danh lanh dao (TASK_06).
                if any(marker in message for marker in ("429", "resource_exhausted", "quota")):
                    if current_model != FALLBACK_MODEL:
                        LOGGER.warning(
                            "Model %s vuot han muc (429); chuyen sang model du phong %s, "
                            "VAN GIU NGUYEN Google Search.",
                            current_model,
                            FALLBACK_MODEL,
                        )
                        current_model = FALLBACK_MODEL
                        continue
                    # Da o model du phong -> xu ly retry/binh thuong duoi day.

                if attempt < self.max_retries and self._is_retryable(message):
                    wait = self.retry_backoff * attempt
                    LOGGER.warning(
                        "Loi tam thoi khi goi Gemini (lan %d/%d): %s. Thu lai sau %.1fs.",
                        attempt,
                        self.max_retries,
                        exc,
                        wait,
                    )
                    time.sleep(wait)
                    continue

                break

        assert last_error is not None
        raise last_error

    @staticmethod
    def _looks_like_json_mode_conflict(message: str) -> bool:
        """Doan xem loi co phai do xung dot response_mime_type voi tool khong."""
        keywords = (
            "response_mime_type",
            "response mime type",
            "response_mime",
            "response schema",
            "responseschema",
            "json mode",
            "mime type",
            "tool and",
        )
        return "tool" in message and any(keyword in message for keyword in keywords)

    @staticmethod
    def _is_retryable(message: str) -> bool:
        """Xac dinh loi nao nen thu lai (rate limit / qua tai / loi mang)."""
        retryable_markers = (
            "409",
            "429",
            "conflict",
            "resource_exhausted",
            "rate limit",
            "quota",
            "500",
            "502",
            "503",
            "504",
            "internal",
            "unavailable",
            "deadline",
            "timeout",
            "timed out",
            "connection",
            "temporarily",
        )
        return any(marker in message for marker in retryable_markers)

    @staticmethod
    def _response_text(response: Any) -> str:
        """Lay van ban tu phan hoi, kem thong bao ro rang khi bi chan/rỗng."""
        try:
            text = response.text
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Khong lay duoc noi dung phan hoi (co the bi chan boi safety): {exc}"
            ) from exc

        if not text or not text.strip():
            reason = _describe_empty_response(response)
            raise RuntimeError(f"Model tra ve noi dung rong. {reason}")
        return text


def _describe_empty_response(response: Any) -> str:
    """Tao thong bao chan doan khi model tra ve rong (finish_reason, safety)."""
    details: List[str] = []
    try:
        candidates = getattr(response, "candidates", None) or []
        for candidate in candidates:
            finish_reason = getattr(candidate, "finish_reason", None)
            if finish_reason:
                details.append(f"finish_reason={finish_reason}")
        prompt_feedback = getattr(response, "prompt_feedback", None)
        block_reason = getattr(prompt_feedback, "block_reason", None) if prompt_feedback else None
        if block_reason:
            details.append(f"block_reason={block_reason}")
    except Exception:  # pragma: no cover - phong thu
        pass
    return " ".join(details) if details else "Khong co chan doan bo sung."


# ---------------------------------------------------------------------------
# Doc file bai bao
# ---------------------------------------------------------------------------


def read_article_file(path: str | os.PathLike) -> str:
    """Doc toan van bai bao tu file ``.txt`` / ``.md`` / ``.docx``.

    ``python-docx`` duoc import tre (lazy) vi no thuoc milestone xuat ban;
    voi file van ban thuan thi khong can thu vien ngoai.
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"Khong tim thay file bai bao: {file_path}")

    suffix = file_path.suffix.lower()
    if suffix == ".docx":
        try:
            from docx import Document  # import tre, tranh phu thuoc cung
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "Can cai dat python-docx de doc file .docx: "
                "pip install python-docx"
            ) from exc

        document = Document(str(file_path))
        paragraphs = [paragraph.text for paragraph in document.paragraphs]
        return "\n".join(paragraphs).strip()

    # Mac dinh doc nhu van ban UTF-8 (txt, md, ...).
    return file_path.read_text(encoding="utf-8").strip()


# ---------------------------------------------------------------------------
# Ham tien loi & CLI
# ---------------------------------------------------------------------------


def review_article(
    article_text: str,
    api_key: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Ham tien loi: kiem duyet mot bai bao ma khong can tu tao ``AIReviewer``."""
    reviewer = AIReviewer(api_key=api_key, model=model)
    return reviewer.review(article_text, **kwargs)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Kiem duyet bai bao bang Gemini + Google Search (core.reviewer)."
    )
    parser.add_argument(
        "--input",
        "-i",
        help="Duong dan file bai bao (.txt/.md/.docx). Bo trong de doc tu stdin.",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Duong dan file JSON ket qua. Bo trong de in ra man hinh.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Ten model Gemini (mac dinh: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--api-key",
        dest="api_key",
        default=None,
        help="Gemini API key (mac dinh lay tu bien moi truong/file .env).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Bat log chi tiet.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Diem vao CLI: ``python -m core.reviewer --input bai.txt``."""
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        if args.input:
            article_text = read_article_file(args.input)
        else:
            LOGGER.info("Dang doc bai bao tu stdin (ket thuc bang Ctrl+Z + Enter tren Windows)...")
            article_text = sys.stdin.read()

        reviewer = AIReviewer(api_key=args.api_key, model=args.model)
        result = reviewer.review(article_text)

        payload = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).write_text(payload, encoding="utf-8")
            LOGGER.info("Da ghi ket qua vao: %s", args.output)
        else:
            print(payload)

        LOGGER.info(
            "Hoan tat: %d loi chinh ta, %d muc fact-check, %d nguon tham chieu.",
            len(result["spelling_errors"]),
            len(result["fact_checks"]),
            len(result["grounding_sources"]),
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI can bao loi than thien
        LOGGER.error("Loi: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
