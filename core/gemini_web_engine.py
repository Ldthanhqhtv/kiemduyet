"""
core/gemini_web_engine.py - Trình kiểm duyệt bài báo qua Gemini Web Session RPC.

TASK_10
=======
Thay the hoan toan phuong an "dieu khien Chrome PWA bang GUI" (TASK_07/TASK_09)
bang **giao thuc Web Session RPC** cua thu vien ``gemini-webapi`` (v2.1.1, da
cai trong ``.venv``), ke thua tu repo tham khau
``E:\\kiem duyet bao\\tham khao\\gemini-web-api-main``.

Uu diem:
- **KHONG mo cua so trinh duyet**, KHONG giap tieu diem, KHONG bam phim,
  KHONG click chuot -> 0% giat man hinh, khong phai paste nham.
- Giao thuc ``StreamGenerate`` chay qua HTTPS truc tiep (TLS Chrome gia lap
  bang ``curl-cffi``).
- Dung 2 cookie phi dang nhap: ``__Secure-1PSID`` va ``__Secure-1PSIDTS``.
- **Cap quyen vinh vien**: luu cau hinh 1 lan duy nhat vao
  ``config/gemini_session.json``; tu do app tu chay ngam, khong bao gio hoi lai.
- **Chong xung dot tai khoan** (Gmail / multi-login) qua ``apply_authuser``:
  moi tai khoan Google duoc dinh tuyến bang duong dan ``/u/N/`` rieng.

.. important::
    Module nay **khong** dung ``ctypes.windll.user32``, khong dung
    ``keybd_event``, khong click chuot theo toa do, khong chuyen cua so OS.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple, Union

from gemini_webapi import GeminiClient
from gemini_webapi.constants import Endpoint as _E
from gemini_webapi.exceptions import APIError, AuthError

from core.reviewer import (
    EDITOR_SYSTEM_INSTRUCTION,
    EDITOR_USER_PROMPT_TEMPLATE,
    MAX_ARTICLE_CHARS,
    extract_json_from_text,
    normalize_review_result,
)
from core.skills_manager import get_active_skills_prompt_sync

LOGGER = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
#: File cau hinh phi dang nhap (cap quyen vinh vien).
CONFIG_PATH = BASE_DIR / "config" / "gemini_session.json"
ENC_CONFIG_PATH = BASE_DIR / "config" / "gemini_session.enc"

#: Model Gemini Web mac dinh (uu tien 3.8-flash, tu dong fallback).
DEFAULT_WEB_MODEL = "gemini-3.8-flash"

#: Ten model hien thi tren bao cao.
GEMINI_WEB_MODEL_NAME = "gemini-web (session-rpc)"

#: Cac module cua ``gemini_webapi`` that ``Endpoint`` (de doi duong dan /u/N/).
_ENDPOINT_MODULES = (
    "gemini_webapi.client",
    "gemini_webapi.utils.get_access_token",
    "gemini_webapi.utils.rotate_1psidts",
    "gemini_webapi.utils.upload_file",
)

#: Endpoint goc cua thu vien (de khoi phuc khi bo ghim).
_DEFAULT_ENDPOINT_CLASS = _E

#: Chi so tai khoan hien tai dang duoc ghim (``None`` = mac dinh).
_CURRENT_AUTHUSER: Optional[str] = None


# ---------------------------------------------------------------------------
# 1. Dinh tuyen tai khoan Google /u/N/ (Multi-login Shim)
# ---------------------------------------------------------------------------


def _endpoint_modules() -> list:
    """Tra ve danh sach module object cua ``gemini_webapi`` that ``Endpoint``.

    Phai lay **module object** tu ``sys.modules`` — ``import
    gemini_webapi.utils.get_access_token`` se bind *ham* (vi package ``__init__``
    re-export) nen patch se khong co tac dung.
    """
    for name in _ENDPOINT_MODULES:
        try:
            __import__(name)
        except Exception:  # noqa: BLE001 - module khong ton tai o phien ban nay
            continue
    return [sys.modules.get(name) for name in _ENDPOINT_MODULES]


def apply_authuser(index: str | int | None) -> None:
    """Dinh tuyen toan bo RPC cua ``gemini_webapi`` vao dung tai khoan ``/u/N/``.

    Google phan biet cac tai khoan da dang nhup bang **chi so** trong duong
    dan (``https://gemini.google.com/u/1/app``). ``gemini_webapi`` hardcode
    endpoint mac dinh (index 0) — neu nguoi dung dang mo Gmail bang tai khoan
    cong viec o index 0 va muon app chay bang tai khoan ca nhan o index 1 thi
    phai doi endpoint.

    Parameters
    ----------
    index:
        Chi so tai khoan (``"0"`` / ``""`` / ``None`` = tai khoan mac dinh).
        Moi chuoi rong hoac ``"0"`` se **khoi phuc** endpoint goc.
    """
    global _CURRENT_AUTHUSER

    modules = _endpoint_modules()
    raw = "" if index is None else str(index).strip()

    if raw in ("", "0"):
        # Khoi phuc endpoint goc (tai khoan mac dinh cua trinh duyet).
        for module in modules:
            if module is not None and hasattr(module, "Endpoint"):
                module.Endpoint = _DEFAULT_ENDPOINT_CLASS
        _CURRENT_AUTHUSER = None
        return

    prefix = f"https://gemini.google.com/u/{raw}"

    class _EndpointShim:
        GOOGLE = str(_E.GOOGLE)
        INIT = f"{prefix}/app"
        GENERATE = (
            f"{prefix}/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate"
        )
        ROTATE_COOKIES = str(_E.ROTATE_COOKIES)
        UPLOAD = str(_E.UPLOAD)
        BATCH_EXEC = f"{prefix}/_/BardChatUi/data/batchexecute"

    for module in modules:
        if module is not None and hasattr(module, "Endpoint"):
            module.Endpoint = _EndpointShim
    _CURRENT_AUTHUSER = raw


def current_authuser() -> Optional[str]:
    """Tra ve chi so tai khoan hien tai dang duoc ghim (None = mac dinh)."""
    return _CURRENT_AUTHUSER


# ---------------------------------------------------------------------------
# 2. Cau hinh phi dang nhap (cap quyen vinh vien)
# ---------------------------------------------------------------------------


def load_gemini_session(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Doc cau hinh phi dang nhap da duoc cap quyen.

    Thu tu uu tien:
    1. File config/gemini_session.json (neu co tren may local).
    2. Streamlit Secrets (st.secrets) neu chay tren Streamlit Cloud.
    3. File ma hoa config/gemini_session.enc (dong bo tu may local len mây).
    4. Bien moi truong.

    Returns
    -------
    dict
        ``{account_email, authuser, secure_1psid, secure_1psidts,
        auto_granted, updated_at}``.
    """
    path = Path(config_path) if config_path else CONFIG_PATH
    if path.is_file():
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict) and data.get("secure_1psid"):
                data.setdefault("authuser", "0")
                data.setdefault("account_email", "")
                data.setdefault("auto_granted", True)
                return data
        except (OSError, ValueError) as exc:
            LOGGER.warning("Khong doc duoc file config session: %s", exc)

    # 2. Doc tu Streamlit Secrets hoac file ma hoa chi khi dung duong dan mac dinh (khong phai test override)
    if config_path is None or Path(config_path) == CONFIG_PATH:
        try:
            import streamlit as st
            if hasattr(st, "secrets"):
                for psid_key in ("SECURE_1PSID", "GEMINI_1PSID", "secure_1psid"):
                    if psid_key in st.secrets:
                        psid_val = str(st.secrets[psid_key]).strip()
                        if psid_val:
                            ts_val = ""
                            for ts_key in ("SECURE_1PSIDTS", "GEMINI_1PSIDTS", "secure_1psidts"):
                                if ts_key in st.secrets:
                                    ts_val = str(st.secrets[ts_key]).strip()
                                    break
                            email_val = str(
                                st.secrets.get("GEMINI_ACCOUNT_EMAIL", "") or st.secrets.get("account_email", "")
                            ).strip()
                            authuser_val = str(
                                st.secrets.get("GEMINI_AUTHUSER", "0") or st.secrets.get("authuser", "0")
                            ).strip()
                            return {
                                "secure_1psid": psid_val,
                                "secure_1psidts": ts_val or None,
                                "account_email": email_val or "luuthanh.quochoi@gmail.com",
                                "authuser": authuser_val or "0",
                                "auto_granted": True,
                                "updated_at": "from_secrets",
                            }
        except Exception:
            pass

        # 3. Doc tu file ma hoa config/gemini_session.enc
        try:
            from core.gemini_session_sync import load_encrypted_session
            enc_data = load_encrypted_session(ENC_CONFIG_PATH)
            if enc_data and enc_data.get("secure_1psid"):
                enc_data.setdefault("authuser", "0")
                enc_data.setdefault("account_email", "luuthanh.quochoi@gmail.com")
                enc_data.setdefault("auto_granted", True)
                return enc_data
        except Exception as exc:
            LOGGER.debug("Khong doc duoc enc session: %s", exc)

    # 4. Fallback: bien moi truong.
    return {
        "secure_1psid": os.getenv("GEMINI_1PSID", "").strip() or None,
        "secure_1psidts": os.getenv("GEMINI_1PSIDTS", "").strip() or None,
        "account_email": os.getenv("GEMINI_ACCOUNT_EMAIL", "").strip() or "",
        "authuser": os.getenv("GEMINI_AUTHUSER", "0").strip() or "0",
        "auto_granted": True,
        "updated_at": "",
    }


