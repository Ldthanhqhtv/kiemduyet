"""
core/skills_manager.py
======================

Module quan ly ky nang bao chi (Custom Skills) cho he thong "Kiem Duyet Bai
Bao AI", giong nhu tinh nang Custom Gems cua Google Gemini.

Nhiem vu
--------
- Luu tru be ngoai (persistent) danh sach ky nang bao chi trong thu muc
  ``skills/`` o goc du an (file duy nhat ``skills/skills.json``).
- Cung cap API CRUD: them, sua, xoa, chon ky nang mac dinh, khoi phuc mac dinh.
- Khoi tao san ky nang goc ``duyet-bai-bao`` lay chuan 100% tu anh cham
  Gemini cua Tong bien tap.

Rang buoc kien truc (drive_constraint & portability)
---------------------------------------------------
- Duong dan luu tru duoc tinh tu vi tri file module
  (``Path(__file__).resolve().parent``), KHONG hardcode o dia tuyet doi — copy
  ca thu muc du an sang may khac (hoac cai USB) thi van hoat dong binh thuong.
- Moi thu luu tren o E (khong ghi gi vao o C dang bi dong bang).

Schema mot ky nang (Skill Schema)
---------------------------------
::

    {
      "id": "duyet-bai-bao",          # ma slug, duy nhat
      "name": "duyet-bai-bao",
      "description": "Sử dụng để kiểm soát...",
      "instructions": "Bạn là một Biên tập viên...",
      "is_default": true,             # ky nang duoc nap mac dinh
      "created_at": "2026-10-17T00:00:00",
      "updated_at": "2026-10-17T00:00:00"
    }

API cua module
--------------
- ``get_skills()``                    -> danh sach tat ca ky nang.
- ``get_skill(skill_id)``             -> chi tiet mot ky nang (None neu khong co).
- ``save_skill(skill_data)``          -> them moi hoac cap nhat ky nang.
- ``delete_skill(skill_id)``          -> xoa ky nang (chan xoa ky nang mac dinh duy nhat).
- ``set_default_skill(skill_id)``     -> dat ky nang lam mac dinh.
- ``get_default_skill()``             -> lay ky nang mac dinh.
- ``reset_to_default_skills()``       -> khoi phuc lai cac ky nang goc chuan.

Moi ham deu ho tro tham so tuy chon ``skills_file`` (duong dan file JSON) de
unit test co the dung thu muc tam thoi, khong lam hong du lieu that.

Chay test:
    & ".venv\\Scripts\\python.exe" -m unittest core.test_skills_manager -v
"""

from __future__ import annotations

import json
import re
import unicodedata
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

# ---------------------------------------------------------------------------
# Hang so duong dan (tinh tu vi tri module — portable, khong hardcode o dia)
# ---------------------------------------------------------------------------

#: Thu muc goc du an (cha cua thu muc ``core/``).
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

#: Thu muc luu cac ky nang bao chi.
SKILLS_DIR: Path = PROJECT_ROOT / "skills"

#: File JSON chua toan bo ky nang.
SKILLS_FILE: Path = SKILLS_DIR / "skills.json"

# ---------------------------------------------------------------------------
# Ky nang goc chuan (lay 100% tu anh cham Gemini cua Tong bien tap)
# ---------------------------------------------------------------------------

