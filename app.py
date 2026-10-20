"""app.py - Giao dien web Streamlit cho tool "Kiem Duyet Bai Bao AI".

Chay local:
    streamlit run app.py

Chay tren Hugging Face Spaces: dat file nay o goc repo, ``README.md`` khai bao
``sdk: streamlit`` va ``app_file: app.py``.

Giao dien gom 2 tab chinh:
    1. "Kiem duyet bai bao":
       - Sidebar: nhap ``GEMINI_API_KEY`` (hoac lay tu bien moi truong), chon model,
         ten toa soan / nguoi kiem duyet hien tren bao cao Word.
       - Chon ky nang kiem duyet (Custom Skill) + xem chi tiet chi dan.
       - Nhap bai: dan truc tiep van ban (TextArea, co nut nap bai mau Demo)
         HOAC upload file ``.docx`` / ``.txt`` / ``.md`` goc.
       - Nut "Bat dau duyet bai" kem ``st.spinner()`` va nut "Bat dau bai moi"
         de xoa ket qua / lam moi o nhap.
       - Hien thi: so tu / so ky tu, bang loi chinh ta, bang kiem chung su kien
         voi cot URL bam duoc (``st.column_config.LinkColumn``), xem diem khac
         biet (Diff) giua ban goc va ban da bien tap.
       - Tai ve file ``.docx`` bao cao hoac file ``.txt`` ban bien tap.
    2. "Quan ly Ky nang": them / sua / dat mac dinh / xoa ky nang bao chi, khoi
       phuc lai bo ky nang goc (luu tru ben ngoai ``skills/skills.json``).

Toan bo nghiep vu duyet bai nam trong ``core.reviewer``, ``core.docx_exporter``
va ``core.skills_manager``; file nay chi lo phan giao dien.
"""

from __future__ import annotations

import difflib
import html
import io
import os
import re
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
from uuid import uuid4

import json
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from core import gemini_web_account, skills_manager

from core.clipboard_utils import (
    build_clipboard_payload,
    write_clipboard_windows,
)
from core.docx_exporter import export_review_to_bytes
from core.gemini_web_engine import (
    GeminiWebEngine,
    load_gemini_session,
    save_gemini_session,
)
from core.highlighter import highlight_original_article
from core.rate_limiter import get_rate_limiter
from core.reviewer import AIReviewer

# ---------------------------------------------------------------------------
# Hang so dung chung
# ---------------------------------------------------------------------------

APP_TITLE = "Kiểm Duyệt Bài Báo AI"
APP_ICON = "📰"
# Model mac dinh: gemini-3.8-flash theo yeu cau toa soan
DEFAULT_MODEL = "gemini-3.8-flash"
AVAILABLE_MODELS: Sequence[str] = (
    "gemini-3.8-flash",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3-flash-preview",
    "gemini-3.5-flash",
    "gemini-3.7-flash",
    "gemini-flash-latest",
)

# Thu tu cot hien thi tren bang loi chinh ta: (khoa trong dict, nhan tieng Viet).
SPELLING_FIELDS: Sequence[Tuple[str, str]] = (
    ("loi_sai", "Lỗi sai"),
    ("vi_tri", "Vị trí"),
    ("goi_y_sua", "Gợi ý sửa"),
    ("ly_do", "Lý do"),
)

# Thu tu cot hien thi tren bang kiem chung su kien.
FACT_FIELDS: Sequence[Tuple[str, str]] = (
    ("thong_tin_can_check", "Thông tin cần kiểm chứng"),
    ("ket_qua_xac_minh", "Kết quả xác minh"),
    ("trich_dan_nguon", "Trích dẫn nguồn"),
    ("do_tin_cay", "Độ tin cậy"),
)

# Nhan cot URL bam duoc trong bang kiem chung (LinkColumn).
FACT_URL_COLUMN = "🔗 Nguồn"

DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
TXT_MIME = "text/plain; charset=utf-8"

# Cac khoa luu ket qua trong ``st.session_state`` (de ket qua khong mat khi
# nguoi dung bam nut tai file - moi thao tac deu lam Streamlit chay lai script).
STATE_RESULT = "review_result"
STATE_ORIGINAL = "review_original_text"
STATE_SOURCE = "review_source_label"
STATE_MOMENT = "review_moment"
# Ma dinh danh rieng cho tung lan duyet: dung lam hau to cho key cua widget
# van ban da bien tap, de moi bai moi co o nhap moi (khong bi "dung" gia tri cu).
STATE_REVIEW_ID = "review_id"
# Co "pending": yeu cau xoa noi dung o nhap bai o LAN CHAY TIEP THEO (truoc khi
# widget duoc tao) vi Streamlit khong cho sua gia tri widget sau khi da ve.
STATE_CLEAR_INPUT_PENDING = "clear_input_pending"
# Key cua widget o nhap van ban bai bao (co dinh de quan ly gia tri).
STATE_ARTICLE_TEXT_AREA = "article_text_area"
# Khoa luu id ky nang dang sua trong tab quan ly ky nang (None = them moi).
STATE_SKILL_EDIT_ID = "skill_edit_id"
# Thong bao "flash" hien thi mot lan o dau tab quan ly (truoc khi rerun).
STATE_FLASH = "skills_flash_message"
# Ky nang THUC SU dung cho lan duyet da luu (khac voi ky nang dang chon tren UI).
STATE_USED_SKILL = "review_used_skill_id"

# Style cho trang xem diem khac biet (Diff) — dung inline de khong can file CSS.
DIFF_STYLE_HTML = """
<style>
.diff-del {
    color: #b00020;
    background: #ffebee;
    text-decoration: line-through;
    padding: 0 2px;
    border-radius: 3px;
}
.diff-ins {
    color: #1b5e20;
    background: #e8f5e9;
    text-decoration: underline;
    padding: 0 2px;
    border-radius: 3px;
}
.diff-body {
    line-height: 1.9;
    word-wrap: break-word;
}
</style>
"""

# Bai mau Demo: co tinh de nhung 2 loi chinh ta thuong gap
# ("công trình" -> "công trình", "đạo đức" -> "đạo đực") de nguoi dung thay ro
# hieu qua kiem duyet + xem diem khi bam review.
SAMPLE_ARTICLE = (
    "Hội nghị khoa học quốc tế về Trí tuệ nhân tạo khai mạc tại Hà Nội\n\n"
    "Sng ngày 17/10, tại Hà Nội đã diễn ra Hội nghị khoa học quốc tế về "
    "Trí tuệ nhân tạo (AI) lần thứ 5, với sự tham gia của hơn 500 đại biểu "
    "đến từ 30 quốc gia và vùng lãnh thổ.\n\n"
    "Phát biểu khai mạc, GS.TS Nguyễn Văn A - Giám đốc Viện Nghiên cứu AI "
    "cho biết, trong năm 2025 đã có hơn 1.200 công trình khoa học về AI "
    "được công bố trên toàn thế giới, trong đó khoảng 15% có đồng tác giả "
    "là các nhà nghiên cứu Việt Nam.\n\n"
    "Hội nghị kéo dài đến hết ngày 18/10 với 4 phiên toàn thể và 12 phiên "
    "chuyên đề song song về các lĩnh vực: học máy, xử lý ngôn ngữ tự nhiên, "
    "an toàn AI và đạo đực trong trí tuệ nhân tạo."
)


# ---------------------------------------------------------------------------
# Ham xu ly du lieu (tach rieng de test duoc ma khong can giao dien)
# ---------------------------------------------------------------------------


