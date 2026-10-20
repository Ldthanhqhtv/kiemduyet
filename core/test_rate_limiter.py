"""
core/test_rate_limiter.py
=========================

Bo test cho ``core.rate_limiter`` (TASK_06): sliding-window RPM 60s, dem RPD
theo ngay, trang thai han muc va goi y chuyen model fallback.

Chay:  python -m unittest core.test_rate_limiter -v
"""

from __future__ import annotations

import threading
import time
import unittest

from core.rate_limiter import (
    DEFAULT_QUOTA,
    FALLBACK_MODEL_ORDER,
    MODEL_LIMITS,
    RateLimiter,
    get_rate_limiter,
)


class FakeClock:
    """Dong ho gia lap: cho phep test dieu khien thoi gian tuy y."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


def _make_limiter(start: float = 1_000_000.0) -> tuple[RateLimiter, FakeClock]:
    clock = FakeClock(start)
    return RateLimiter(clock=clock), clock


class ModelQuotaDefinitionTests(unittest.TestCase):
    """Bang han muc theo dung spec TencentDB [model_rate_limits]."""

    def test_limits_match_spec(self) -> None:
        self.assertEqual(MODEL_LIMITS["gemini-3.1-flash-lite"].rpm, 15)
        self.assertEqual(MODEL_LIMITS["gemini-3.1-flash-lite"].rpd, 500)
        self.assertEqual(MODEL_LIMITS["gemini-3.5-flash-lite"].rpm, 15)
        self.assertEqual(MODEL_LIMITS["gemini-3.5-flash-lite"].rpd, 500)
        self.assertEqual(MODEL_LIMITS["gemini-3.8-flash"].rpm, 5)
        self.assertEqual(MODEL_LIMITS["gemini-3.8-flash"].rpd, 20)

    def test_unknown_model_uses_safe_default(self) -> None:
        limiter, _ = _make_limiter()
        self.assertEqual(limiter.limits_for("model-khong-ro"), DEFAULT_QUOTA)
        status = limiter.get_status("model-khong-ro")
        self.assertEqual(status["rpm_limit"], 15)
        self.assertEqual(status["rpd_limit"], 500)

    def test_fallback_order_starts_with_lite_models(self) -> None:
        # Thu tu fallback: 3.1-lite truoc (500 RPD), 3.8-flash cuoi (20 RPD).
        self.assertEqual(FALLBACK_MODEL_ORDER[0], "gemini-3.1-flash-lite")
        self.assertEqual(FALLBACK_MODEL_ORDER[1], "gemini-3.5-flash-lite")
        self.assertEqual(FALLBACK_MODEL_ORDER[-1], "gemini-3.8-flash")


class RecordAndStatusTests(unittest.TestCase):
    """record_call + get_status tren cua so 60 giay va trong ngay."""

    def test_fresh_limiter_is_clean(self) -> None:
        limiter, _ = _make_limiter()
        status = limiter.get_status("gemini-3.1-flash-lite")
        self.assertEqual(status["rpm_used"], 0)
        self.assertEqual(status["rpd_used"], 0)
        self.assertTrue(status["can_call"])
        self.assertFalse(status["exceeded"])

    def test_record_call_counts_rpm_and_rpd(self) -> None:
        limiter, _ = _make_limiter()
        for _ in range(3):
            limiter.record_call("gemini-3.1-flash-lite")

        status = limiter.get_status("gemini-3.1-flash-lite")
        self.assertEqual(status["rpm_used"], 3)
        self.assertEqual(status["rpd_used"], 3)
        self.assertEqual(status["rpm_remaining"], 12)
        self.assertEqual(status["rpd_remaining"], 497)

    def test_calls_are_tracked_per_model(self) -> None:
        limiter, _ = _make_limiter()
        limiter.record_call("gemini-3.1-flash-lite")
        limiter.record_call("gemini-3.8-flash")

        self.assertEqual(limiter.get_status("gemini-3.1-flash-lite")["rpm_used"], 1)
        self.assertEqual(limiter.get_status("gemini-3.8-flash")["rpm_used"], 1)
        self.assertEqual(limiter.get_status("gemini-3.5-flash-lite")["rpm_used"], 0)

    def test_rpm_sliding_window_expires_after_60s(self) -> None:
        limiter, clock = _make_limiter()
        for _ in range(5):
            limiter.record_call("gemini-3.8-flash")

        # Len 5/5 RPM cua 3.8-flash -> het han muc phut.
        self.assertFalse(limiter.get_status("gemini-3.8-flash")["can_call"])

        clock.advance(61.0)
        status = limiter.get_status("gemini-3.8-flash")
        self.assertEqual(status["rpm_used"], 0)  # cua so 60s da qua het
        self.assertTrue(status["can_call"])

    def test_rpm_window_keeps_recent_calls_only(self) -> None:
        limiter, clock = _make_limiter()
        limiter.record_call("gemini-3.1-flash-lite")  # goi #1 tai t=0
        clock.advance(30.0)
        limiter.record_call("gemini-3.1-flash-lite")  # goi #2 tai t=30
        clock.advance(31.0)  # t=61: goi #1 het han, goi #2 van con (duoi 60s)

        status = limiter.get_status("gemini-3.1-flash-lite")
        self.assertEqual(status["rpm_used"], 1)

    def test_rpd_increments_even_when_rpm_window_slides(self) -> None:
        # RPD khong bi anh huong boi cua so 60s: 1 ngay = 1 dem nguyen ngay.
        limiter, clock = _make_limiter()
        for _ in range(5):
            limiter.record_call("gemini-3.8-flash")
        clock.advance(3600.0)  # 1 gio sau: RPM ve 0 nhung RPD van 5

        status = limiter.get_status("gemini-3.8-flash")
        self.assertEqual(status["rpm_used"], 0)
        self.assertEqual(status["rpd_used"], 5)

    def test_rpd_resets_on_next_day(self) -> None:
        # Bat dau luc 23:59:00 (gio dia phuong), sau 120 giay la ngay moi -> RPD reset.
        import time as time_module

        now_struct = time_module.localtime()
        near_midnight = time_module.mktime(
            (now_struct.tm_year, now_struct.tm_mon, now_struct.tm_mday, 23, 59, 0, 0, 0, -1)
        )
        limiter, clock = _make_limiter(start=near_midnight)
        for _ in range(20):
            limiter.record_call("gemini-3.8-flash")
        self.assertFalse(limiter.get_status("gemini-3.8-flash")["can_call"])

        clock.advance(120.0)  # qua 00:01 ngay tiep theo
        status = limiter.get_status("gemini-3.8-flash")
        self.assertEqual(status["rpd_used"], 0)
        self.assertTrue(status["can_call"])

    def test_rpd_exceeded_blocks_calling(self) -> None:
        limiter, _ = _make_limiter()
        for _ in range(500):
            limiter.record_call("gemini-3.1-flash-lite")

        status = limiter.get_status("gemini-3.1-flash-lite")
        self.assertEqual(status["rpd_used"], 500)
        self.assertEqual(status["rpd_remaining"], 0)
        self.assertFalse(status["can_call"])
        self.assertTrue(status["exceeded"])

    def test_status_reports_all_expected_keys(self) -> None:
        limiter, _ = _make_limiter()
        status = limiter.get_status("gemini-3.1-flash-lite")
        for key in (
            "model",
            "rpm_used",
            "rpm_limit",
            "rpd_used",
            "rpd_limit",
            "rpm_remaining",
            "rpd_remaining",
            "can_call",
            "exceeded",
        ):
            self.assertIn(key, status)


class ShouldSwitchModelTests(unittest.TestCase):
    """should_switch_model dua tren han muc hien tai."""

    def test_no_switch_while_under_limit(self) -> None:
        limiter, _ = _make_limiter()
        limiter.record_call("gemini-3.1-flash-lite")
        can_switch, next_model = limiter.should_switch_model("gemini-3.1-flash-lite")
        self.assertFalse(can_switch)
        self.assertEqual(next_model, "gemini-3.1-flash-lite")

    def test_switch_when_rpm_exhausted(self) -> None:
        # 3.8-flash chi co 5 RPM -> goi 5 lan la het phut.
        limiter, _ = _make_limiter()
        for _ in range(5):
            limiter.record_call("gemini-3.8-flash")

        can_switch, next_model = limiter.should_switch_model("gemini-3.8-flash")
        self.assertTrue(can_switch)
        # Uu tien model lite dau tien con han muc (3.1-flash-lite).
        self.assertEqual(next_model, "gemini-3.1-flash-lite")

    def test_switch_when_rpd_exhausted(self) -> None:
        limiter, clock = _make_limiter()
        for _ in range(20):
            limiter.record_call("gemini-3.8-flash")
        clock.advance(3600.0)  # RPM ve 0 nhung RPD = 20/20

        can_switch, next_model = limiter.should_switch_model("gemini-3.8-flash")
        self.assertTrue(can_switch)
        self.assertEqual(next_model, "gemini-3.1-flash-lite")

    def test_switch_skips_exhausted_candidate(self) -> None:
        # Het han muc ca 3.1-lite va 3.5-lite -> fallback tiep 3.8-flash.
        limiter, _ = _make_limiter()
        for _ in range(500):
            limiter.record_call("gemini-3.1-flash-lite")
            limiter.record_call("gemini-3.5-flash-lite")

        can_switch, next_model = limiter.should_switch_model("gemini-3.1-flash-lite")
        self.assertTrue(can_switch)
        self.assertEqual(next_model, "gemini-3.8-flash")

    def test_keeps_current_when_all_models_exhausted(self) -> None:
        limiter, _ = _make_limiter()
        for model in FALLBACK_MODEL_ORDER:
            quota = MODEL_LIMITS[model]
            for _ in range(quota.rpd):
                limiter.record_call(model)

        can_switch, next_model = limiter.should_switch_model("gemini-3.1-flash-lite")
        self.assertTrue(can_switch)
        # Khong con noi de chuyen: giu nguyen model (khong chuyen sang cung het).
        self.assertEqual(next_model, "gemini-3.1-flash-lite")

    def test_unknown_model_switches_to_known_lite(self) -> None:
        limiter, _ = _make_limiter()
        for _ in range(15):
            limiter.record_call("model-khong-ro")  # han mac dinh 15 RPM

        can_switch, next_model = limiter.should_switch_model("model-khong-ro")
        self.assertTrue(can_switch)
        self.assertEqual(next_model, "gemini-3.1-flash-lite")


class ResetAndThreadSafetyTests(unittest.TestCase):
    """reset() va an toan da luong (Streamlit chay nhieu request)."""

    def test_reset_single_model(self) -> None:
        limiter, _ = _make_limiter()
        limiter.record_call("gemini-3.1-flash-lite")
        limiter.record_call("gemini-3.8-flash")

        limiter.reset("gemini-3.1-flash-lite")
        self.assertEqual(limiter.get_status("gemini-3.1-flash-lite")["rpm_used"], 0)
        self.assertEqual(limiter.get_status("gemini-3.8-flash")["rpm_used"], 1)

    def test_reset_all(self) -> None:
        limiter, _ = _make_limiter()
        limiter.record_call("gemini-3.1-flash-lite")
        limiter.record_call("gemini-3.8-flash")

        limiter.reset()
        self.assertEqual(limiter.get_status("gemini-3.1-flash-lite")["rpm_used"], 0)
        self.assertEqual(limiter.get_status("gemini-3.8-flash")["rpm_used"], 0)

    def test_concurrent_record_calls_do_not_lose_counts(self) -> None:
        limiter, _ = _make_limiter()
        threads = [
            threading.Thread(
                target=lambda: [
                    limiter.record_call("gemini-3.1-flash-lite") for _ in range(50)
                ]
            )
            for _ in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        # 8 luong x 50 loi goi = 400 (duoi RPD 500 nen khong bi cat).
        self.assertEqual(
            limiter.get_status("gemini-3.1-flash-lite")["rpm_used"], 400
        )

    def test_get_rate_limiter_returns_singleton(self) -> None:
        first = get_rate_limiter()
        second = get_rate_limiter()
        self.assertIs(first, second)
        # Khong de singleton cua test anh huong test khac.
        first.reset()


class RealClockSmokeTests(unittest.TestCase):
    """Smoke test voi dong ho that (khong dieu khien duoc, chi kiem tra chay)."""

    def test_record_and_status_with_real_clock(self) -> None:
        limiter = RateLimiter()
        limiter.record_call("gemini-3.1-flash-lite")
        status = limiter.get_status("gemini-3.1-flash-lite")
        self.assertEqual(status["rpm_used"], 1)
        self.assertEqual(status["rpd_used"], 1)
        limiter.reset()

    def test_clock_default_is_time_time(self) -> None:
        limiter = RateLimiter()
        before = time.time()
        value = limiter._clock()
        self.assertGreaterEqual(value, before)


if __name__ == "__main__":
    unittest.main()
