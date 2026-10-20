"""core/test_batch_safety.py - Kiem thu an toan cho cac file script .bat.

Kiem tra:
1. run_app.bat khong su dung findstr /R co khoang trang ma thieu /C: (nguyen nhan lam sapt Antigravity va Taskbar).
2. run_app.bat co day du bo loc: /C:":%PORT% ", LISTENING, bo qua PID 0 va 4, kiem tra tasklist chi kill python.exe/streamlit.exe.
3. run_gitnexus_tencent.bat va run_tencent.bat deu co bao ve tranh kill PID he thong (PID <= 4).
"""

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class BatchScriptSafetyTests(unittest.TestCase):
    def test_run_app_bat_safe_regex_and_guardrails(self):
        bat_file = PROJECT_ROOT / "run_app.bat"
        self.assertTrue(bat_file.exists(), f"Khong tim thay {bat_file}")
        content = bat_file.read_text(encoding="utf-8")

        # 1. Khong duoc chua doan regex loi cu gay kill hang loat
        self.assertNotIn(
            r'findstr /R "\:%PORT% .*LISTEN"',
            content,
            "run_app.bat van chua bieu thuc regex nguy hiem lam match tat ca cong LISTENING!",
        )

        # 2. Phai su dung /C:":%PORT% " kem LISTENING
        self.assertIn(
            r'/C:":%PORT% "',
            content,
            "run_app.bat phai dung /C: de so khop chinh xac cong %PORT%",
        )
        self.assertIn(
            'findstr "LISTENING"',
            content,
            "run_app.bat phai loc dong LISTENING",
        )

        # 3. Phai kiem tra loai tru PID 0 va 4
        self.assertIn('if not "%%a"=="0"', content)
        self.assertIn('if not "%%a"=="4"', content)

        # 4. Phai kiem tra tasklist chi kill python/streamlit
        self.assertIn("python.exe streamlit.exe", content)

    def test_run_gitnexus_tencent_bat_guardrails(self):
        bat_file = PROJECT_ROOT / "run_gitnexus_tencent.bat"
        self.assertTrue(bat_file.exists(), f"Khong tim thay {bat_file}")
        content = bat_file.read_text(encoding="utf-8")

        self.assertIn('if not "%%a"=="0"', content)
        self.assertIn('if not "%%a"=="4"', content)
        self.assertIn(r'/C:":8125 "', content)
        self.assertIn(r'/C:":8420 "', content)
        self.assertIn(r'/C:":4747 "', content)

    def test_run_tencent_bat_guardrails(self):
        bat_file = PROJECT_ROOT / "run_tencent.bat"
        self.assertTrue(bat_file.exists(), f"Khong tim thay {bat_file}")
        content = bat_file.read_text(encoding="utf-8")

        self.assertIn('if not "%%a"=="0"', content)
        self.assertIn('if not "%%a"=="4"', content)
        self.assertIn(r'/C:":8125 "', content)
        self.assertIn(r'/C:":8420 "', content)


if __name__ == "__main__":
    unittest.main()