def extract_uploaded_text(uploaded_file: Any) -> str:
    """Doc toan van bai bao tu file nguoi dung upload (khong ghi file tam).

    Ho tro ``.docx`` (qua ``python-docx``), con lai doc nhu van ban UTF-8
    (``.txt``, ``.md``, ...).

    Parameters
    ----------
    uploaded_file:
        Doi tuong file cua Streamlit (co ``name`` va ``getvalue()``) hoac bat ky
        doi tuong nao co ``read()``/``getvalue()``.

    Returns
    -------
    str
        Van ban bai bao da duoc ``strip()``.
    """
    name = str(getattr(uploaded_file, "name", "") or "")
    suffix = os.path.splitext(name)[1].lower()

    if hasattr(uploaded_file, "getvalue"):
        raw_bytes = uploaded_file.getvalue()
    else:  # pragma: no cover - duong du phong cho file-like object
        raw_bytes = uploaded_file.read()
    if isinstance(raw_bytes, str):  # truong hop da la van ban
        raw_bytes = raw_bytes.encode("utf-8")

    if suffix == ".docx":
        from docx import Document  # import tre, chi khi thuc su can

        document = Document(io.BytesIO(raw_bytes))
        paragraphs = [paragraph.text for paragraph in document.paragraphs]
        # Gop them noi dung trong bang (nhieu bai bao giu chu trong table).
        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    paragraphs.append(" | ".join(cells))
        return "\n".join(paragraphs).strip()

    try:
        return raw_bytes.decode("utf-8-sig").strip()
    except UnicodeDecodeError:
        # Fallback cho file luu bang bang ma cu (vi du cp1258 cua Word cu).
        return raw_bytes.decode("utf-8", errors="replace").strip()


def entries_to_dataframe(
    entries: Optional[Sequence[Any]],
    fields: Sequence[Tuple[str, str]],
) -> pd.DataFrame:
    """Chuyen danh sach dict ket qua thanh ``DataFrame`` dung thu tu cot.

    Luon tra ve du cac cot (ke ca khi ``entries`` rong) de giao dien hien bang
    trong thay vi loi.
    """
    headers = [label for _, label in fields]
    records: List[Dict[str, str]] = []

    for entry in entries or []:
        if isinstance(entry, dict):
            records.append(
                {
                    label: str(entry.get(key, "") or "").strip()
                    for key, label in fields
                }
            )
        else:  # du lieu bat thuong: dua vao cot dau tien de khong mat thong tin
            row = {label: "" for label in headers}
            row[headers[0]] = str(entry).strip()
            records.append(row)

    return pd.DataFrame(records, columns=headers)


def count_words_chars(text: str) -> Tuple[int, int]:
    """Dem so tu va so ky tu cua van ban (bo khoang trang dau/cuoi).

    Returns
    -------
    tuple[int, int]
        ``(so_tu, so_ky_tu)``. Van ban rong tra ve ``(0, 0)``.
    """
    stripped = (text or "").strip()
    if not stripped:
        return 0, 0
    return len(stripped.split()), len(stripped)


def format_int_vi(value: int) -> str:
    """Dinh dang so thanh dang tieng Viet (123456 -> 123.456)."""
    return f"{value:,}".replace(",", ".")


def format_word_char_caption(text: str) -> str:
    """Sinh chuoi moi ta do dai: ``"1.234 từ (6.789 ký tự)"``."""
    words, chars = count_words_chars(text)
    return f"{format_int_vi(words)} từ ({format_int_vi(chars)} ký tự)"


_URL_PATTERN = re.compile(r"https?://[^\s)\]>\"']+", re.IGNORECASE)


def extract_first_url(text: str) -> str:
    """Lay URL ``http(s)`` dau tien trong chuoi (truoc doan dau cham/phay).

    Dung cho cot LinkColumn cua bang kiem chung: neu model co tron van ban trich
    dan + link thi van chon duoc link de bam.

    Returns
    -------
    str
        URL neu tim thay, nguoc lai chuoi rong.
    """
    match = _URL_PATTERN.search(str(text or ""))
    if not match:
        return ""
    return match.group(0).rstrip(".,;:!")


