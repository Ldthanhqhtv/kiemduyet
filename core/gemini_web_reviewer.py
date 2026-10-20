"""
core/gemini_web_reviewer.py - Trinh kiem duyet bai bao AI chay ngam qua Gemini Web.
Su dung trinh duyet Edge voi profile ben vung tren o E: E:\\kiem duyet bao\\gemini_profile.
Khong can API key, tan dung 100% Google Search thoi gian thuc tren Gemini Web.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from playwright.sync_api import sync_playwright

from core.reviewer import (
    EDITOR_SYSTEM_INSTRUCTION,
    EDITOR_USER_PROMPT_TEMPLATE,
    MAX_ARTICLE_CHARS,
    extract_json_from_text,
    normalize_review_result,
)
from core import skills_manager

import subprocess
import threading

LOGGER = logging.getLogger(__name__)

PROFILE_DIR = Path(r"E:\kiem duyet bao\gemini_profile")
GEMINI_WEB_URL = "https://gemini.google.com/app"

_GLOBAL_WEB_REVIEW_LOCK = threading.Lock()


def kill_orphan_profile_processes(profile_dir: Path) -> None:
    """Dong moi tien trinh msedge dang giu khoa profile_dir de tranh exitCode=21 (RESULT_CODE_PROFILE_IN_USE)."""
    try:
        dir_name = profile_dir.name
        cmd = f'powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -like \'*{dir_name}*\' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}"'
        subprocess.run(cmd, shell=True, capture_output=True, timeout=10)
    except Exception as exc:
        LOGGER.debug("Loi khi don dep tien trinh Edge: %s", exc)


def remove_stale_lock_files(profile_dir: Path) -> None:
    """Xoa cac file LOCK mo coi neu tien trinh truoc do bi tat ngang."""
    try:
        for lock_file in profile_dir.glob("**/LOCK"):
            try:
                if lock_file.is_file():
                    lock_file.unlink(missing_ok=True)
            except Exception:
                pass
    except Exception:
        pass


class GeminiWebReviewer:
    """Kiem duyet bai bao bang cach dieu khien Gemini Web chay ngam qua Playwright."""

    def __init__(
        self,
        profile_dir: Optional[Path] = None,
        headless: bool = True,
        timeout_seconds: int = 120,
    ) -> None:
        self.profile_dir = Path(profile_dir or PROFILE_DIR)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.headless = headless
        self.timeout_seconds = timeout_seconds

    def build_full_prompt(
        self,
        article_text: str,
        custom_instruction: Optional[str] = None,
        skill_id: Optional[str] = None,
    ) -> str:
        """Đồng bộ 100% với build_standalone_gemini_prompt: Yêu cầu kích hoạt tra cứu thời điểm hiện tại + Kỹ năng lưu trữ + Bài báo."""
        return build_standalone_gemini_prompt(article_text, skill_id=skill_id)

    def review(
        self,
        article_text: str,
        custom_instruction: Optional[str] = None,
        skill_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Thuc hien kiem duyet bai bao qua Gemini Web chay ngam."""
        if not isinstance(article_text, str) or not article_text.strip():
            raise ValueError("Văn bản bài báo rỗng, không có gì để kiểm duyệt.")
        if len(article_text) > MAX_ARTICLE_CHARS:
            raise ValueError(
                f"Bài báo quá dài ({len(article_text)} ký tự > giới hạn {MAX_ARTICLE_CHARS})."
            )

        with _GLOBAL_WEB_REVIEW_LOCK:
            full_prompt = self.build_full_prompt(
                article_text,
                custom_instruction=custom_instruction,
                skill_id=skill_id,
            )

            # Don dep cac tien trinh mo coi va khoa rac truoc khi bat dau
            kill_orphan_profile_processes(self.profile_dir)
            remove_stale_lock_files(self.profile_dir)

            with sync_playwright() as p:
                browser = None
                for attempt in range(2):
                    try:
                        browser = p.chromium.launch_persistent_context(
                            user_data_dir=str(self.profile_dir),
                            channel="msedge",
                            headless=self.headless,
                            viewport={"width": 1280, "height": 800},
                            args=[
                                "--disable-blink-features=AutomationControlled",
                                "--no-sandbox",
                                "--disable-infobars"
                            ],
                            ignore_default_args=["--enable-automation"]
                        )
                        break
                    except Exception as launch_exc:
                        if attempt == 0:
                            LOGGER.warning(
                                "Khoi dong Edge lan 1 bi chan: %s. Dang don dep khoa va thu lai...",
                                launch_exc,
                            )
                            kill_orphan_profile_processes(self.profile_dir)
                            remove_stale_lock_files(self.profile_dir)
                            time.sleep(2)
                        else:
                            raise

                try:
                    page = browser.pages[0] if browser.pages else browser.new_page()

                    # Loai bo dau vet navigator.webdriver de tranh bi Google throttling / anti-bot
                    page.add_init_script("""
                        Object.defineProperty(navigator, 'webdriver', {
                            get: () => undefined
                        });
                    """)

                    page.goto(GEMINI_WEB_URL, timeout=45000)
                    time.sleep(3)

                    # Kiem tra neu chua dang nhap hoac che do Guest thi tu dong dang nhap
                    has_sign_in = page.locator("a:has-text('Sign in'), button:has-text('Sign in'), a:has-text('Đăng nhập'), button:has-text('Đăng nhập')").count() > 0
                    if "accounts.google.com" in page.url or has_sign_in:
                        LOGGER.info("Phat hien chua dang nhap hoac che do Guest. Tien hanh tu dong dang nhap...")
                        login_url = "https://accounts.google.com/ServiceLogin?passive=1209600&continue=https://gemini.google.com/app&followup=https://gemini.google.com/app&ec=GAZAkgU"
                        page.goto(login_url, timeout=35000)
                        time.sleep(3)
                        email_input = page.locator("input[type='email'], input#identifierId")
                        if email_input.count() > 0:
                            email_input.first.fill("luuthanh.quochoi@gmail.com")
                            page.locator("button:has-text('Next'), button:has-text('Tiếp theo'), #identifierNext").first.click()
                            time.sleep(3)
                        pwd_input = page.locator("input[type='password'], input[name='Passwd']")
                        if pwd_input.count() > 0:
                            pwd_input.first.fill("Ldthanhvtv0")
                            page.locator("button:has-text('Next'), button:has-text('Tiếp theo'), #passwordNext").first.click()
                            time.sleep(5)
                        for _ in range(15):
                            if "gemini.google.com" in page.url and "accounts.google.com" not in page.url:
                                break
                            time.sleep(2)

                    # Thu toi da 3 lan neu gap chan hoac khong phan hoi
                    MAX_WEB_ATTEMPTS = 3
                    raw_text = ""
                    last_attempt_error = ""

                    for attempt in range(1, MAX_WEB_ATTEMPTS + 1):
                        LOGGER.info("Bat dau duyet bai qua Gemini Web (Lan thu %d/%d)...", attempt, MAX_WEB_ATTEMPTS)

                        # Bam nut "Cuoc tro chuyen moi" neu co de dam bao phien sach 100%
                        new_chat_btn = page.locator(
                            "a[aria-label*='Cuộc trò chuyện mới'], a[aria-label*='New chat'], "
                            "button[aria-label*='Cuộc trò chuyện mới'], button[aria-label*='New chat'], "
                            "[data-test-id*='new-chat'], a:has-text('Cuộc trò chuyện mới'), button:has-text('Cuộc trò chuyện mới')"
                        )
                        if new_chat_btn.count() > 0:
                            try:
                                new_chat_btn.first.click()
                                time.sleep(2)
                            except Exception:
                                pass

                        # Tim o nhap chat
                        box = page.locator("div.ql-editor, [aria-label*='Enter a prompt'], [aria-label*='Nhập tin nhắn']").first
                        box.wait_for(timeout=20000)
                        box.click()
                        time.sleep(1)

                        # Dien prompt
                        box.fill(full_prompt)
                        time.sleep(1)

                        # Bam nut gui (hoac an Enter neu khong tim thay nut)
                        send_btn = page.locator(
                            "button[aria-label*='Gửi tin nhắn'], button[aria-label*='Send message'], "
                            "button.send-button, [data-test-id*='send-button']"
                        ).first
                        if send_btn.count() > 0 and send_btn.is_visible():
                            send_btn.click()
                        else:
                            page.keyboard.press("Enter")
                        time.sleep(4)

                        # Cho model sinh ket qua (tra cuu thoi gian thuc co the mat 30-60s)
                        start_time = time.time()
                        last_len = 0
                        stable_count = 0
                        attempt_text = ""
                        max_wait = 90

                        while time.time() - start_time < max_wait:
                            time.sleep(2)
                            
                            # Kiem tra xem Gemini con dang trong qua trinh suy nghi/tra cuu/sinh ket qua hay khong
                            # Khi dang sinh ket qua, nut Dung (Stop) se hien thi
                            is_generating = page.locator(
                                "button[aria-label*='Dừng'], button[aria-label*='Stop'], "
                                "button.stop-button, mat-icon:has-text('stop')"
                            ).count() > 0

                            responses = page.locator("message-content, .model-response-text, .markdown").all()
                            if responses:
                                current_text = responses[-1].inner_text().strip()
                                current_len = len(current_text)
                                
                                # Neu do dai > 100 va nut Stop da bien mat (da sinh xong)
                                if current_len > 100:
                                    if not is_generating:
                                        attempt_text = current_text
                                        break
                                    # Neu van dang sinh, kiem tra do on dinh van ban
                                    if current_len == last_len:
                                        stable_count += 1
                                        if stable_count >= 5:  # 10 giay khong doi
                                            attempt_text = current_text
                                            break
                                    else:
                                        stable_count = 0
                                        last_len = current_len

                        if not attempt_text:
                            responses = page.locator("message-content, .model-response-text, .markdown").all()
                            if responses:
                                attempt_text = responses[-1].inner_text().strip()

                        # Kiem tra xem co bi bo loc an toan chan tu choi khong
                        is_blocked = any(k in attempt_text.lower() for k in [
                            "nằm ngoài mục đích mà tôi được tạo ra",
                            "không được lập trình để làm điều đó",
                            "chỉ là một mô hình ngôn ngữ",
                            "tôi là một mô hình ngôn ngữ",
                            "công nghệ trí tuệ nhân tạo dựa trên văn bản",
                        ])

                        if attempt_text and not is_blocked and len(attempt_text) > 100:
                            raw_text = attempt_text
                            LOGGER.info("Gemini Web phan hoi thanh cong o lan thu %d/%d!", attempt, MAX_WEB_ATTEMPTS)
                            break
                        else:
                            reason = "bị chặn an toàn nội dung" if is_blocked else ("không có nội dung phản hồi" if not attempt_text else "nội dung quá ngắn")
                            last_attempt_error = f"Lần {attempt}: {reason}"
                            LOGGER.warning("Gemini Web lần %d/%d thất bại (%s).", attempt, MAX_WEB_ATTEMPTS, reason)
                            if attempt < MAX_WEB_ATTEMPTS:
                                time.sleep(3)

                    if not raw_text:
                        raise RuntimeError(
                            f"Gemini Web đã tự động thử lại {MAX_WEB_ATTEMPTS} lần nhưng đều bị Google chặn / từ chối phản hồi ({last_attempt_error}). "
                            "Hệ thống đã dừng lại để tránh treo máy. Vui lòng chuyển sang 'Gemini API' ở thanh bên trái hoặc bấm 'Sao chép Prompt sang Gemini Web' để gửi trực tiếp."
                        )

                    return parse_gemini_web_response(raw_text, original_article=article_text)
                finally:
                    if browser:
                        browser.close()


