"""
core/rate_limiter.py
====================

Bo dem REQUEST/PHUT (RPM - sliding window 60 giay) va REQUEST/NGAY (RPD) cho
cac model Gemini, phuc vu TASK_06 (RPM Limiter & Model Fallback).

Bo dem nay giup he thong:
- Theo doi han muc Free tier hien tai (xem truoc khi goi API):
    * ``gemini-3.1-flash-lite`` : 15 RPM / 500 RPD
    * ``gemini-3.5-flash-lite`` : 15 RPM / 500 RPD
    * ``gemini-3.8-flash``      :  5 RPM /  20 RPD (han muc rat hep!)
- Tu dong goi y chuyen model khi model dang dung cham den han muc
  (``should_switch_model``) truoc khi tien hanh goi API — tranh 429 cua Google.
- Cung cap trang thai hien thi tren Sidebar cua Streamlit (``get_status``).

Thiet ke:
- RPM dung **sliding-window 60 giay** (dem tung thoi diem goi, bo nhung cai
  qua 60 giay).
- RPD dem theo **ngay hien tai** (moi ngay moi dem tu 0; dong hon 23:59:59
  sang ngay moi se reset).
- Thread-safe (Streamlit co the chay nhieu request dong thoi) bang
  ``threading.Lock``.
- Dong ho co the tiem vao (``clock``) de test khong can do thoi gian that.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, Optional, Tuple

#: Cua so RPM: 60 giay cuoi cung.
RPM_WINDOW_SECONDS: float = 60.0


@dataclass(frozen=True)
class ModelQuota:
    """Han muc cua mot model Gemini: requests/phut (RPM) va requests/ngay (RPD)."""

    rpm: int
    rpd: int


#: Han muc Free tier theo tung model (nguon: TencentDB [model_rate_limits]).
MODEL_LIMITS: Dict[str, ModelQuota] = {
    "gemini-3.1-flash-lite": ModelQuota(rpm=15, rpd=500),
    "gemini-3.5-flash-lite": ModelQuota(rpm=15, rpd=500),
    "gemini-3.8-flash": ModelQuota(rpm=5, rpd=20),
}

#: Thu tu uu tien chuyen fallback: model truoc nhe hon (500 RPD), model sau
#: nghiem ngat hon (3.8-flash chi 20 RPD) chi dung khi khong con lua chon.
FALLBACK_MODEL_ORDER: Tuple[str, ...] = (
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash",
)

#: Han mac dinh cho model khong co trong bang (du bao de an toan, khong bi 429).
DEFAULT_QUOTA: ModelQuota = ModelQuota(rpm=15, rpd=500)


class RateLimiter:
    """Bo dem RPM/RPD sliding-window, thread-safe, co the test duoc.

    Parameters
    ----------
    clock:
        Ham tra ve ``time.time()`` (giay, float). Mac dinh dong ho he thong;
        test co the truyen fake clock de dieu khien thoi gian.

    Vi du
    -----
    >>> limiter = RateLimiter(clock=lambda: 0.0)          # doctest: +SKIP
    >>> limiter.record_call("gemini-3.8-flash")           # doctest: +SKIP
    >>> status = limiter.get_status("gemini-3.8-flash")   # doctest: +SKIP
    >>> status["rpm_used"]                                # doctest: +SKIP
    1
    """

    def __init__(self, clock: Optional[Callable[[], float]] = None) -> None:
        self._clock: Callable[[], float] = clock or time.time
        self._lock = threading.Lock()
        #: Moi model co deque cac thoi diem goi (giay) dang trong cua so 60s.
        self._calls: Dict[str, Deque[float]] = {}
        #: Bo dem RPD: model -> (ngay_hien_tai ISO, so_luong).
        self._day_counters: Dict[str, Tuple[str, int]] = {}

    # -- Tien ich noi bo ----------------------------------------------------

    @staticmethod
    def _day_key(timestamp: float) -> str:
        """Chuyen thoi diem (giay) thanh khoa ngay ISO (yyyy-mm-dd) local."""
        return time.strftime("%Y-%m-%d", time.localtime(timestamp))

    @staticmethod
    def limits_for(model: str) -> ModelQuota:
        """Lay han muc cua model; model khong ro duong dung han mac dinh."""
        return MODEL_LIMITS.get(model, DEFAULT_QUOTA)

    def _prune_locked(self, model: str, now: float) -> None:
        """Bo cac thoi diem goi da qua 60 giay (goi khi dang giua lock)."""
        stamps = self._calls.get(model)
        if not stamps:
            return
        cutoff = now - RPM_WINDOW_SECONDS
        while stamps and stamps[0] <= cutoff:
            stamps.popleft()

    # -- API cong khai ------------------------------------------------------

    def record_call(self, model: str) -> None:
        """Ghi nhan MOT loi goi API vua duoc thuc hien bang ``model``.

        Nen goi TRUOC moi loi generate_content de dem vao han muc (ke ca loi
        dang bi 429 — van phai dem, tranh viec lap tuc goi lai nghien ngat quota).
        """
        now = float(self._clock())
        with self._lock:
            stamps = self._calls.setdefault(model, deque())
            stamps.append(now)

            day = self._day_key(now)
            current_day, current_count = self._day_counters.get(model, (day, 0))
            if current_day != day:
                current_count = 0
            self._day_counters[model] = (day, current_count + 1)

    def get_status(self, model: str) -> Dict[str, int | bool | str]:
        """Tra ve trang thai han muc hien tai cua ``model``.

        Returns
        -------
        dict
            Gom ``model``, ``rpm_used``/``rpm_limit``, ``rpd_used``/``rpd_limit``,
            ``rpm_remaining``/``rpd_remaining``, ``can_call`` (True neu van con
            han muc) va ``exceeded`` (da cham 1 trong 2 han muc).
        """
        now = float(self._clock())
        limits = self.limits_for(model)

        with self._lock:
            self._prune_locked(model, now)
            rpm_used = len(self._calls.get(model, ()))

            day = self._day_key(now)
            current_day, rpd_used = self._day_counters.get(model, (day, 0))
            if current_day != day:
                rpd_used = 0  # ngay moi -> dem lai tu 0

        rpm_remaining = max(0, limits.rpm - rpm_used)
        rpd_remaining = max(0, limits.rpd - rpd_used)
        can_call = rpm_used < limits.rpm and rpd_used < limits.rpd

        return {
            "model": model,
            "rpm_used": rpm_used,
            "rpm_limit": limits.rpm,
            "rpd_used": rpd_used,
            "rpd_limit": limits.rpd,
            "rpm_remaining": rpm_remaining,
            "rpd_remaining": rpd_remaining,
            "can_call": can_call,
            "exceeded": not can_call,
        }

    def should_switch_model(self, current_model: str) -> Tuple[bool, str]:
        """Kiem tra xem co nen chuyen sang model khac truoc khi goi API khong.

        Returns
        -------
        tuple[bool, str]
            ``(can_switch, next_model)``:
            - ``(False, current_model)``: model hien tai van con han muc.
            - ``(True, khac)``: da cham han muc model hien tai; model moi la
              model dau tien trong ``FALLBACK_MODEL_ORDER`` CON han muc.
            - ``(True, current_model)``: khong con model nao du han muc —
              giu nguyen model (API co the tra 429, nhung it nhat khong chuyen
              sang model cung het quota).
        """
        if self.get_status(current_model)["can_call"]:
            return False, current_model

        for candidate in FALLBACK_MODEL_ORDER:
            if candidate == current_model:
                continue
            if self.get_status(candidate)["can_call"]:
                return True, candidate

        # Tat ca model deu cham han muc -> khong co noi de chuyen.
        return True, current_model

    def reset(self, model: Optional[str] = None) -> None:
        """Xoa toan bo (hoac cua mot model) — dung cho test / reset phien."""
        with self._lock:
            if model is None:
                self._calls.clear()
                self._day_counters.clear()
            else:
                self._calls.pop(model, None)
                self._day_counters.pop(model, None)


#: Singleton dung chung cho ca ung dung (app.py + core.reviewer).
_LIMITER_LOCK = threading.Lock()
_RATE_LIMITER: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """Lay bo dem RPM/RPD singleton cua he thong (tao lan dau neu chua co)."""
    global _RATE_LIMITER
    if _RATE_LIMITER is None:
        with _LIMITER_LOCK:
            if _RATE_LIMITER is None:
                _RATE_LIMITER = RateLimiter()
    return _RATE_LIMITER