DEFAULT_SKILL_SEED: Dict[str, Any] = {
    "id": "duyet-bai-bao",
    "name": "duyet-bai-bao",
    "description": (
        "Sử dụng để kiểm soát lỗi chính tả Tiếng Việt và kiểm tra tính "
        "chính xác nhân vật, sự kiện"
    ),
    "instructions": (
        "Bạn là một Biên tập viên tin tức kỳ cựu và Chuyên gia Kiểm chứng "
        "thông tin (Fact-checker).\n"
        "\n"
        "Mỗi khi nhận được văn bản hoặc bài báo, hãy tự động thực hiện "
        "các bước sau:\n"
        "\n"
        "1. SOÁT LỖI CHÍNH TẢ & NGỮ PHÁP TIẾNG VIỆT:\n"
        "- Lập bảng gồm 3 cột: [Lỗi sai], [Vị trí/Ngữ cảnh], "
        "[Gợi ý sửa chuẩn].\n"
        "- Kiểm tra kỹ dấu thanh (hỏi/ngã), từ đồng âm, quy tắc viết hoa "
        "tên cơ quan, tổ chức, chức danh.\n"
        "\n"
        "2. KIỂM CHỨNG NHÂN VẬT & SỰ KIỆN (FACT-CHECKING):\n"
        "- Hãy chủ động sử dụng Google Search để kiểm tra chéo các chi "
        "tiết trong bài:\n"
        "+ Tên nhân vật, chức vụ hiện tại hoặc tại thời điểm sự kiện.\n"
        "+ Thời gian, địa điểm diễn ra sự kiện.\n"
        "+ Các số liệu, mốc lịch sử hoặc phát ngôn quan trọng.\n"
        "- Trả về danh sách cảnh báo: Chỉ rõ chi tiết nào nghi ngờ sai "
        "lệch, thông tin thực tế theo báo chí chính thống là gì và kèm "
        "đường dẫn nguồn (nếu có).\n"
        "\n"
        "3. KẾT LUẬN BIÊN TẬP:\n"
        "- Đánh giá ngắn gọn bài viết: \"Đạt chuẩn xuất bản\" hay "
        "\"Cần biên tập lại\"."
    ),
    "is_default": True,
}

#: Danh sach ky nang goc (mac dinh) — duoc dung khi khoi tao / khoi phuc.
DEFAULT_SKILLS: List[Dict[str, Any]] = [DEFAULT_SKILL_SEED]


# ---------------------------------------------------------------------------
# Tien ich noi bo
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Lay thoi diem hien tai dang ISO string."""
    return datetime.now().isoformat(timespec="seconds")


def _resolve_file(skills_file: Optional[Path]) -> Path:
    """Chon file luu tru: tham so truyen vao hoac mac dinh ``skills/skills.json``."""
    return Path(skills_file) if skills_file else SKILLS_FILE


def _ensure_parent_dir(path: Path) -> None:
    """Dam bao thu muc cha cua file luu tru ton tai (mkdir -p)."""
    path.parent.mkdir(parents=True, exist_ok=True)


def slugify(text: str) -> str:
    """Tao ma slug don gian tu ten ky nang (bo dau tieng Viet).

    Vi du: ``"Chính tả siêu tốc"`` -> ``"chinh-ta-sieu-toc"``.
    """
    normalized = unicodedata.normalize("NFKD", str(text or ""))
    # NFKD khong phan tich duoc chu "D/đ" (D strok) — tu dong hoa truoc.
    normalized = normalized.replace("Đ", "D").replace("đ", "d")
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return slug or "ky-nang-moi"


def _normalize(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Chuan hoa mot ky nang: day du truong, thoi gian ISO."""
    now = _now_iso()
    created = str(entry.get("created_at") or "").strip() or now
    return {
        "id": str(entry.get("id") or "").strip(),
        "name": str(entry.get("name") or "").strip(),
        "description": str(entry.get("description") or "").strip(),
        "instructions": str(entry.get("instructions") or "").strip(),
        "is_default": bool(entry.get("is_default", False)),
        "created_at": created,
        "updated_at": str(entry.get("updated_at") or "").strip() or created,
    }


def _is_valid(entry: Any) -> bool:
    """Kiem tra mot doi tuong co phai la ky nang hop le (co ten + chi dan)."""
    if not isinstance(entry, dict):
        return False
    name = str(entry.get("name") or "").strip()
    instructions = str(entry.get("instructions") or "").strip()
    return bool(name) and bool(instructions)