def save_gemini_session(
    secure_1psid: str,
    secure_1psidts: str = "",
    account_email: str = "",
    authuser: str = "0",
    config_path: Optional[Path] = None,
) -> bool:
    """Luu cau hinh phi dang nhap (cap quyen vinh vien) vao file JSON va file ma hoa ENC.

    Sau khi luu mot lan, app chay ngam ma **khong hoi lai**.

    Returns
    -------
    bool
        ``True`` neu ghi file thanh cong.
    """
    if not str(secure_1psid or "").strip():
        return False

    path = Path(config_path) if config_path else CONFIG_PATH
    payload = {
        "account_email": str(account_email or "").strip(),
        "authuser": str(authuser or "0").strip() or "0",
        "secure_1psid": str(secure_1psid).strip(),
        "secure_1psidts": str(secure_1psidts or "").strip(),
        "auto_granted": True,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    ok = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        ok = True
    except OSError as exc:
        LOGGER.error("Khong ghi duoc file config session %s: %s", path, exc)

    # Dong thoi tao file ma hoa config/gemini_session.enc de dong bo len Streamlit Cloud
    try:
        from core.gemini_session_sync import save_encrypted_session
        save_encrypted_session(payload, ENC_CONFIG_PATH)
    except Exception as exc:
        LOGGER.warning("Khong tao duoc file ma hoa enc: %s", exc)

    return ok


# ---------------------------------------------------------------------------
# 3. Engine
# ---------------------------------------------------------------------------


class GeminiWebEngine:
    """Engine goi Gemini Web RPC ngam bang session cookies da cap quyen toan phan."""

    def __init__(self, config_path: Optional[Path] = None) -> None:
        self.config_path: Optional[Path] = Path(config_path) if config_path else None
        self._client: Optional[GeminiClient] = None
        self._lock = asyncio.Lock()

    # -- Cau hinh -----------------------------------------------------------
    def get_session_config(self) -> Dict[str, Any]:
        """Lay cau hinh phi dang nhap da duoc cap quyen tu file JSON hoac env."""
        return load_gemini_session(self.config_path)

    def is_granted(self) -> bool:
        """``True`` neu phi dang nhap da duoc cap quyen (app chay ngam duoc)."""
        config = self.get_session_config()
        return bool(config.get("secure_1psid"))

    # -- Client -------------------------------------------------------------
    async def init_client(self, force_refresh: bool = False) -> GeminiClient:
        """Khoi tao ``GeminiClient`` voi co che dinh tuyen tai khoan.

        ``auto_refresh=False`` — **bat buoc** khi cookie lay tu phien Chrome
        dang chay: Chrome la "chu so hop" cua cookie nen no tu refresh, app
        khong duoc tu rotate (``rotate_1psidts``) vi se lam vang phien dang
        nhap Gmail cua nguoi dung.
        """
        async with self._lock:
            if self._client is not None and not force_refresh:
                return self._client

            config = self.get_session_config()
            psid = config.get("secure_1psid")
            psidts = config.get("secure_1psidts")
            authuser = config.get("authuser", "0")

            if not psid:
                raise AuthError(
                    "Chưa cấu hình Cookie Google Gemini! Vui lòng nhập "
                    "__Secure-1PSID trong cài đặt."
                )

            # Dinh tuyen tai khoan Google /u/N/ de khong dung dot Gmail.
            apply_authuser(authuser)

            client = GeminiClient(secure_1psid=psid, secure_1psidts=psidts)
            await client.init(timeout=120, auto_refresh=False)
            self._client = client
            return client

    async def close_client(self) -> None:
        """Dong client (giai phong ket noi)."""
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            await client.close()
        except Exception:  # noqa: BLE001 - dong client la thao tac don le
            LOGGER.debug("Dong GeminiClient that bai (khong anh huong)", exc_info=True)

    # -- Kiem tra ket noi ----------------------------------------------------
    async def test_connection_async(self) -> Tuple[bool, str]:
        """Gui prompt kiem tra ket noi toi Gemini Web."""
        try:
            client = await self.init_client(force_refresh=True)
            target_model = self._resolve_target_model(client, DEFAULT_WEB_MODEL)
            result = await client.generate_content(
                "Ping: Phản hồi đúng 2 chữ 'PONG OK'",
                model=target_model,
            )
            text = (getattr(result, "text", "") or "").strip()
            return True, f"Kết nối thành công! Phản hồi từ Gemini: {text[:60]}"
        except AuthError as exc:
            return False, f"Lỗi xác thực Cookie: {exc}"
        except Exception as exc:  # noqa: BLE001 - bao loi cho nguoi dung
            err_str = str(exc)
            if any(k in err_str.lower() for k in ("unauthenticated", "permission denied", "cookie", "auth")):
                return False, f"Phiên Cookie đã hết hiệu lực hoặc bị từ chối: {exc}"
            return False, f"Lỗi kết nối Gemini Web: {exc}"

    def test_connection_sync(self) -> Tuple[bool, str]:
        """Ham dong bo an toan de goi tu Streamlit UI."""
        return self._run_async(self.test_connection_async())

    # -- Kiem duyet bai bao --------------------------------------------------
    def build_prompt(
        self,
        article_text: str,
        skill_id: Optional[Union[str, Sequence[str]]] = None,
        custom_instruction: Optional[str] = None,
    ) -> str:
        """Ghép prompt biên tập viên với Yêu cầu kích hoạt Google Search Grounding & Custom Skill.

        Thứ tự ưu tiên:
        1. [YÊU CẦU KÍCH HOẠT GOOGLE SEARCH GROUNDING]: Dẫn xuất trực tiếp ở dòng 1
           để ép router của Gemini Web bật Google Search thời gian thực.
        2. [CHUYÊN GIA BIÊN TẬP]: System instruction quy định vai trò.
        3. [KỸ NĂNG BÁO CHÍ (CUSTOM SKILL)]: Nạp hướng dẫn nghiệp vụ (ví dụ T3).
        4. [CHỈ DẪN SOÁT LỖI & KIỂM CHỨNG]: Răn đe nghiêm ngặt về lỗi gõ máy,
           thiếu chữ cái (uốc hội -> Quốc hội), quy tắc viết hoa tên người
           (nguyễn trung tùng -> Nguyễn Trung Tùng) và kiểm chứng chức danh đương nhiệm.
        5. [BÀI BÁO & CẤU TRÚC JSON]: Trả về kết quả chuẩn hóa cho hệ thống.
        """
        # 1. Yêu cầu kích hoạt Google Search thời gian thực (đặt ngay đầu prompt)
        search_trigger = (
            "Hãy dùng Google Tìm kiếm để tra cứu, kiểm chứng TẤT CẢ các nhân vật có "
            "chức danh đính kèm xuất hiện trong bài báo (bao gồm lãnh đạo Đảng, Nhà nước, "
            "các bộ ngành, chính quyền địa phương, cho đến lãnh đạo cơ quan, ban ngành, đoàn thể, "
            "viện nghiên cứu, trường học, bệnh viện, doanh nghiệp, tập đoàn, hiệp hội, chuyên gia...), "
            "cùng các sự kiện, phát ngôn, địa danh và số liệu trước khi trả lời. "
            "BẮT BUỘC TRA CỨU GOOGLE THỜI GIAN THỰC."
        )

        # 2. Vai trò biên tập viên chuyên nghiệp
        system_text = str(custom_instruction or "").strip() or EDITOR_SYSTEM_INSTRUCTION

        # 3. Kỹ năng báo chí đang áp dụng (Custom Skill)
        skills_text = get_active_skills_prompt_sync(skill_id=skill_id)

        # 4. Chỉ dẫn nghiệp vụ bắt buộc: rà soát từng từ, bắt lỗi viết thường và gõ sai
        strict_rules = (
            "[CHỈ DẪN NGHIỆP VỤ BẮT BUỘC - SOÁT LỖI & KIỂM CHỨNG]:\n"
            "1. SOÁT LỖI CHÍNH TẢ, GÕ MÁY & QUY TẮC VIẾT HOA TIẾNG VIỆT:\n"
            "   - Rà soát từng từ, từng câu để phát hiện lỗi gõ máy, mất chữ, thiếu ký tự, lỗi đảo phím Telex/VNI "
            "(Ví dụ: 'cwo bản' gõ nhầm Telex -> Bắt buộc sửa thành 'cơ bản'; 'sng' -> 'sáng'; 'uốc hội' thiếu chữ 'Q' -> 'Quốc hội').\n"
            "   - Kiểm tra nghiêm ngặt quy tắc viết hoa danh từ riêng, tên người, tên cơ quan: "
            "(Ví dụ: 'nguyễn trung tùng' viết thường là sai -> Bắt buộc sửa thành 'Nguyễn Trung Tùng'; "
            "'quốc hội' phải viết hoa là 'Quốc hội').\n"
            "   - Kiểm tra lỗi dấu thanh hỏi/ngã, từ đồng âm, dùng sai từ.\n"
            "   - TẤT CẢ các lỗi trên PHẢI được ghi nhận vào danh sách spelling_errors.\n\n"
            "2. KIỂM CHỨNG TẤT CẢ CÁC NHÂN VẬT CÓ CHỨC DANH ĐÍNH KÈM (REAL-TIME GOOGLE SEARCH):\n"
            "   - BẤT KỲ NHÂN VẬT NÀO có chức danh, chức vụ, học vị hoặc nghề nghiệp đi kèm "
            "(Ví dụ: Bí thư, Chủ tịch, Bộ trưởng, Thứ trưởng, Trưởng ban, Giám đốc Sở, "
            "Chủ tịch HĐQT, Tổng giám đốc, Giám đốc điều hành, Viện trưởng, Hiệu trưởng, Giáo sư, Bác sĩ trưởng khoa...) "
            "ĐỀU PHẢI ĐƯỢC TRA CỨU KIỂM CHỨNG TRÊN GOOGLE.\n"
            "   - Trích xuất từng cặp [Họ tên] + [Chức danh] + [Cơ quan/Đơn vị/Doanh nghiệp] để đối chiếu "
            "với dữ liệu mới nhất từ Cổng thông tin chính thức, báo chí chính thống hoặc website cơ quan.\n"
            "   - Nếu chức danh/nhân sự đúng với thực tế hiện nay, xác nhận ĐÚNG kèm thông tin "
            "chức vụ thực tế và đường dẫn tham chiếu. Tránh nghi ngờ vô căn cứ đối với nhân vật và chức danh có thật.\n"
            "   - Nếu chức danh sai, lỗi thời, gán ghép nhầm hoặc nhân vật không giữ vị trí nêu trên, "
            "BẮT BUỘC cảnh báo sai lệch và hướng dẫn sửa chuẩn.\n\n"
            "3. TUYỆT ĐỐI KHÔNG IN LẠI TOÀN VĂN BÀI BÁO: Chỉ tập trung phát hiện lỗi và trả về đúng đối tượng JSON "
            "gồm spelling_errors và fact_checks (không tạo lại toàn văn bài báo để tiết kiệm token và tăng tối đa tốc độ)."
        )

        # 5. Bài báo & JSON schema
        user_text = EDITOR_USER_PROMPT_TEMPLATE.format(article_text=article_text)

        parts = [search_trigger, system_text]
        if skills_text:
            parts.append(skills_text)
        parts.append(strict_rules)
        parts.append(user_text)
        return "\n\n".join(parts)

    async def review_article_async(
        self,
        article_text: str,
        custom_instruction: Optional[str] = None,
        skill_id: Optional[Union[str, Sequence[str]]] = None,
    ) -> Dict[str, Any]:
        """Gửi bài báo kiểm duyệt và trích xuất kết quả chuẩn hóa (JSON + Markdown fallback)."""
        if not isinstance(article_text, str) or not article_text.strip():
            raise ValueError("Văn bản bài báo rỗng.")
        if len(article_text) > MAX_ARTICLE_CHARS:
            raise ValueError(
                f"Bài báo quá dài ({len(article_text)} ký tự > {MAX_ARTICLE_CHARS})."
            )

        full_prompt = self.build_prompt(
            article_text,
            skill_id=skill_id,
            custom_instruction=custom_instruction,
        )

        client = await self.init_client()
        target_model = self._resolve_target_model(client, DEFAULT_WEB_MODEL)
        try:
            response = await client.generate_content(
                full_prompt, model=target_model, extended_thinking=False
            )
            raw_text = getattr(response, "text", "") or ""
        except (AuthError, APIError) as exc:
            LOGGER.warning("Lỗi phiên làm việc, thử khởi tạo lại client: %s", exc)
            client = await self.init_client(force_refresh=True)
            target_model = self._resolve_target_model(client, DEFAULT_WEB_MODEL)
            response = await client.generate_content(
                full_prompt, model=target_model, extended_thinking=False
            )
            raw_text = getattr(response, "text", "") or ""

        citations = self._collect_citations(response)

        realtime_query = ["Tra cứu Google thời gian thực (chinhphu.vn / báo chí chính thống)"]

        # 1. Thử trích xuất JSON trước
        try:
            parsed_json = extract_json_from_text(raw_text)
            norm = normalize_review_result(
                parsed_json,
                model=GEMINI_WEB_MODEL_NAME,
                grounding_sources=citations,
                search_queries=realtime_query,
                raw_response_text=raw_text,
            )
            if norm.get("spelling_errors") or norm.get("fact_checks"):
                return norm
        except Exception:
            pass

        # 2. Fallback sang bộ bóc tách Markdown linh hoạt (hỗ trợ cả bảng T3)
        try:
            from core.gemini_web_reviewer import parse_gemini_web_response
            parsed = parse_gemini_web_response(raw_text, original_article=article_text)
            if citations:
                existing = parsed.get("grounding_sources") or []
                existing_uris = {str(item.get("uri") or "") for item in existing}
                for c in citations:
                    if str(c.get("uri") or "") not in existing_uris:
                        existing.append(c)
                parsed["grounding_sources"] = existing
            parsed["model"] = GEMINI_WEB_MODEL_NAME
            parsed["search_queries"] = realtime_query
            return parsed
        except Exception as exc:
            LOGGER.warning("Fallback parse_gemini_web_response không thành công: %s", exc)

        # Cuối cùng: trả về kết quả rỗng chuẩn hóa kèm raw_text
        return normalize_review_result(
            {},
            model=GEMINI_WEB_MODEL_NAME,
            grounding_sources=citations,
            search_queries=realtime_query,
            raw_response_text=raw_text,
        )

    @staticmethod
    def _resolve_target_model(client: GeminiClient, preferred_name: str = "gemini-3.8-flash") -> Any:
        """Chon model uu tien nhat ma tai khoan nguoi dung thuc te cung cap."""
        resolve = getattr(client, "resolve_model", None)
        if resolve:
            for candidate in (preferred_name, "gemini-3-flash", "gemini-flash", "gemini-auto"):
                try:
                    return resolve(candidate)
                except Exception:
                    continue
        return None

    @staticmethod
    def _collect_citations(response: Any) -> list:
        """Lay danh sach nguon trich dan (citations) neu co."""
        citations = getattr(response, "citations", None) or []
        collected = []
        for item in citations:
            if isinstance(item, dict):
                collected.append(
                    {
                        "uri": str(item.get("uri") or item.get("url") or ""),
                        "title": str(item.get("title") or ""),
                    }
                )
        return collected

    def review_article_sync(
        self,
        article_text: str,
        custom_instruction: Optional[str] = None,
        skill_id: Optional[Union[str, Sequence[str]]] = None,
    ) -> Dict[str, Any]:
        """Wrapper dong bo an toan voi event loop cua Streamlit."""
        return self._run_async(
            self.review_article_async(
                article_text=article_text,
                custom_instruction=custom_instruction,
                skill_id=skill_id,
            )
        )

    # -- Tien ich event loop -------------------------------------------------
    def _run_async(self, coro: Any) -> Any:
        """Chay coroutine an toan, khong xung dot loop Streamlit."""
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


__all__ = [
    "apply_authuser",
    "current_authuser",
    "load_gemini_session",
    "save_gemini_session",
    "GeminiWebEngine",
    "CONFIG_PATH",
    "DEFAULT_WEB_MODEL",
    "GEMINI_WEB_MODEL_NAME",
]
