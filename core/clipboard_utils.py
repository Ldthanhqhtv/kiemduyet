"""
core/clipboard_utils.py
=======================

Sao chep Rich Text (HTML mau do) vao clipboard he dieu hanh (TASK_05).

Nghiep vu
---------
Yeu cau cua Ban bien tap: sau khi bôi do diem nghi ngo sai tren ban thao goc,
bien tap vien bam mot nut la copy duoc toan van bai bao (giu nguyen bo cuc)
de dan truc tiep vao Microsoft Word hoac CMS VOV2 — cac cum tu loi hien mau
do, dam, gach duoi.

Ky thuat
--------
Clipboard Windows ho tro nhieu dinh dang cung luc. De Word / CMS VOV2 tai
dung mau do khi paste, du lieu phai duoc ghi voi dinh dang **CF_HTML**
(HTML Format) — header ``Version/StartHTML/EndHTML/StartFragment/
EndFragment`` tinh theo byte offset (UTF-8), theo chuan cua Microsoft.
Dong thoi van ghi them ``CF_UNICODETEXT`` (van ban thuan) lam fallback cho
cac ung dung khong doc HTML.

Thuc thi bang ``ctypes`` (thu vien chuan cua Python, khong can cai them goi)
— nen chay tren may co giao dien Windows; neu khong mo duoc clipboard (may
khong co session) thi tra ve ``False`` de giao dien hien huong dan copy tay.

Luu y vi tri: ham nam o ``core/`` (khong nam trong ``app.py``) de unit test
co the ``mock.patch("core.clipboard_utils.write_clipboard_windows")`` — AppTest
cua Streamlit exec lai toan bo script ``app.py`` moi lan chay nen khong the
mock truc tiep mot ham dinh nghia trong ``app.py``.
"""

from __future__ import annotations

import os
from typing import Any, Dict

#: Dinh dang CF_HTML (HTML Format) cua Microsoft Word / Outlook / Edge.
CF_HTML_FORMAT_NAME = "HTML Format"

#: Dinh dang van ban Unicode (fallback cho cac ung dung khong doc HTML).
CF_UNICODETEXT_ID = 13

GMEM_MOVEABLE = 0x0002
GMEM_ZEROINIT = 0x0040


def build_clipboard_payload(highlight: Dict[str, Any]) -> Dict[str, str]:
    """Chuan bi du lieu clipboard tu ket qua bôi do bai goc.

    Parameters
    ----------
    highlight:
        Ket qua cua ``core.highlighter.highlight_original_article`` (co khoa
        ``rich_clipboard_html`` va ``plain_text``).

    Returns
    -------
    dict
        ``{"html": rich_clipboard_html, "text": plain_text}`` — hai dinh dang
        cung cap cho clipboard de Word / CMS VOV2 tai dung khi paste.
    """
    return {
        "html": str(highlight.get("rich_clipboard_html") or ""),
        "text": str(highlight.get("plain_text") or ""),
    }


def build_cf_html_fragment(html_payload: str) -> str:
    """Dong goi HTML thanh dinh dang CF_HTML (Microsoft) dung offset byte.

    CF_HTML yeu cau header ``Version/StartHTML/EndHTML/StartFragment/
    EndFragment`` tinh theo byte offset trong UTF-8 — Word va CMS VOV2 moi
    doc duoc phan noi dung dung (khong hien thi ca header ra van ban).

    Examples
    --------
    >>> fragment = build_cf_html_fragment("<b>chào</b>")
    >>> "<!--StartFragment-->" in fragment and "<b>chào</b>" in fragment
    True
    """
    header_template = (
        "Version:0.9\r\n"
        "StartHTML:{:010d}\r\n"
        "EndHTML:{:010d}\r\n"
        "StartFragment:{:010d}\r\n"
        "EndFragment:{:010d}\r\n"
    )
    prefix = "<html>\r\n<body>\r\n<!--StartFragment-->\r\n"
    suffix = "\r\n<!--EndFragment-->\r\n</body>\r\n</html>\r\n"

    # Tinh offset tren header co dinh truoc (4 so 10 chu so), roi ghi lai.
    placeholder = header_template.format(0, 0, 0, 0)
    start_html = len(placeholder.encode("utf-8"))
    start_fragment = start_html + len(prefix.encode("utf-8"))
    end_fragment = start_fragment + len(html_payload.encode("utf-8"))
    end_html = end_fragment + len(suffix.encode("utf-8"))

    header = header_template.format(start_html, end_html, start_fragment, end_fragment)
    return header + prefix + html_payload + suffix