def _seed_record() -> Dict[str, Any]:
    """Tao mot ban sao ky nang goc co thoi gian hien tai."""
    record = _normalize(deepcopy(DEFAULT_SKILL_SEED))
    record["id"] = DEFAULT_SKILL_SEED["id"]
    record["name"] = DEFAULT_SKILL_SEED["name"]
    record["is_default"] = True
    return record


def build_default_skills() -> List[Dict[str, Any]]:
    """Tra ve danh sach ky nang goc (mac dinh) — dang san sang de luu vao file.

    Moi lan goi tra ve ban sao moi (copy) de nguoi goi co the tu do sua doi
    ma khong lam hong du lieu goc.
    """
    return [_seed_record()]


def _ensure_single_default(skills: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Dam bao danh sach luon co dung mot ky nang ``is_default``."""
    default_ids = [s["id"] for s in skills if s.get("is_default")]
    if not default_ids and skills:
        skills[0]["is_default"] = True
    elif len(default_ids) > 1:
        keep = default_ids[0]
        for skill in skills:
            if skill["id"] != keep:
                skill["is_default"] = False
    return skills


def _read_skills(skills_file: Optional[Path]) -> List[Dict[str, Any]]:
    """Doc danh sach ky nang tu file.

    Neu file khong ton tai, bi hong JSON hoac khong co ky nang hop le nao
    thi tu dong khoi tao lai bang ky nang goc (khong de app bi loi khi
    nguoi dung copy thu muc moi sang may khac).
    """
    path = _resolve_file(skills_file)

    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = None

        entries: Any = None
        if isinstance(raw, dict):
            entries = raw.get("skills")
        elif isinstance(raw, list):
            entries = raw

        if isinstance(entries, list):
            valid = [_normalize(e) for e in entries if _is_valid(e)]
            if valid:
                return _ensure_single_default(valid)

    # Duong dan suy doi: khoi tao lai ky nang goc.
    skills = [_seed_record()]
    _write_skills(skills, path)
    return skills


def _write_skills(skills: List[Dict[str, Any]], path: Path) -> None:
    """Ghi danh sach ky nang ra file JSON (UTF-8, hien thi dau tieng Viet)."""
    _ensure_parent_dir(path)
    payload = {"skills": skills}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# API cong khai
# ---------------------------------------------------------------------------


def get_skills(skills_file: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Lay danh sach tat ca ky nang da luu tru.

    Parameters
    ----------
    skills_file:
        Duong dan file JSON tuy chon (dung cho test). Mac dinh la
        ``skills/skills.json`` cua du an.

    Returns
    -------
    list
        Danh sach dict ky nang, sap xep theo ``created_at`` tang dan.
    """
    skills = _read_skills(skills_file)
    skills.sort(key=lambda r: r.get("created_at", ""))
    return skills


def get_skill(
    skill_id: str,
    skills_file: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Lay chi tiet mot ky nang theo ``id``.

    Parameters
    ----------
    skill_id:
        Ma dinh danh slug cua ky nang (vi du: ``duyet-bai-bao``).

    Returns
    -------
    dict or None
        Dict ky nang neu tim thay; nguoc lai ``None``.
    """
    target = str(skill_id or "").strip()
    if not target:
        return None
    for record in _read_skills(skills_file):
        if record["id"] == target:
            return record
    return None


def get_skill_by_id(
    skill_id: str,
    skills_file: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Alias cua ``get_skill`` (TASK_07 - Gemini App automation).

    Giu ten ham ro nghia ``get_skill_by_id`` cho cac module tu dong hoa ben
    ngoai, tranh phai sua lai chu ky ``get_skill`` da duoc dung rong rai.
    """
    return get_skill(skill_id, skills_file)


def save_skill(
    skill_data: Dict[str, Any],
    skills_file: Optional[Path] = None,
) -> Dict[str, Any]:
    """Them moi hoac cap nhat mot ky nang (upsert theo ``id``).

    Parameters
    ----------
    skill_data:
        Dict chua it nhat ``name`` va ``instructions``. Co the co ``id``,
        ``description``, ``is_default``. Neu thieu ``id`` thi tu dong sinh
        slug tu ``name``.
    skills_file:
        Duong dan file JSON tuy chon (dung cho test).

    Returns
    -------
    dict
        Ky nang da duoc luu (co day du ``created_at``/``updated_at``).

    Raises
    ------
    ValueError
        Neu ``skill_data`` khong phai dict, hoac ``name``/``instructions`` rong.
    """
    if not isinstance(skill_data, dict):
        raise ValueError("skill_data phai la mot dict hop le.")

    name = str(skill_data.get("name") or "").strip()
    instructions = str(skill_data.get("instructions") or "").strip()
    if not name:
        raise ValueError("Ky nang bat buoc phai co ten (name).")
    if not instructions:
        raise ValueError("Ky nang bat buoc phai co chi dan (instructions).")

    path = _resolve_file(skills_file)
    skill_id = str(skill_data.get("id") or "").strip() or slugify(name)
    now = _now_iso()

    skills = _read_skills(path)
    existing = next((r for r in skills if r["id"] == skill_id), None)

    if existing is not None:
        existing["name"] = name
        existing["description"] = str(skill_data.get("description") or "").strip()
        existing["instructions"] = instructions
        existing["is_default"] = bool(
            skill_data.get("is_default", False) or existing.get("is_default", False)
        )
        existing["updated_at"] = now
        saved = existing
    else:
        record: Dict[str, Any] = {
            "id": skill_id,
            "name": name,
            "description": str(skill_data.get("description") or "").strip(),
            "instructions": instructions,
            "is_default": bool(skill_data.get("is_default", False)),
            "created_at": now,
            "updated_at": now,
        }
        skills.append(record)
        saved = record

    # Neu ky nang nay duoc dat la mac dinh, huy mac dinh cua cac ky nang khac.
    if saved["is_default"]:
        for record in skills:
            if record["id"] != skill_id:
                record["is_default"] = False

    _ensure_single_default(skills)
    _write_skills(skills, path)
    return saved


def delete_skill(
    skill_id: str,
    skills_file: Optional[Path] = None,
) -> bool:
    """Xoa mot ky nang theo ``id``.

    Khong cho phep xoa neu do la ky nang mac dinh duy nhat (truong hop
    ``is_default=True`` va la ky nang con lai trong danh sach).

    Returns
    -------
    bool
        ``True`` neu xoa thanh cong; ``False`` neu khong tim thay hoac bi chan.
    """
    target = str(skill_id or "").strip()
    path = _resolve_file(skills_file)
    skills = _read_skills(path)
    victim = next((r for r in skills if r["id"] == target), None)
    if victim is None:
        return False

    if len(skills) == 1 and victim.get("is_default", False):
        # Ky nang mac dinh duy nhat -> khong duoc phep xoa.
        return False

    remaining = [r for r in skills if r["id"] != target]

    # Neu xoa ky nang mac dinh, dat ky nang dau tien con lai lam mac dinh moi.
    if victim.get("is_default") and remaining:
        for record in remaining:
            record["is_default"] = False
        remaining[0]["is_default"] = True

    _ensure_single_default(remaining)
    _write_skills(remaining, path)
    return True


def set_default_skill(
    skill_id: str,
    skills_file: Optional[Path] = None,
) -> bool:
    """Dat mot ky nang lam mac dinh.

    Returns
    -------
    bool
        ``True`` neu cap nhat thanh cong; ``False`` neu khong tim thay.
    """
    target = str(skill_id or "").strip()
    path = _resolve_file(skills_file)
    skills = _read_skills(path)
    found = False
    for record in skills:
        record["is_default"] = record["id"] == target
        if record["is_default"]:
            found = True
    if found:
        _write_skills(skills, path)
    return found


def get_active_skills_prompt_sync(
    skill_id: Optional[Union[str, Sequence[str]]] = None,
    skills_file: Optional[Path] = None,
) -> str:
    """Trả về phần hướng dẫn kỹ năng báo chí sẵn sàng ghép vào prompt.

    Hỗ trợ cả kỹ năng đơn lẻ (str) và nhiều kỹ năng đồng thời (Sequence[str]).
    Dùng chung cho cả đường ``Gemini API`` (``core.reviewer``) và đường
    ``Gemini Web Session RPC`` (``core.gemini_web_engine``) để hai kênh
    kiểm duyệt luôn dùng một bộ quy tắc nghiệp vụ.

    Parameters
    ----------
    skill_id:
        ID của kỹ năng hoặc danh sách các ID kỹ năng cần dùng.
        Nếu ``None`` hoặc rỗng -> dùng kỹ năng mặc định đang chọn.
    skills_file:
        Đường dẫn file ``skills.json`` (mặc định: ``skills/skills.json``).

    Returns
    -------
    str
        Nội dung hướng dẫn gộp (có tiêu đề) hoặc chuỗi rỗng nếu không tìm thấy
        kỹ năng nào. KHÔNG bao giờ raise.
    """
    id_list: List[str] = []
    if isinstance(skill_id, str):
        if skill_id.strip():
            id_list = [skill_id.strip()]
    elif isinstance(skill_id, (list, tuple, set)):
        id_list = [str(sid).strip() for sid in skill_id if str(sid).strip()]

    if not id_list:
        try:
            default_rec = get_default_skill(skills_file=skills_file)
            if default_rec and default_rec.get("id"):
                id_list = [str(default_rec["id"])]
        except Exception:  # noqa: BLE001 - không để lỗi file kỹ năng làm hỏng prompt
            return ""

    prompts: List[str] = []
    seen: set = set()
    for sid in id_list:
        if sid in seen:
            continue
        seen.add(sid)
        try:
            record = get_skill(sid, skills_file=skills_file)
        except Exception:  # noqa: BLE001
            record = None
        if not record:
            continue
        name = str(record.get("name") or record.get("id") or "Kỹ năng báo chí").strip()
        instructions = str(record.get("instructions") or "").strip()
        if instructions:
            prompts.append(f"# KỸ NĂNG BÁO CHÍ ĐANG ÁP DỤNG: {name}\n{instructions}")

    return "\n\n".join(prompts)


def get_default_skill(skills_file: Optional[Path] = None) -> Dict[str, Any]:
    """Lay ky nang duoc dat lam mac dinh.

    Returns
    -------
    dict
        Ky nang mac dinh. Neu khong co ky nang nao duoc danh dau
        ``is_default`` thi lay ky nang dau tien; neu danh sach rong thi
        khoi tao lai ky nang goc.
    """
    path = _resolve_file(skills_file)
    skills = _read_skills(path)
    if not skills:  # duong dan du phong (da duoc xu ly trong _read_skills)
        skills = [_seed_record()]
        _write_skills(skills, path)

    for record in skills:
        if record.get("is_default", False):
            return record
    # Khong co ai duoc danh dau -> chot ky nang dau tien va sua lai trang thai.
    skills[0]["is_default"] = True
    _write_skills(skills, path)
    return skills[0]


def reset_to_default_skills(skills_file: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Khoi phuc lai toan bo ky nang goc chuan (xoa ky nang tu tao).

    Returns
    -------
    list
        Danh sach ky nang goc sau khi da ghi lai file.
    """
    path = _resolve_file(skills_file)
    skills = [_seed_record()]
    _write_skills(skills, path)
    return skills


# ---------------------------------------------------------------------------
# Kiem thu nhanh (chay truc tiep file nay: python core/skills_manager.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Cac ky nang hien tai:")
    for item in get_skills():
        print(f"  - {item['id']} (mac dinh={item['is_default']})")