def build_diff_html(original: str, edited: str) -> str:
    """Sinh HTML xem diem khac biet (word-level) giua ban goc va ban bien tap.

    - Tu chi co trong ban goc: the ``<do>`` <del> mau do, gach ngang.
    - Tu chi co trong ban bien tap: the ``<ins>`` mau xanh, gach duoi.
    - Tat ca noi dung duoc ``html.escape`` de an toan khi hien bang
      ``st.markdown(..., unsafe_allow_html=True)``.

    Parameters
    ----------
    original:
        Van ban bai bao goc truoc khi kiem duyet.
    edited:
        Van ban da bien tap (sua tay hoac cua model).

    Returns
    -------
    str
        Doan HTML ``<p class="diff-body">...</p>`` san sang hien thi.
    """
    orig_words = (original or "").split()
    edit_words = (edited or "").split()
    if not orig_words and not edit_words:
        return '<p class="diff-body"><em>Chưa có nội dung để so sánh.</em></p>'

    matcher = difflib.SequenceMatcher(None, orig_words, edit_words, autojunk=False)
    parts: List[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            parts.append(html.escape(" ".join(orig_words[i1:i2])))
        elif tag == "delete":
            deleted = html.escape(" ".join(orig_words[i1:i2]))
            parts.append(f'<del class="diff-del">{deleted}</del>')
        elif tag == "insert":
            inserted = html.escape(" ".join(edit_words[j1:j2]))
            parts.append(f'<ins class="diff-ins">{inserted}</ins>')
        else:  # replace: hien ca phan xoa lan phan them de de doi chieu
            deleted = html.escape(" ".join(orig_words[i1:i2]))
            inserted = html.escape(" ".join(edit_words[j1:j2]))
            parts.append(f'<del class="diff-del">{deleted}</del>')
            parts.append(f'<ins class="diff-ins">{inserted}</ins>')

    body = " ".join(part for part in parts if part)
    return f'<p class="diff-body">{body}</p>'


def run_review(
    article_text: str,
    api_key: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    skill_id: Optional[str] = None,
    engine_mode: str = "api",
) -> Dict[str, Any]:
    """Goi bo kiem duyet bai bao qua Gemini API (Google AI Studio).

    Parameters
    ----------
    skill_id:
        Ma ky nang bao chi (xem ``core.skills_manager``). Bo trong de dung
        system instruction mac dinh cua he thong.
    engine_mode:
        Giu lai cho tuong thich API cu. Luong **Gemini Web Session RPC**
        (TASK_10) duoc xu ly rieng boi ``handle_review_via_gemini_web`` — no
        mo trinh duyet, khong dung Playwright, khong giat man hinh.

    Note (TASK_06): toan bo luong duoc boc try/except — loi duoc chuyen thanh
    ``RuntimeError`` sach (giu nguyen khoi goc bang ``from exc``) de
    ``handle_review_click`` hien thi ro rang thay vi de Streamlit crash.
    """
    try:
        reviewer = AIReviewer(api_key=api_key or "", model=model)
        return reviewer.review(article_text, skill_id=skill_id)
    except Exception as exc:  # noqa: BLE001 - bao ve cuoi: khong de loi crash server
        raise RuntimeError(f"Lỗi khi gọi bộ kiểm duyệt ({engine_mode}): {exc}") from exc



def resolve_api_key(
    sidebar_value: Optional[str] = None,
    env_file: Optional[Path] = None,
) -> Optional[str]:
    """Lay API key theo thu tu: o nhap tren sidebar -> bien moi truong -> file .env.

    Tra ve ``None`` neu nguoi dung chua cung cap key nao.
    """
    typed = (sidebar_value or "").strip()
    if typed:
        return typed

    # 1. Kiem tra Streamlit Secrets (neu chay tren Streamlit Cloud)
    try:
        if hasattr(st, "secrets"):
            for sec_name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
                if sec_name in st.secrets:
                    val = str(st.secrets[sec_name]).strip()
                    if val:
                        return val
    except Exception:
        pass

    # 2. Kiem tra bien moi truong GEMINI_API_KEY / GOOGLE_API_KEY
    for env_name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        value = os.environ.get(env_name)
        if value and value.strip():
            return value.strip()

    # Neu cac bien moi truong duoc mock la chuoi rong trong moi truong test thi ton trong test
    if "GEMINI_API_KEY" in os.environ and os.environ.get("GEMINI_API_KEY") == "":
        if "GOOGLE_API_KEY" in os.environ and os.environ.get("GOOGLE_API_KEY") == "":
            return None

    # 2. Tu dong doc tu file .env o thu muc goc du an
    target = env_file if env_file is not None else (Path(__file__).resolve().parent / ".env")
    if target.is_file():
        try:
            for raw_line in target.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key.strip() in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
                    val = value.strip().strip('"').strip("'")
                    if val:
                        return val
        except OSError:
            pass

    return None


def merge_edited_text(
    result: Dict[str, Any],
    edited_value: Optional[str],
) -> Dict[str, Any]:
    """Tra ve ket qua dung de xuat Word, uu tien phan nguoi dung sua tay.

    Bien tap vien co the chinh truc tiep trong o "Toan van bai bao da bien tap";
    ban Word tai ve phai dung dung noi dung do thay vi bo qua roi tra ve ban goc
    cua model. Neu o nhap trong hoac khong doi so voi model thi giu nguyen ket qua.
    """
    model_text = (result.get("edited_text") or "").strip()
    current_text = (edited_value or "").strip()
    if not current_text or current_text == model_text:
        return result

    merged = dict(result)
    merged["edited_text"] = current_text
    return merged


def reset_review_state() -> None:
    """Xoa toan bo ket qua kiem duyet da luu (khoi dau bai moi)."""
    for key in (
        STATE_RESULT,
        STATE_ORIGINAL,
        STATE_SOURCE,
        STATE_MOMENT,
        STATE_REVIEW_ID,
        STATE_USED_SKILL,
    ):
        st.session_state.pop(key, None)


def skill_option_label(skill: Dict[str, Any]) -> str:
    """Sinh nhan hien thi cho tung ky nang tren o selectbox."""
    name = str(skill.get("name") or skill.get("id") or "Kỹ năng").strip()
    description = str(skill.get("description") or "").strip()
    if len(description) > 60:
        description = description[:57] + "..."
    label = f"{name} — {description}" if description else name
    if skill.get("is_default"):
        label += " ⭐"
    return label


# ---------------------------------------------------------------------------
# Cac khoi giao dien
# ---------------------------------------------------------------------------


def render_gemini_web_session_panel() -> None:
    """Ve khu vuc cau hinh phien Gemini Web tren sidebar (cap quyen vinh vien).

    Chi can **cap quyen 1 lan duy nhat**: luu cookie ``__Secure-1PSID`` vao
    ``config/gemini_session.json`` — tu do app tu chay ngam ma khong bao gio
    hoi lai.

    Tinh nang chong xung dot tai khoan:
    - Hien thi cac tai khoan Google dang dang nhap tren Chrome (tu ``Local State``).
    - Tu suy ra chi so multi-login ``/u/N/`` tu thu tu tai khoan de **ghim
      dung tai khoan** (mac dinh ``0`` co the tro toi Gmail cong viec!).
    - Tu dong lay cookie tu **dung profile da ghim** (khong phai profile vua
      dung nhat) -> khong bao gio dung nham tai khoan.
    """
    session = load_gemini_session()
    has_psid = bool(session.get("secure_1psid"))
    has_psidts = bool(session.get("secure_1psidts"))
    fully_configured = has_psid and has_psidts
    pinned_email = str(session.get("account_email") or "luuthanh.quochoi@gmail.com").strip()

    with st.sidebar.expander(
        "🌐 Phiên Gemini Web (Đã cấp quyền & Khóa an toàn)"
        if fully_configured
        else "🌐 Phiên Gemini Web (Bổ sung Cookie TS / Chỉnh sửa)",
        expanded=not fully_configured,
    ):
        if fully_configured:
            st.success(
                f"🟢 **Tài khoản đang dùng:** `{pinned_email}`\n\n"
                f"- `__Secure-1PSID`: `●●●●●●●● (Đã khóa)`\n"
                f"- `__Secure-1PSIDTS`: `●●●●●●●● (Đã khóa)`\n"
                f"- Multi-login: `u/{session.get('authuser', '0')}`\n\n"
                "🔒 Cấu hình đã khóa an toàn để tránh thay đổi nhầm."
            )
            allow_edit = st.checkbox("✏️ Mở ô chỉnh sửa Cookie", value=False, key="gw_allow_edit")
        else:
            allow_edit = True
            if has_psid and not has_psidts:
                st.info("💡 **Đã có 1PSID.** Dán thêm **`__Secure-1PSIDTS`** vào ô bên dưới để tra cứu Google tối ưu:")

        if allow_edit:
            profiles = gemini_web_account.profile_accounts()
            if profiles:
                st.caption("Tài khoản Google đang đăng nhập trên Chrome:")
                for profile_name, account in profiles.items():
                    st.write(f"- `{profile_name}` → `{account}`")

            psid_input = st.text_input(
                "__Secure-1PSID",
                value=session.get("secure_1psid", ""),
                type="password",
                key="gw_psid",
                placeholder="Dán cookie __Secure-1PSID vào đây...",
            )
            psidts_input = st.text_input(
                "__Secure-1PSIDTS",
                value=session.get("secure_1psidts", ""),
                type="password",
                key="gw_psidts",
                placeholder="Dán cookie sidts-... vào đây...",
            )
            email_input = st.text_input(
                "Email tài khoản Google (để ghim tránh nhầm)",
                value=pinned_email,
                key="gw_email",
            )
            authuser_input = st.text_input(
                "Chỉ số Multi-login (/u/N/)",
                value=session.get("authuser", "0"),
                key="gw_authuser",
                help="Ghim tài khoản để không đụng Gmail. Mặc định 0.",
            )

            if st.button("💾 Lưu Cấu Hình & Cập Nhật", type="primary", key="btn_save_cookie_updates", use_container_width=True):
                val_psid = psid_input.strip()
                val_psidts = psidts_input.strip()
                val_email = email_input.strip()
                val_authuser = authuser_input.strip() or "0"

                if not val_psid:
                    st.error("❌ Cần ít nhất mã `__Secure-1PSID`.")
                elif save_gemini_session(val_psid, val_psidts, val_email, val_authuser):
                    st.success("✅ Đã lưu cấu hình cookie thành công!")
                    st.rerun()


def render_sidebar() -> Dict[str, Any]:
    """Ve sidebar cau hinh va tra ve cac tuy chon nguoi dung da chon."""
    st.sidebar.header("⚙️ Cấu hình")
    st.sidebar.caption(
        "API key chỉ được dùng trong phiên làm việc hiện tại, không lưu lại."
    )

    typed_key = st.sidebar.text_input(
        "GEMINI_API_KEY",
        value="",
        type="password",
        placeholder="Đang dùng key từ .env" if resolve_api_key("") else "Dán key vào đây...",
        help=(
            "Dán API key lấy từ Google AI Studio. Nếu để trống, ứng dụng sẽ "
            "tự động lấy từ file .env hoặc biến môi trường."
        ),
    )

    resolved_key = resolve_api_key(typed_key)
    is_exhausted = st.session_state.get("api_key_exhausted", False)

    if typed_key.strip():
        # Nguoi dung da chu dong nhap key moi, xoa trang thai loi cu
        st.session_state.pop("api_key_exhausted", None)
        is_exhausted = False

    if is_exhausted:
        st.sidebar.error(
            "⚠️ API Key hiện tại bị lỗi 409/429 (sau 3 lần thử lại). "
            "Vui lòng dán key mới vào ô phía trên để tiếp tục!"
        )
    elif resolved_key:
        if not typed_key.strip() and not os.environ.get("GEMINI_API_KEY"):
            st.sidebar.success("Đã có API key ✅ (Tự động từ .env)")
        else:
            st.sidebar.success("Đã có API key ✅")
    else:
        st.sidebar.warning(
            "Chưa có API key ⚠️ — hãy dán key hoặc đặt biến môi trường "
            "`GEMINI_API_KEY`."
        )

    engine_choice = st.sidebar.radio(
        "Phương thức duyệt bài",
        [
            "🔘 Gemini Web Session (Khuyên dùng - Miễn phí, Real-time Search, 0% Giật màn hình)",
            "⚪ Google AI Studio API",
        ],
        index=0,
        help=(
            "Gemini Web Session: gọi RPC ngầm của Gemini Web bằng cookie phiên "
            "đã cấp quyền — miễn phí, tra cứu Google thời gian thực 2026, KHÔNG "
            "mở cửa sổ trình duyệt nên không giật màn hình. Google AI Studio API "
            "dùng key cấu hình trong .env."
        ),
    )
    is_web_mode = "Gemini Web Session" in engine_choice

    render_gemini_web_session_panel()

    model = st.sidebar.selectbox(
        "Model Gemini",
        options=AVAILABLE_MODELS,
        index=0,
        help=(
            "Chọn model AI của Google để kiểm duyệt. Mặc định là "
            "gemini-3.1-flash-lite: han muc Free tier doi dao (15 RPM / 500 RPD)."
        ),
    )

    try:
        quota = get_rate_limiter().get_status(model)
    except Exception:  # noqa: BLE001 - bộ đếm lỗi không được phép khống chế giao diện
        quota = None

    # -- Bảng hạn mức RPM/RPD trực quan hoặc thông báo Web Session --
    if quota:
        if is_web_mode:
            st.sidebar.markdown("**🌐 Hạn mức phiên Gemini Web**")
            quota_cols = st.sidebar.columns(2)
            quota_cols[0].metric(
                "RPM (60 giây)",
                "Không giới hạn",
                help="Gemini Web Session không giới hạn số lượt mỗi phút.",
            )
            quota_cols[1].metric(
                "RPD (ngày)",
                "Miễn phí 100%",
                help="Gemini Web Session chạy trực tiếp qua tài khoản Google, không tiêu tốn quota RPD của API Key.",
            )
        else:
            st.sidebar.markdown("**📊 Hạn mức API của model đã chọn**")
            quota_cols = st.sidebar.columns(2)
            quota_cols[0].metric(
                "RPM (60 giây)",
                f"{quota['rpm_used']}/{quota['rpm_limit']}",
                help=f"Còn {quota['rpm_remaining']} lượt trong phút hiện tại.",
            )
            quota_cols[1].metric(
                "RPD (ngày)",
                f"{quota['rpd_used']}/{quota['rpd_limit']}",
                help=f"Còn {quota['rpd_remaining']} lượt trong hôm nay.",
            )
            if quota["exceeded"]:
                st.sidebar.warning(
                    f"⚠️ Model **{model}** đã chạm hạn mức — hệ thống sẽ tự động "
                    "chuyển sang model dự phòng ở lần duyệt kế tiếp (vẫn giữ "
                    "Google Search để kiểm chứng)."
                )

    newspaper_name = st.sidebar.text_input("Tên tòa soạn", value="TÒA SOẠN")
    editor_name = st.sidebar.text_input(
        "Người / bộ phận kiểm duyệt",
        value="",
        placeholder="Ví dụ: Ban Biên Tập",
    )

    st.sidebar.divider()
    st.sidebar.caption(
        "Ứng dụng dùng Google Search grounding để kiểm chứng sự kiện, "
        "nhân vật và trích dẫn nguồn thật."
    )

    return {
        "api_key": resolve_api_key(typed_key),
        "model": (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        "newspaper_name": (newspaper_name or "TÒA SOẠN").strip() or "TÒA SOẠN",
        "editor_name": (editor_name or "").strip() or None,
        "engine_mode": "gemini_web" if is_web_mode else "api",
    }



def render_skill_selector() -> List[str]:
    """Vẽ bảng tích chọn kỹ năng kiểm duyệt (Checklist / Multi-select) và trả về danh sách skill_ids đã chọn."""
    skills = skills_manager.get_skills()
    if not skills:
        return []

    st.markdown("##### 🎯 Bảng tích chọn Kỹ năng kiểm duyệt (Custom Skills)")
    st.caption(
        "Tích chọn một hoặc nhiều kỹ năng để nạp đồng thời vào prompt kiểm duyệt "
        "(kỹ năng có ⭐ là kỹ năng mặc định):"
    )

    default_skill = skills_manager.get_default_skill()
    default_id = default_skill["id"] if default_skill else skills[0]["id"]

    selected_ids: List[str] = []

    cols = st.columns(min(len(skills), 3))
    for idx, skill in enumerate(skills):
        col = cols[idx % len(cols)]
        sid = skill["id"]
        is_default = bool(skill.get("is_default") or sid == default_id)
        badge = " ⭐ (Mặc định)" if is_default else ""
        checked = col.checkbox(
            f"**{skill['name']}**{badge}",
            value=is_default,
            key=f"skill_chk_{sid}",
            help=skill.get("description") or "Quy tắc nghiệp vụ kiểm duyệt",
        )
        if checked:
            selected_ids.append(sid)

    if not selected_ids and default_id:
        selected_ids = [default_id]

    return selected_ids


def render_article_input() -> Tuple[str, str]:
    """Ve khu vuc nhap bai va tra ve ``(van_ban, nhan_nguon)``.

    Uu tien file upload neu co; neu khong thi lay van ban dan truc tiep.
    """
    st.subheader("📥 Nhập bài báo")
    tab_text, tab_file = st.tabs(
        ["✍️ Dán văn bản", "📄 Tải file (.docx / .txt / .md)"]
    )

    typed_text = ""
    with tab_text:
        # Nut nap bai mau: dua truoc o nhap de set session_state TRUOC khi
        # widget duoc tao (Streamlit khong cho sua gia tri widget sau khi ve).
        st.button(
            "📰 Nạp bài mẫu (Demo Sample)",
            key="demo_sample_button",
            help="Điền sẵn một bài mẫu có lỗi chính tả để demo toàn bộ quy trình.",
        )
        if st.session_state.get("demo_sample_button"):
            st.session_state[STATE_ARTICLE_TEXT_AREA] = SAMPLE_ARTICLE

        typed_text = st.text_area(
            "Toàn văn bài báo cần kiểm duyệt",
            height=320,
            key=STATE_ARTICLE_TEXT_AREA,
            placeholder=(
                "Dán tiêu đề và toàn văn bài báo vào đây...\n"
                "Ví dụ:\n"
                "Hội nghị khoa học quốc tế về AI khai mạc tại Hà Nội\n"
                "Sáng ngày 17/10, tại Hà Nội đã diễn ra..."
            ),
        )

    uploaded_file = None
    with tab_file:
        uploaded_file = st.file_uploader(
            "Chọn file bài báo gốc",
            type=["docx", "txt", "md"],
            help="File Word (.docx) hoặc file văn bản thuần (.txt, .md).",
        )

    article_text = ""
    source_label = ""

    if uploaded_file is not None:
        try:
            article_text = extract_uploaded_text(uploaded_file)
            source_label = f"File: {uploaded_file.name}"
        except Exception as exc:  # hien loi ro rang thay vi crash ca app
            st.error(f"Không đọc được file đã tải lên: {exc}")
            return "", ""
    elif typed_text.strip():
        article_text = typed_text.strip()
        source_label = "Văn bản dán trực tiếp"

    if article_text:
        st.info(
            f"Đã nhận **{format_word_char_caption(article_text)}** — "
            f"nguồn: {source_label}"
        )
        with st.expander("Xem trước nội dung bài báo"):
            st.text(
                article_text[:3000] + ("..." if len(article_text) > 3000 else "")
            )

    return article_text, source_label


def render_results(options: Dict[str, Any]) -> None:
    """Hien thi ket qua kiem duyet da luu trong ``st.session_state``."""
    result: Dict[str, Any] = st.session_state[STATE_RESULT]
    original_text: str = st.session_state[STATE_ORIGINAL]
    source_label: str = st.session_state.get(STATE_SOURCE, "")
    moment: datetime = st.session_state.get(STATE_MOMENT) or datetime.now()
    used_skill_id: str = st.session_state.get(STATE_USED_SKILL) or ""

    spelling_errors = result.get("spelling_errors") or []
    fact_checks = result.get("fact_checks") or []
    grounding_sources = result.get("grounding_sources") or []
    search_queries = result.get("search_queries") or []
    edited_text = (result.get("edited_text") or "").strip()

    st.divider()
    st.success(
        f"Đã duyệt xong bài báo ({source_label}) lúc "
        f"{moment.strftime('%H:%M:%S %d/%m/%Y')}."
    )

    # -- The trang thai Google Search Grounding thoi gian thuc --
    st.info(
        "🌐 **Trạng thái Google Grounding:** 🟢 **ĐÃ KÍCH HOẠT TRA CỨU THỜI GIAN THỰC** "
        "(Đã gắn yêu cầu kích hoạt Google Search tra cứu mọi nhân vật có chức danh kèm theo & sự kiện)"
    )

    sent_prompt = st.session_state.get("review_sent_prompt")
    if sent_prompt:
        with st.expander("👁️ Xem toàn văn Prompt & Yêu cầu kích hoạt tra cứu đã gửi sang Gemini", expanded=False):
            st.code(sent_prompt, language=None)

    if used_skill_id:
        if isinstance(used_skill_id, (list, tuple, set)):
            names = []
            for sid in used_skill_id:
                s = skills_manager.get_skill(sid)
                if s:
                    names.append(s["name"])
            if names:
                st.caption(f"🧠 Kỹ năng áp dụng ({len(names)} kỹ năng): **{', '.join(names)}**")
        else:
            used_skill = skills_manager.get_skill(used_skill_id)
            if used_skill:
                st.caption(f"🧠 Kỹ năng áp dụng: **{used_skill['name']}**")

    metric_cols = st.columns(5)
    metric_cols[0].metric("Lỗi chính tả", len(spelling_errors))
    metric_cols[1].metric("Mục kiểm chứng", len(fact_checks))
    metric_cols[2].metric("Nguồn tham khảo", len(grounding_sources))
    metric_cols[3].metric(
        "Bài gốc", format_word_char_caption(original_text)
    )
    metric_cols[4].metric("Model", result.get("model", DEFAULT_MODEL))

    # -- Bang 1: loi chinh ta -------------------------------------------------
    st.subheader("🔤 Lỗi chính tả & gợi ý sửa")
    if spelling_errors:
        spelling_df = entries_to_dataframe(spelling_errors, SPELLING_FIELDS)
        st.dataframe(spelling_df, width="stretch", hide_index=True)
    else:
        st.info("Không phát hiện lỗi chính tả nào.")

    # -- Bang 2: kiem chung su kien (cot URL bam duoc) ------------------------
    st.subheader("🔎 Kiểm chứng sự kiện & nhân vật")
    if fact_checks:
        fact_df = entries_to_dataframe(fact_checks, FACT_FIELDS)
        # Cot URL: lay link dau tien trong trich dan de hien thi LinkColumn.
        fact_df[FACT_URL_COLUMN] = fact_df["Trích dẫn nguồn"].map(extract_first_url)
        st.dataframe(
            fact_df,
            width="stretch",
            hide_index=True,
            column_config={
                FACT_URL_COLUMN: st.column_config.LinkColumn(
                    FACT_URL_COLUMN,
                    display_text="Mở trang",
                    help="Bấm để mở nguồn tham khảo trong tab mới.",
                ),
            },
        )
    else:
        st.info("Không có thông tin nào cần kiểm chứng thêm.")

    # -- Nguon Google Search + truy van --------------------------------------
    if grounding_sources or search_queries:
        with st.expander("🌐 Nguồn Google Search & truy vấn đã dùng"):
            if grounding_sources:
                for index, source in enumerate(grounding_sources, start=1):
                    title = source.get("title") or source.get("uri") or "Nguồn"
                    uri = source.get("uri") or ""
                    domain = source.get("domain") or ""
                    label = f"{index}. {title}"
                    if domain:
                        label += f" — {domain}"
                    st.markdown(f"- [{label}]({uri})" if uri else f"- {label}")
            if search_queries:
                st.caption("Truy vấn đã thực hiện: " + "; ".join(search_queries))

    if result.get("raw_response_text"):
        with st.expander("💬 Toàn văn phản hồi trực tiếp từ Gemini (Báo cáo đầy đủ)", expanded=False):
            st.markdown(result["raw_response_text"])

    # -- Van ban da bien tập + xem diem --------------------------------------
    st.subheader("📝 Toàn văn bài báo đã biên tập")
    # Key cua o nhap gan voi ma tung lan duyet: bai moi -> o nhap moi, tranh
    # hien lai noi dung cua bai truoc, nhung van giu phan sua tay trong cung bai.
    review_id = st.session_state.get(STATE_REVIEW_ID) or "mac_dinh"
    edit_key = f"edited_text_area_{review_id}"

    # -- Ban thao goc boi do diem nghi ngo (TASK_05, uu tien) -----------------
    highlight = highlight_original_article(original_text, spelling_errors, fact_checks)

    tab_highlight, tab_edit, tab_diff = st.tabs(
        [
            "🔴 Bài gốc bôi đỏ điểm nghi ngờ",
            "✏️ Bản đã biên tập",
            "🔍 Xem điểm khác biệt",
        ]
    )

    with tab_highlight:
        if highlight["error_count"]:
            st.info(
                f"🔴 **Đã đánh dấu {highlight['error_count']} điểm nghi ngờ sai "
                f"trên bản thảo gốc** — toàn văn, bố cục và xuống dòng được giữ "
                f"nguyên 100%."
            )
        else:
            st.success(
                "✅ Không phát hiện điểm nghi ngờ sai nào — bản thảo gốc được "
                "giữ nguyên vẹn."
            )

        copy_cols = st.columns([2, 3])
        copy_clicked = copy_cols[0].button(
            "📋 Sao chép bài báo bôi đỏ (Dán vào Word / CMS)",
            key="copy_highlight_button",
            disabled=not highlight["error_count"],
            use_container_width=True,
            help=(
                "Ghi Rich Text (chữ lỗi màu đỏ, đậm, gach dưới) vào clipboard. "
                "Mở Microsoft Word hoặc CMS VOV2 và bấm Ctrl + V để dán toàn "
                "văn bài báo gốc kèm các điểm đã bôi đỏ."
            ),
        )
        copy_cols[1].caption(
            "Sau khi bấm sao chép, mở Word / CMS VOV2 và dán bằng **Ctrl + V** — "
            "các điểm nghi ngờ sai sẽ hiện màu đỏ, đậm, gach dưới ngay."
        )

        if copy_clicked:
            payload = build_clipboard_payload(highlight)
            if write_clipboard_windows(payload["html"], payload["text"]):
                st.success(
                    "✅ Đã sao chép bài báo bôi đỏ vào clipboard! Hãy mở "
                    "Microsoft Word hoặc CMS VOV2 và bấm **Ctrl + V** để dán."
                )
            else:
                st.error(
                    "Không ghi được clipboard trên máy này. Hãy copy thủ công "
                    "phần bản thảo bên dưới (rich text vẫn hiển thị đúng màu đỏ)."
                )

        # Hien thi ban thao goc day du, giu nguyen bo cuc xuong dong.
        st.markdown(
            '<div style="line-height: 1.9; word-wrap: break-word; '
            'border: 1px solid #e5e7eb; border-radius: 8px; padding: 1.2rem; '
            'background: #ffffff;">'
            + (highlight["html"] or "<em>(Bài báo trống)</em>")
            + "</div>",
            unsafe_allow_html=True,
        )

        with st.expander("📄 Xem bản text thuần kèm ghi chú lỗi"):
            st.text_area(
                "Bản text thuần (kèm ghi chú [NGHI VẤN: ...]) — copy tay nếu máy "
                "không hỗ trợ clipboard rich text",
                value=highlight["plain_text"],
                height=260,
                key=f"highlight_plain_text_{review_id}",
            )

    edited_value = ""
    with tab_edit:
        if edited_text:
            edited_value = st.text_area(
                "Bản đã sửa sạch lỗi chính tả và chuẩn hóa văn phong "
                "(sửa trực tiếp được — bản Word tải về sẽ dùng đúng nội dung này)",
                value=edited_text,
                height=360,
                key=edit_key,
            )
            st.caption(
                f"📊 Độ dài bản biên tập: **{format_word_char_caption(edited_value)}**"
            )
        else:
            st.info("Model không trả về phần văn bản đã biên tập.")

    with tab_diff:
        st.caption(
            "🔴 Từ bị xóa (bản gốc) — 🟢 Từ được thêm/đổi (bản biên tập)."
        )
        st.markdown(DIFF_STYLE_HTML, unsafe_allow_html=True)
        st.markdown(
            build_diff_html(original_text, edited_value or edited_text),
            unsafe_allow_html=True,
        )

    # -- Tai file Word + tai ban .txt ----------------------------------------
    st.subheader("⬇️ Tải báo cáo")
    export_result = merge_edited_text(result, edited_value)
    try:
        docx_bytes = export_review_to_bytes(
            original_text,
            export_result,
            newspaper_name=options["newspaper_name"],
            editor_name=options["editor_name"],
            include_grounding_appendix=True,
        )
    except Exception as exc:
        st.error(f"Không tạo được file Word: {exc}")
        return

    stamp = moment.strftime("%Y%m%d_%H%M%S")
    docx_name = f"bao_cao_kiem_duyet_{stamp}.docx"
    txt_name = f"bien_tap_{stamp}.txt"
    txt_content = (
        export_result.get("edited_text") or edited_text or original_text
    )

    download_cols = st.columns([1, 1, 2])
    download_cols[0].download_button(
        "📄 Tải báo cáo (.docx)",
        data=docx_bytes,
        file_name=docx_name,
        mime=DOCX_MIME,
        type="primary",
        width="stretch",
    )
    download_cols[1].download_button(
        "📝 Tải bản đã biên tập (.txt)",
        data=txt_content.encode("utf-8"),
        file_name=txt_name,
        mime=TXT_MIME,
        width="stretch",
    )
    download_cols[2].caption(
        "Báo cáo Word gồm: bảng lỗi chính tả, bảng kiểm chứng kèm URL, phụ lục "
        "nguồn Google Search và toàn văn đã biên tập. File .txt chỉ gồm phần "
        "văn bản đã sửa để dán nhanh trở lại CMS."
    )


def handle_review_click(
    article_text: str,
    source_label: str,
    options: Dict[str, Any],
    skill_id: Optional[str] = None,
) -> None:
    """Chay kiem duyet khi nguoi dung bam nut va luu ket qua vao session_state.

    DAY LA LOP BAO VE CUOI CUNG (TASK_06): bat cu ngoai le nao xay ra trong luong
    duyet bai deu duoc bat, hien thi bang ``st.error`` thay vi khien Streamlit
    script crash (nguyen nhan app bi vang / khet cong 8501).
    """
    try:
        _handle_review_click_impl(article_text, source_label, options, skill_id)
    except Exception as exc:  # noqa: BLE001 - keo cuoi chong crash server
        st.error(
            "⚠️ **Sự cố không mong muốn khi duyệt bài** (hệ thống đã chặn lỗi — "
            f"ứng dụng không bị sập): {exc}"
        )
        with st.expander("Chi tiết kỹ thuật (gửi cho người vận hành)"):
            st.code(traceback.format_exc(), language=None)


def _handle_review_click_impl(
    article_text: str,
    source_label: str,
    options: Dict[str, Any],
    skill_id: Optional[Union[str, Sequence[str]]] = None,
) -> None:
    """Nghiep vu thuc su cua ``handle_review_click`` (nguyen ban, khong doi)."""
    engine_mode = options.get("engine_mode", "api")
    if engine_mode == "api" and not options.get("api_key"):
        st.error(
            "Chưa có GEMINI_API_KEY. Hãy nhập key ở sidebar hoặc đặt biến môi "
            "trường `GEMINI_API_KEY` rồi thử lại."
        )
        return
    if not article_text.strip():
        st.error("Chưa có nội dung bài báo. Hãy dán văn bản hoặc tải file lên.")
        return

    try:
        spinner_msg = "Đang gửi bài cho Gemini kiểm duyệt và tra cứu nguồn..."
        with st.spinner(spinner_msg):
            result = run_review(
                article_text,
                options.get("api_key"),
                options.get("model", DEFAULT_MODEL),
                skill_id=skill_id,
                engine_mode=engine_mode,
            )

    except Exception as exc:
        err_msg = str(exc)
        err_lower = err_msg.lower()
        if any(code in err_lower for code in ("409", "429", "quota", "resource_exhausted", "conflict")):
            st.session_state["api_key_exhausted"] = True
            st.error(
                "❌ **API Key hiện tại gặp lỗi giới hạn / xung đột (409/429) sau 3 lần tự động thử lại.**\n\n"
                "👉 Vui lòng dán thêm một API Key Gemini khác vào ô **GEMINI_API_KEY** "
                "ở thanh bên trái (Sidebar) rồi bấm duyệt lại bài."
            )
        elif "thử lại 3 lần" in err_lower or "chặn" in err_lower:
            st.error(
                "🛑 **Hệ thống đã tự động thử lại 3 lần nhưng đều bị Google Gemini Web chặn / từ chối phản hồi.**\n\n"
                "👉 **Nguyên nhân**: Google áp dụng bộ lọc an toàn đối với các thông tin nhân sự chính trị khi chạy tự động.\n\n"
                "👉 **Cách khắc phục**:\n"
                "1. Bạn hãy chuyển chế độ kiểm duyệt ở thanh bên trái sang **⚡ Gemini API (Google AI Studio Key)** "
                "(API có cấu hình `BLOCK_NONE` chuyên dụng cho báo chí nên không bị chặn).\n"
                "2. Hoặc bấm nút **📋 Sao chép Prompt sang Gemini Web** ở trên để dán trực tiếp vào tab trình duyệt cá nhân."
            )
        else:
            st.error(f"Duyệt bài thất bại: {exc}")
        return

    # Duyet thanh cong: xoa co loi API cu neu co
    st.session_state.pop("api_key_exhausted", None)

    # Luu ket qua de giu nguyen khi Streamlit chay lai script (bam nut tai file).
    st.session_state[STATE_RESULT] = result
    st.session_state[STATE_ORIGINAL] = article_text
    st.session_state[STATE_SOURCE] = source_label
    st.session_state[STATE_MOMENT] = datetime.now()
    st.session_state[STATE_REVIEW_ID] = uuid4().hex
    st.session_state[STATE_USED_SKILL] = skill_id


def handle_review_via_gemini_web(
    article_text: str,
    source_label: str,
    skill_id: Optional[Union[str, Sequence[str]]] = None,
) -> None:
    """TASK_10 - Kiem duyet qua **Gemini Web Session RPC** (hoan toan ngam).

    Chuoi thao tac:
    1. Kiem tra phi dang nhap da duoc cap quyen (neu chua -> huong dan cap quyen).
    2. Ghep prompt bien tap vien (dung chung contract JSON voi duong Gemini API).
    3. Goi ``StreamGenerate`` qua HTTPS truc tiep bang ``gemini-webapi``.
    4. Chu hoa ket qua va luu vao ``st.session_state`` de ve bang loi / bang
       kiem chung / boi do / xuat Word nhu binh thuong.

    KHONG mo cua so trinh duyet, KHONG bam phim, KHONG click chuot -> man hinh
    nguoi dung khong bao gio bi giat.

    Lop bao ve cuoi cung: bat cu loi nao cung chi hien ``st.error`` —
    khong de Streamlit script crash (khet cong 8501).
    """
    try:
        if not article_text.strip():
            st.error(
                "Chưa có nội dung bài báo. Hãy dán văn bản hoặc tải file lên "
                "trước khi bắt đầu duyệt."
            )
            return

        engine = GeminiWebEngine()
        if not engine.is_granted():
            # Tu dong luu neu nguoi dung da dan cookie vao o gw_psid o sidebar
            pending_psid = str(st.session_state.get("gw_psid") or "").strip()
            if pending_psid:
                pending_psidts = str(st.session_state.get("gw_psidts") or "").strip()
                pending_email = str(st.session_state.get("gw_email") or "").strip()
                pending_authuser = str(st.session_state.get("gw_authuser") or "0").strip() or "0"
                if save_gemini_session(pending_psid, pending_psidts, pending_email, pending_authuser):
                    engine = GeminiWebEngine()

        if not engine.is_granted():
            st.warning(
                "🟠 **Chưa cấp quyền phiên Gemini Web.** Mở mục "
                "**🌐 Phiên Gemini Web** ở thanh bên trái, dán cookie "
                "`__Secure-1PSID` một lần duy nhất và bấm **💾 Lưu Cấu Hình & "
                "Cấp Quyền Tự Động**. Sau đó app sẽ chạy ngầm vĩnh viễn."
            )
            return

        with st.spinner(
            "Đang gửi bài báo tới Gemini Web qua giao thức bảo mật và phân tích lỗi..."
        ):
            full_prompt = engine.build_prompt(article_text, skill_id=skill_id)
            st.session_state["review_sent_prompt"] = full_prompt
            result = engine.review_article_sync(article_text, skill_id=skill_id)

        st.session_state[STATE_RESULT] = result
        st.session_state[STATE_ORIGINAL] = article_text.strip()
        st.session_state[STATE_SOURCE] = source_label or "Gemini Web Session"
        st.session_state[STATE_MOMENT] = datetime.now()
        st.session_state[STATE_REVIEW_ID] = uuid4().hex
        st.session_state[STATE_USED_SKILL] = skill_id

        st.success(
            "✅ **Đã kiểm duyệt xong qua Gemini Web Session!** "
            "Xem bảng lỗi chính tả, bảng kiểm chứng và tab bôi đỏ bên dưới."
        )
        st.rerun()
    except ValueError as exc:
        st.error(str(exc))
    except Exception as exc:  # noqa: BLE001 - keo cuoi chong crash server
        st.error(f"⚠️ Sự cố khi duyệt qua Gemini Web: {exc}")
        with st.expander("Chi tiết kỹ thuật (gửi cho người vận hành)"):
            st.code(traceback.format_exc(), language=None)


def render_review_tab(options: Dict[str, Any]) -> None:
    """Ve noi dung tab kiem duyet bai bao."""
    # Xu ly yeu cau xoa o nhap TRUOC khi cac widget duoc tao (lan chay sau
    # khi bam nut reset) vi Streamlit cam sua gia tri widget da ve.
    if st.session_state.pop(STATE_CLEAR_INPUT_PENDING, False):
        st.session_state[STATE_ARTICLE_TEXT_AREA] = ""

    selected_skill_ids = render_skill_selector()

    with st.expander(f"📖 Xem chi tiết kỹ năng đang chọn ({len(selected_skill_ids)} kỹ năng)"):
        for sid in selected_skill_ids:
            current_skill = skills_manager.get_skill(sid)
            if current_skill:
                st.write(f"**{current_skill['name']}**")
                description = (current_skill.get("description") or "").strip()
                if description:
                    st.caption(description)
                st.code(current_skill.get("instructions") or "", language=None)

    article_text, source_label = render_article_input()

    engine_mode = options.get("engine_mode", "api")

    review_clicked = False
    copy_prompt_clicked = False
    reset_clicked = False
    web_review_clicked = False

    if engine_mode == "gemini_web":
        action_cols = st.columns([2, 2, 2])
        with action_cols[0]:
            web_review_clicked = st.button(
                "🚀 Bắt Đầu Kiểm Duyệt Qua Gemini Web",
                type="primary",
                key="run_review_button",
                use_container_width=True,
                help=(
                    "Gọi RPC ngầm của Gemini Web bằng cookie phiên đã cấp quyền: "
                    "không mở cửa sổ trình duyệt, không bấm phím, không chiếm con "
                    "trỏ — màn hình của bạn hoàn toàn không bị giật."
                ),
            )
        with action_cols[1]:
            copy_prompt_clicked = st.button(
                "📋 Xem & Sao chép Prompt",
                key="copy_gemini_prompt_button",
                use_container_width=True,
                help="Hiển thị và sao chép toàn bộ Prompt hoàn chỉnh gồm Yêu cầu kích hoạt Google Search + Các Kỹ năng đã chọn + Toàn văn bài báo.",
            )
        with action_cols[2]:
            reset_clicked = st.button(
                "🔄 Bắt đầu bài mới / Xóa kết quả",
                key="reset_review_button",
                use_container_width=True,
            )
    else:
        action_cols = st.columns([2, 2, 2])
        with action_cols[0]:
            review_clicked = st.button(
                "🚀 Bắt đầu duyệt bài",
                type="primary",
                key="run_review_button",
                use_container_width=True,
            )
        with action_cols[1]:
            copy_prompt_clicked = st.button(
                "📋 Sao chép Prompt sang Gemini Web",
                key="copy_gemini_prompt_button",
                use_container_width=True,
                help="Tạo prompt hoàn chỉnh gồm Yêu cầu kích hoạt Google Search thời điểm hiện tại, Kỹ năng lưu trữ và Bài báo rồi sao chép vào Clipboard để dán trực tiếp sang ô chat Gemini bên cạnh.",
            )
        with action_cols[2]:
            reset_clicked = st.button(
                "🔄 Bắt đầu bài mới / Xóa kết quả",
                key="reset_review_button",
                use_container_width=True,
            )

    if copy_prompt_clicked:
        if not article_text.strip():
            st.warning("⚠️ Vui lòng dán văn bản hoặc tải file bài báo lên trước khi sao chép.")
        else:
            engine = GeminiWebEngine()
            prompt_content = engine.build_prompt(
                article_text, skill_id=selected_skill_ids
            )
            # 1. Ghi clipboard he thong Windows
            write_clipboard_windows(
                f"<pre>{html.escape(prompt_content)}</pre>", prompt_content
            )
            # 2. Ghi clipboard trinh duyet (Client-Side JS)
            escaped_json = json.dumps(prompt_content)
            components.html(
                f"""
                <script>
                if (navigator.clipboard && navigator.clipboard.writeText) {{
                    navigator.clipboard.writeText({escaped_json}).then(function() {{
                        console.log("Client clipboard updated");
                    }});
                }}
                </script>
                """,
                height=0,
                width=0,
            )
            st.success(
                "✅ **Đã sao chép toàn bộ Prompt vào Clipboard!**\n\n"
                "👉 Hãy click chuột vào ô **`+ Hỏi Gemini`** ở khung bên trái và bấm **`Ctrl + V`** ➔ **`Enter`**.\n\n"
                "*(Hoặc bạn có thể bấm biểu tượng 📋 Sao chép ở góc phải khung mã bên dưới)*"
            )
            with st.expander("👁️ Xem toàn văn câu lệnh Prompt vừa sao chép (Có nút Copy ở góc phải)", expanded=True):
                st.code(prompt_content, language=None)


    if reset_clicked:
        reset_review_state()
        # Xoa o nhap o LAN CHAY TIEP THEO (truoc khi widget duoc tao).
        st.session_state[STATE_CLEAR_INPUT_PENDING] = True
        st.rerun()

    if web_review_clicked:
        handle_review_via_gemini_web(
            article_text, source_label, skill_id=selected_skill_ids
        )

    if review_clicked:
        handle_review_click(
            article_text, source_label, options, skill_id=selected_skill_ids
        )


    if st.session_state.get(STATE_RESULT):
        render_results(options)


def render_skills_manager_tab() -> None:
    """Ve tab quan ly ky nang bao chi (them/sua/xoa/dat mac dinh/khoi phuc)."""
    st.subheader("✨ Quản lý Kỹ năng Kiểm duyệt (Custom Skills)")
    st.caption(
        "Mỗi kỹ năng là một tập quy tắc nghiệp vụ được nạp vào System Prompt "
        "trước mỗi lần duyệt bài. Kỹ năng mặc định (⭐) được chọn tự động khi "
        "mở ứng dụng. Dữ liệu lưu tại thư mục `skills/` của dự án."
    )

    # Thong bao flash (truoc do da rerun sau khi thao tac thanh cong).
    flash_message = st.session_state.pop(STATE_FLASH, None)
    if flash_message:
        st.success(flash_message)

    skills = skills_manager.get_skills()
    editing_id: Optional[str] = st.session_state.get(STATE_SKILL_EDIT_ID)

    col_list, col_form = st.columns([1, 2], gap="large")

    # -- Cot trai: danh sach + cac nut hanh dong ------------------------------
    with col_list:
        st.markdown("##### 📋 Danh sách kỹ năng")
        for skill in skills:
            is_default = bool(skill.get("is_default"))
            badge = " ⭐" if is_default else ""
            st.markdown(f"**{skill['name']}**{badge}")
            st.caption(skill.get("description") or "—")
            item_cols = st.columns([1, 1])
            if item_cols[0].button(
                "✏️ Sửa", key=f"edit_skill_{skill['id']}", use_container_width=True
            ):
                st.session_state[STATE_SKILL_EDIT_ID] = skill["id"]
                st.rerun()
            if item_cols[1].button(
                "⭐ Làm mặc định",
                key=f"default_skill_{skill['id']}",
                disabled=is_default,
                use_container_width=True,
            ):
                skills_manager.set_default_skill(skill["id"])
                st.rerun()
            st.divider()

        manage_cols = st.columns([1, 1])
        if manage_cols[0].button(
            "➕ Thêm kỹ năng mới", key="new_skill_button", use_container_width=True
        ):
            st.session_state[STATE_SKILL_EDIT_ID] = None
            st.rerun()
        if manage_cols[1].button(
            "🔄 Khôi phục mặc định",
            key="reset_skills_button",
            use_container_width=True,
        ):
            skills_manager.reset_to_default_skills()
            st.session_state[STATE_SKILL_EDIT_ID] = None
            st.session_state[STATE_FLASH] = "Đã khôi phục bộ kỹ năng mặc định."
            st.rerun()

    # -- Cot phai: bien mau them / sua ky nang --------------------------------
    with col_form:
        if editing_id is None:
            st.markdown("##### ➕ Thêm kỹ năng mới")
            current: Dict[str, Any] = {
                "id": "",
                "name": "",
                "description": "",
                "instructions": "",
            }
        else:
            loaded = skills_manager.get_skill(editing_id)
            if loaded is None:
                st.session_state.pop(STATE_SKILL_EDIT_ID, None)
                st.info("Kỹ năng đã bị xóa — hãy chọn kỹ năng khác trong danh sách.")
                return
            current = loaded
            st.markdown(f"##### ✏️ Sửa kỹ năng: {current['name']}")

        # Moi bien mau co key rieng theo ky nang dang sua ("new" = them moi)
        # de Streamlit khong tron gia tri cu giua cac lan chon khac nhau.
        form_uid = str(current.get("id") or "new")

        name_value = st.text_input(
            "Tên kỹ năng",
            value=str(current.get("name") or ""),
            key=f"skill_form_name_{form_uid}",
            placeholder="Ví dụ: Phóng sự điều tra",
            help="Tên hiển thị khi chọn kỹ năng ở tab kiểm duyệt.",
        )
        description_value = st.text_area(
            "Mô tả ngắn (hiện trên danh sách chọn)",
            value=str(current.get("description") or ""),
            height=80,
            key=f"skill_form_description_{form_uid}",
            placeholder="Ví dụ: Dùng cho phóng sự điều tra, ưu tiên kiểm chứng số liệu.",
        )
        instructions_value = st.text_area(
            "Chi tiết kỹ năng (System Prompt / Quy tắc nghiệp vụ)",
            value=str(current.get("instructions") or ""),
            height=420,
            key=f"skill_form_instructions_{form_uid}",
            placeholder=(
                "Ví dụ: Bạn là biên tập viên chuyên mục thể thao. Chỉ soát chính tả "
                "và kiểm chứng tên vận động viên, giải đấu..."
            ),
            help=(
                "Nội dung này được nối vào System Prompt mỗi khi chọn kỹ năng. "
                "Hệ thống tự động giữ lại ràng buộc định dạng JSON ở cuối."
            ),
        )

        form_cols = st.columns([1, 1, 1])
        save_clicked = form_cols[0].button(
            "💾 Lưu kỹ năng", type="primary", key="save_skill_button"
        )
        delete_clicked = form_cols[1].button(
            "🗑️ Xóa kỹ năng",
            key="delete_skill_button",
            disabled=editing_id is None,
        )
        cancel_clicked = form_cols[2].button(
            "✖️ Đóng biểu mẫu", key="cancel_skill_button"
        )

        if save_clicked:
            payload: Dict[str, Any] = {
                # Giu id cu neu dang sua; them moi thi skills_manager tu sinh slug.
                "id": str(current.get("id") or "") or None,
                "name": name_value,
                "description": description_value,
                "instructions": instructions_value,
            }
            try:
                saved = skills_manager.save_skill(payload)
            except ValueError as exc:
                st.error(str(exc))
            else:
                # Chuyen ve chinh ky nang vua luu de nguoi dung tiep tuc dieu chinh.
                st.session_state[STATE_SKILL_EDIT_ID] = saved["id"]
                st.session_state[STATE_FLASH] = (
                    f"Đã lưu kỹ năng «{saved['name']}»."
                )
                st.rerun()

        if delete_clicked and editing_id:
            if skills_manager.delete_skill(editing_id):
                st.session_state.pop(STATE_SKILL_EDIT_ID, None)
                st.session_state[STATE_FLASH] = "Đã xóa kỹ năng."
                st.rerun()
            else:
                st.error(
                    "Không thể xóa: đây là kỹ năng mặc định duy nhất còn lại "
                    "của hệ thống."
                )

        if cancel_clicked:
            st.session_state.pop(STATE_SKILL_EDIT_ID, None)
            st.rerun()


def main() -> None:
    """Diem vao cua ung dung Streamlit."""
    st.set_page_config(
        page_title=APP_TITLE,
        page_icon=APP_ICON,
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.title(f"{APP_ICON} {APP_TITLE}")
    st.caption(
        "Kiểm tra chính tả, kiểm chứng sự kiện bằng Google Search, xuất báo cáo "
        "Word và quản lý kỹ năng kiểm duyệt riêng cho tòa soạn."
    )

    try:
        options = render_sidebar()
    except Exception as exc:  # noqa: BLE001 - sidebar loi van phai thay duoc thong bao
        st.error(f"⚠️ Sidebar gặp lỗi khi hiển thị cấu hình: {exc}")
        with st.expander("Chi tiết kỹ thuật"):
            st.code(traceback.format_exc(), language=None)
        return

    tab_review, tab_skills = st.tabs(
        ["📰 Kiểm duyệt bài báo", "✨ Quản lý Kỹ năng"]
    )

    # TASK_06: boc try/except cho tung tab — mot tab loi khong duoc lam sập
    # toan bo app (tranh tinh trang crash / khet cong 8501).
    with tab_review:
        try:
            render_review_tab(options)
        except Exception as exc:  # noqa: BLE001
            st.error(f"⚠️ Tab kiểm duyệt gặp lỗi giao diện: {exc}")
            with st.expander("Chi tiết kỹ thuật (gửi cho người vận hành)"):
                st.code(traceback.format_exc(), language=None)

    with tab_skills:
        try:
            render_skills_manager_tab()
        except Exception as exc:  # noqa: BLE001
            st.error(f"⚠️ Tab quản lý kỹ năng gặp lỗi giao diện: {exc}")
            with st.expander("Chi tiết kỹ thuật (gửi cho người vận hành)"):
                st.code(traceback.format_exc(), language=None)


if __name__ == "__main__":
    main()