def write_clipboard_windows(html_payload: str, text_payload: str) -> bool:
    """Ghi Rich Text (CF_HTML) + van ban thuan vao clipboard he dieu hanh.

    Su dung thu vien chuan ``ctypes`` (khong can cai them goi) dang ky dinh
    dang ``HTML Format`` — khi paste vao Microsoft Word hoac CMS VOV2, cac the
    ``<span style="color: red; font-weight: bold; text-decoration: underline;">``
    duoc ton trong va hien chu mau do, dam, gach duoi nguyen ven.

    Parameters
    ----------
    html_payload:
        Noi dung HTML Rich Text (xem ``core.highlighter.to_rich_clipboard_html``).
    text_payload:
        Van ban thuan (fallback cho cac ung dung khong doc HTML).

    Returns
    -------
    bool
        ``True`` neu ghi thanh cong; ``False`` neu khong phai Windows, khong
        mo duoc clipboard (may khong giao dien) hoac co loi khac — luc do
        giao dien hien huong dan copy tay.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # --- KHAI BAO KIEU DUONG (bat buoc tren Windows 64-bit) -------------
        # Mac dinh cua ``ctypes`` la ``c_int`` (32-bit) cho moi ham Win32 ->
        # ``HGLOBAL``/con tro 64-bit bi CAT NGAN -> ``memmove`` ghi vao dia
        # chi sai -> **Windows fatal exception: access violation** (lam server
        # Streamlit sup). Phai khai bao ro restype/argtypes cho tung ham.
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        user32.SetClipboardData.restype = wintypes.HANDLE
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.CloseClipboard.restype = wintypes.BOOL
        user32.EmptyClipboard.restype = wintypes.BOOL
        user32.RegisterClipboardFormatW.restype = wintypes.UINT
        user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]

        html_format = user32.RegisterClipboardFormatW(CF_HTML_FORMAT_NAME)
        if html_format == 0:
            return False

        if not user32.OpenClipboard(None):
            return False
        try:
            user32.EmptyClipboard()

            # --- CF_HTML (Rich Text mau do cho Word / CMS VOV2) ---
            cf_html = build_cf_html_fragment(html_payload).encode("utf-8")
            handle_html = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(cf_html) + 1)
            if handle_html:
                pointer = kernel32.GlobalLock(handle_html)
                ctypes.memmove(pointer, cf_html, len(cf_html))
                ctypes.memset(pointer + len(cf_html), 0, 1)
                kernel32.GlobalUnlock(handle_html)
                user32.SetClipboardData(wintypes.UINT(html_format), handle_html)

            # --- CF_UNICODETEXT (van ban thuan, fallback) ---
            wide = ctypes.create_unicode_buffer(text_payload)
            byte_size = len(wide) * ctypes.sizeof(ctypes.c_wchar)
            handle_text = kernel32.GlobalAlloc(
                GMEM_MOVEABLE | GMEM_ZEROINIT, byte_size
            )
            if handle_text:
                pointer = kernel32.GlobalLock(handle_text)
                ctypes.memmove(pointer, wide, byte_size)
                kernel32.GlobalUnlock(handle_text)
                user32.SetClipboardData(
                    wintypes.UINT(CF_UNICODETEXT_ID), handle_text
                )

            return True
        finally:
            user32.CloseClipboard()
    except Exception:
        return False


__all__ = [
    "build_clipboard_payload",
    "build_cf_html_fragment",
    "write_clipboard_windows",
    "CF_HTML_FORMAT_NAME",
    "CF_UNICODETEXT_ID",
]