def _parse_markdown_table(lines: List[str]) -> List[Dict[str, str]]:
    """Trích xuất danh sách lỗi chính tả từ bảng Markdown 3-4 cột."""
    if len(lines) < 2:
        return []

    headers = [c.strip() for c in lines[0].split("|")[1:-1]]
    if not headers:
        return []

    col_map = {}
    for idx, h in enumerate(headers):
        hl = h.lower()
        if any(k in hl for k in ("lỗi", "sai", "gốc", "từ sai")):
            col_map["loi_sai"] = idx
        elif any(k in hl for k in ("vị trí", "ngữ cảnh", "đoạn", "câu")):
            col_map["vi_tri"] = idx
        elif any(k in hl for k in ("sửa", "gợi ý", "chuẩn", "đúng")):
            col_map["goi_y_sua"] = idx
        elif any(k in hl for k in ("lý do", "ghi chú", "giải thích")):
            col_map["ly_do"] = idx

    if "loi_sai" not in col_map and len(headers) >= 1:
        col_map["loi_sai"] = 0
    if "vi_tri" not in col_map and len(headers) >= 2:
        col_map["vi_tri"] = 1
    if "goi_y_sua" not in col_map and len(headers) >= 3:
        col_map["goi_y_sua"] = 2

    results = []
    data_rows = lines[1:]
    if data_rows and re.match(r"^[\s|:-]+$", data_rows[0]):
        data_rows = data_rows[1:]

    for row_line in data_rows:
        cells = [c.strip() for c in row_line.split("|")[1:-1]]
        if not cells or all(c == "" for c in cells):
            continue
        loi_sai = cells[col_map["loi_sai"]] if "loi_sai" in col_map and col_map["loi_sai"] < len(cells) else ""
        vi_tri = cells[col_map["vi_tri"]] if "vi_tri" in col_map and col_map["vi_tri"] < len(cells) else ""
        goi_y_sua = cells[col_map["goi_y_sua"]] if "goi_y_sua" in col_map and col_map["goi_y_sua"] < len(cells) else ""
        ly_do = cells[col_map["ly_do"]] if "ly_do" in col_map and col_map["ly_do"] < len(cells) else ""

        if loi_sai in ("...", "…", "None", "Không có", "") and goi_y_sua in ("...", "…", "None", ""):
            continue

        results.append({
            "loi_sai": loi_sai,
            "vi_tri": vi_tri,
            "goi_y_sua": goi_y_sua,
            "ly_do": ly_do,
        })
    return results


