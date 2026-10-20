"""
Package `core` cua du an "Kiem Duyet Bai Bao AI".

Chua cac module loi (core business logic) dung chung cho ca ban Streamlit
tren Hugging Face Spaces lan ban Desktop .exe:

- core.reviewer       : AI Reviewer (Google Gemini + Google Search grounding).
- core.docx_exporter  : Xuat bao cao kiem duyet ra file Word (python-docx).
- core.skills_manager : Quan ly ky nang bao chi tuy chinh (custom skills).
"""

from .reviewer import (
    AIReviewer,
    DEFAULT_MODEL,
    ReviewResult,
    normalize_review_result,
    review_article,
)
from .docx_exporter import (
    build_report_document,
    coerce_review_result,
    export_review_to_bytes,
    export_review_to_docx,
    extract_article_title,
)
from .skills_manager import (
    DEFAULT_SKILL_SEED,
    SKILLS_FILE,
    build_default_skills,
    delete_skill,
    get_default_skill,
    get_default_skills,
    get_skill,
    get_skill_by_id,
    get_skills,
    reset_to_default_skills,
    save_skill,
    set_default_skill,
    slugify,
)

__all__ = [
    # reviewer
    "AIReviewer",
    "DEFAULT_MODEL",
    "ReviewResult",
    "normalize_review_result",
    "review_article",
    # docx_exporter
    "export_review_to_docx",
    "export_review_to_bytes",
    "build_report_document",
    "coerce_review_result",
    "extract_article_title",
    # skills_manager
    "SKILLS_FILE",
    "DEFAULT_SKILL_SEED",
    "slugify",
    "get_skills",
    "get_skill",
    "get_skill_by_id",
    "get_default_skill",
    "get_default_skills",
    "save_skill",
    "delete_skill",
    "set_default_skill",
    "build_default_skills",
    "reset_to_default_skills",
]