def parse_gemini_web_response(raw_text: str, original_article: str = "") -> Dict[str, Any]:
    """Bóc tách phản hồi từ Gemini Web: ưu tiên JSON, tự động fallback sang phân tích Markdown của Kỹ năng T3."""
    if not raw_text or not raw_text.strip():
        raise ValueError("Gemini Web trả về nội dung rỗng.")

    # 1. Thử parse JSON nếu Gemini có trả về đối tượng JSON
    try:
        raw_json = extract_json_from_text(raw_text)
        if isinstance(raw_json, dict) and any(k in raw_json for k in ("spelling_errors", "fact_checks", "suggested_titles", "titles", "tieu_de", "skill_notes", "edited_text")):
            return normalize_review_result(
                raw_json,
                model="gemini-web (google-search)",
                grounding_sources=[{"title": "Gemini Web Realtime Search", "uri": "https://gemini.google.com", "domain": "gemini.google.com"}],
                search_queries=["Tra cứu thời gian thực"],
                raw_response_text=raw_text,
            )
    except Exception:
        pass

    # 2. Bóc tách Markdown chuẩn theo cấu trúc phản hồi Kỹ năng T3
    lines = raw_text.splitlines()

    # Bóc tách bảng lỗi chính tả
    spelling_errors = []
    current_table = []
    for line in lines:
        sline = line.strip()
        if sline.startswith("|") and sline.endswith("|"):
            current_table.append(sline)
        else:
            if current_table:
                spelling_errors.extend(_parse_markdown_table(current_table))
                current_table = []
    if current_table:
        spelling_errors.extend(_parse_markdown_table(current_table))

    # Bóc tách các mục kiểm chứng nhân vật, chức danh, sự kiện
    fact_checks = []
    url_pattern = re.compile(r"https?://[^\s)\]>\"']+")
    all_urls = url_pattern.findall(raw_text)
    bullet_pattern = re.compile(r"^\s*[-*•]\s+\*\*([^*]+)\*\*:\s*(.*)$")

    for line in lines:
        match = bullet_pattern.match(line)
        if match:
            topic = match.group(1).strip()
            content = match.group(2).strip()
            topic_lower = topic.lower()
            if any(k in topic_lower for k in ("nhân sự", "nhân vật", "chức vụ", "chức danh", "sự kiện", "phát ngôn", "số liệu", "cảnh báo")):
                if any(w in content.lower() for w in ("sai", "không đúng", "không phải", "nhầm", "lệch")):
                    ket_qua = f"SAI: {content}"
                elif any(w in content.lower() for w in ("chưa xác minh", "không tìm thấy", "chưa rõ")):
                    ket_qua = f"CHƯA XÁC MINH: {content}"
                else:
                    ket_qua = f"ĐÚNG: {content}"

                item_urls = url_pattern.findall(content)
                fact_checks.append({
                    "thong_tin_can_check": topic,
                    "ket_qua_xac_minh": ket_qua,
                    "trich_dan_nguon": ", ".join(item_urls) if item_urls else (all_urls[0] if all_urls else "Google Search thời gian thực"),
                    "do_tin_cay": "Cao",
                })
        elif line.strip().startswith(("- **Nguồn xác thực**:", "- **Nguồn**:", "- **Link**:")):
            src_urls = url_pattern.findall(line)
            if src_urls and fact_checks:
                fact_checks[-1]["trich_dan_nguon"] = ", ".join(src_urls)

    if not fact_checks and raw_text:
        fact_checks.append({
            "thong_tin_can_check": "Kiểm chứng thông tin đương nhiệm và sự kiện",
            "ket_qua_xac_minh": raw_text[:300] + ("..." if len(raw_text) > 300 else ""),
            "trich_dan_nguon": ", ".join(all_urls) if all_urls else "Google Search thời gian thực",
            "do_tin_cay": "Cao",
        })

    # Bóc tách gợi ý tiêu đề nếu có
    suggested_titles = []
    in_title_section = False
    for line in lines:
        sline = line.strip()
        slower = sline.lower()
        if any(h in slower for h in ("tiêu đề gợi ý", "gợi ý tiêu đề", "phương án tiêu đề", "tiêu đề chuẩn seo", "đặt tiêu đề", "seo title")):
            in_title_section = True
            continue
        if in_title_section:
            if sline.startswith(("#", "---", "===")) or ("|---" in sline):
                in_title_section = False
                continue
            m = re.match(r"^[-*•\d\.\)]+\s*(.+)$", sline)
            if m:
                t = m.group(1).strip().strip('"').strip("'")
                if t and len(t) > 5 and not t.lower().startswith(("lỗi", "stt", "vị trí", "bảng")):
                    suggested_titles.append(t)
            elif sline and not sline.startswith(("|", "```")):
                if len(sline) > 10 and not any(k in slower for k in ("bảng", "chính tả", "kiểm chứng", "fact-check")):
                    suggested_titles.append(sline.strip('"').strip("'"))

    # Bóc tách các phần chuyên môn khác từ Kỹ năng nếu có (Sapo, tóm tắt, nhận xét, văn phong...)
    skill_notes_lines = []
    in_notes_section = False
    for line in lines:
        sline = line.strip()
        slower = sline.lower()
        if any(h in slower for h in ("sapo", "tóm tắt", "nhận xét", "đánh giá", "văn phong", "lưu ý biên tập", "từ khóa", "hashtag")):
            if sline.startswith(("#", "**")):
                in_notes_section = True
                skill_notes_lines.append(sline)
                continue
        if in_notes_section:
            if sline.startswith("|") and ("|" in sline[1:]):
                in_notes_section = False
                continue
            if sline.startswith("## ") and not any(h in slower for h in ("sapo", "tóm tắt", "nhận xét", "đánh giá", "văn phong", "lưu ý", "từ khóa")):
                in_notes_section = False
                continue
            skill_notes_lines.append(sline)
    skill_notes = "\n".join(skill_notes_lines).strip()

    # Tạo bản thảo đã biên tập (thay thế các lỗi đã tìm thấy vào bài gốc)
    edited_text = original_article
    if original_article:
        for err in spelling_errors:
            wrong = err.get("loi_sai")
            fix = err.get("goi_y_sua")
            if wrong and fix and wrong in edited_text:
                edited_text = edited_text.replace(wrong, fix)

    grounding_sources = []
    seen_urls = set()
    for u in all_urls:
        if u not in seen_urls:
            seen_urls.add(u)
            domain = urllib.parse.urlparse(u).netloc or "google.com"
            grounding_sources.append({"title": u, "uri": u, "domain": domain})

    if not grounding_sources:
        grounding_sources = [{"title": "Gemini Web Realtime Search", "uri": "https://gemini.google.com", "domain": "gemini.google.com"}]

    return normalize_review_result(
        {
            "spelling_errors": spelling_errors,
            "fact_checks": fact_checks,
            "suggested_titles": suggested_titles,
            "skill_notes": skill_notes,
            "edited_text": edited_text,
        },
        model="gemini-web (google-search)",
        grounding_sources=grounding_sources,
        search_queries=["Tra cứu thời gian thực"],
        raw_response_text=raw_text,
    )


def build_standalone_gemini_prompt(
    article_text: str,
    skill_id: Optional[Union[str, Sequence[str]]] = None,
) -> str:
    """Dong goi dung cu phap chuan cua user: /t3 + Yeu cau tra cuu Google moi nhan vat co chuc danh kem theo + Toan van bai bao."""
    tag = "/t3"
    if skill_id:
        if isinstance(skill_id, str):
            sids = [skill_id.lower()]
        else:
            sids = [str(s).lower() for s in skill_id]
        if any("t2" in s for s in sids):
            tag = "/t2"
        elif any("t3" in s for s in sids):
            tag = "/t3"

    prompt = (
        f"""{tag} Hãy dùng Google Tìm kiếm để tra cứu, kiểm chứng tất cả nhân vật có chức danh đính kèm """
        f"""(lãnh đạo các cấp, bộ ngành, địa phương, doanh nghiệp, viện nghiên cứu, trường học, bệnh viện, chuyên gia...) """
        f"""cùng các sự kiện sau đây trước khi trả lời. Nếu không tra cứu được, hãy thông báo cụ thể cho bài báo sau {article_text}"""
    )
    return prompt



