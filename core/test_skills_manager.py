"""
Unit test cho core/skills_manager.py
====================================

Chay: & ".venv\\Scripts\\python.exe" -m unittest core.test_skills_manager -v

Tat ca test dung thu muc tam thoi (``tempfile.TemporaryDirectory``) de khong
lam hong file ``skills/skills.json`` that cua du an.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.skills_manager import (
    DEFAULT_SKILL_SEED,
    _seed_record,
    build_default_skills,
    delete_skill,
    get_default_skill,
    get_skill,
    get_skills,
    reset_to_default_skills,
    save_skill,
    set_default_skill,
    slugify,
)


class SkillsManagerTestCase(unittest.TestCase):
    """Bo test chung: moi test chay trong mot thu muc tam rieng."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.skills_file = Path(self._tmp.name) / "skills" / "skills.json"


class TestSeedAndSlug(SkillsManagerTestCase):
    def test_default_skill_seed_has_exact_plan_content(self) -> None:
        """Ky nang goc phai khop 100% noi dung ke hoach (anh chup Gemini)."""
        self.assertEqual(DEFAULT_SKILL_SEED["id"], "duyet-bai-bao")
        self.assertEqual(DEFAULT_SKILL_SEED["name"], "duyet-bai-bao")
        self.assertIn(
            "kiểm soát lỗi chính tả Tiếng Việt",
            DEFAULT_SKILL_SEED["description"],
        )
        instructions = DEFAULT_SKILL_SEED["instructions"]
        self.assertIn("Biên tập viên tin tức kỳ cựu", instructions)
        self.assertIn("SOÁT LỖI CHÍNH TẢ & NGỮ PHÁP TIẾNG VIỆT", instructions)
        self.assertIn("KIỂM CHỨNG NHÂN VẬT & SỰ KIỆN", instructions)
        self.assertIn("KẾT LUẬN BIÊN TẬP", instructions)
        self.assertIn("Đạt chuẩn xuất bản", instructions)

    def test_seed_record_is_isolated_copy(self) -> None:
        first = _seed_record()
        second = _seed_record()
        self.assertEqual(first["id"], "duyet-bai-bao")
        self.assertTrue(first["is_default"])
        first["name"] = "da-sua"
        self.assertNotEqual(first["name"], second["name"])
        # DEFAULT_SKILL_SEED goc khong bi anh huong.
        self.assertEqual(DEFAULT_SKILL_SEED["name"], "duyet-bai-bao")

    def test_slugify_vietnamese(self) -> None:
        self.assertEqual(
            slugify("Đề xuất sửa lỗi chính tả"), "de-xuat-sua-loi-chinh-ta"
        )
        self.assertEqual(slugify("Phóng sự Điều tra"), "phong-su-dieu-tra")
        self.assertEqual(slugify("  --- "), "ky-nang-moi")
        self.assertEqual(slugify(""), "ky-nang-moi")


class TestGetSkillsAndInit(SkillsManagerTestCase):
    def test_get_skills_initializes_default_file(self) -> None:
        """Lan goi dau tien tao file voi ky nang goc."""
        skills = get_skills(self.skills_file)
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0]["id"], "duyet-bai-bao")
        self.assertTrue(self.skills_file.is_file())

        saved = json.loads(self.skills_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["skills"][0]["id"], "duyet-bai-bao")

    def test_get_skills_recovers_from_corrupt_json(self) -> None:
        self.skills_file.parent.mkdir(parents=True, exist_ok=True)
        self.skills_file.write_text("{ hong nang luong", encoding="utf-8")
        skills = get_skills(self.skills_file)
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0]["id"], "duyet-bai-bao")

    def test_get_skills_recovers_from_empty_list(self) -> None:
        self.skills_file.parent.mkdir(parents=True, exist_ok=True)
        self.skills_file.write_text(json.dumps({"skills": []}), encoding="utf-8")
        skills = get_skills(self.skills_file)
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0]["id"], "duyet-bai-bao")

    def test_get_skills_recovers_from_invalid_entries(self) -> None:
        self.skills_file.parent.mkdir(parents=True, exist_ok=True)
        self.skills_file.write_text(
            json.dumps(
                {
                    "skills": [
                        {"id": "thieu-ten", "instructions": ""},
                        "khong-phai-dict",
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        skills = get_skills(self.skills_file)
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0]["id"], "duyet-bai-bao")

    def test_skills_schema_fields_present(self) -> None:
        skill = get_skills(self.skills_file)[0]
        for field in (
            "id",
            "name",
            "description",
            "instructions",
            "is_default",
            "created_at",
            "updated_at",
        ):
            self.assertIn(field, skill)

    def test_utf8_vietnamese_content_roundtrip(self) -> None:
        """Noi dung tieng Viet co dau phai duoc ghi/ doc nguyen vẹn."""
        skills = get_skills(self.skills_file)
        self.assertIn("kiểm soát", skills[0]["description"])
        self.assertIn("Fact-checker", skills[0]["instructions"])


class TestGetSkill(SkillsManagerTestCase):
    def test_get_skill_found(self) -> None:
        skill = get_skill("duyet-bai-bao", self.skills_file)
        self.assertIsNotNone(skill)
        assert skill is not None
        self.assertEqual(skill["id"], "duyet-bai-bao")
        self.assertIn("Fact-checker", skill["instructions"])

    def test_get_skill_not_found(self) -> None:
        self.assertIsNone(get_skill("khong-ton-tai", self.skills_file))

    def test_get_skill_empty_id(self) -> None:
        self.assertIsNone(get_skill("", self.skills_file))


class TestSaveSkill(SkillsManagerTestCase):
    def test_save_new_skill_with_auto_slug_id(self) -> None:
        saved = save_skill(
            {
                "name": "Phóng sự Điều tra",
                "description": "Kiem chung sau cho bai phong su",
                "instructions": "Hay doc ky tung cau truc bai phong su.",
            },
            self.skills_file,
        )
        self.assertEqual(saved["id"], "phong-su-dieu-tra")

        fetched = get_skill("phong-su-dieu-tra", self.skills_file)
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(fetched["description"], "Kiem chung sau cho bai phong su")
        self.assertEqual(
            fetched["instructions"], "Hay doc ky tung cau truc bai phong su."
        )

    def test_save_skill_keeps_existing_default(self) -> None:
        """Ky nang moi khong tu thay mac dinh khi khong yeu cau."""
        save_skill(
            {"name": "Chinh ta Sieu toc", "instructions": "Sua loi chinh ta nhanh."},
            self.skills_file,
        )
        default = get_default_skill(self.skills_file)
        self.assertEqual(default["id"], "duyet-bai-bao")

    def test_save_skill_with_is_default_flag(self) -> None:
        saved = save_skill(
            {
                "name": "Chinh ta Sieu toc",
                "instructions": "Sua loi chinh ta nhanh.",
                "is_default": True,
            },
            self.skills_file,
        )
        self.assertTrue(saved["is_default"])
        default = get_default_skill(self.skills_file)
        self.assertEqual(default["id"], "chinh-ta-sieu-toc")

    def test_update_existing_skill_preserves_created_at(self) -> None:
        first = save_skill(
            {"name": "Ky nang A", "instructions": "Chi dan A."},
            self.skills_file,
        )
        updated = save_skill(
            {
                "id": first["id"],
                "name": "Ky nang A",
                "description": "Mo ta moi",
                "instructions": "Chi dan A phien ban moi.",
            },
            self.skills_file,
        )
        self.assertEqual(updated["created_at"], first["created_at"])
        self.assertEqual(updated["description"], "Mo ta moi")

        # Khong bi nhan doi sau khi update.
        all_skills = get_skills(self.skills_file)
        same_id = [s for s in all_skills if s["id"] == first["id"]]
        self.assertEqual(len(same_id), 1)

    def test_save_skill_requires_name(self) -> None:
        with self.assertRaises(ValueError):
            save_skill({"instructions": "Chi dan."}, self.skills_file)
        with self.assertRaises(ValueError):
            save_skill({"name": "   ", "instructions": "Chi dan."}, self.skills_file)

    def test_save_skill_requires_instructions(self) -> None:
        with self.assertRaises(ValueError):
            save_skill({"name": "Ten ky nang"}, self.skills_file)
        with self.assertRaises(ValueError):
            save_skill({"name": "Ten", "instructions": "  "}, self.skills_file)

    def test_save_skill_rejects_non_dict(self) -> None:
        with self.assertRaises(ValueError):
            save_skill("khong phai dict", self.skills_file)  # type: ignore[arg-type]

    def test_saving_multiple_skills_keeps_one_default(self) -> None:
        save_skill({"name": "Ky nang A", "instructions": "A."}, self.skills_file)
        save_skill(
            {"name": "Ky nang B", "instructions": "B.", "is_default": True},
            self.skills_file,
        )
        skills = get_skills(self.skills_file)
        defaults = [s for s in skills if s.get("is_default")]
        self.assertEqual(len(skills), 3)  # goc + A + B
        self.assertEqual(len(defaults), 1)
        self.assertEqual(defaults[0]["name"], "Ky nang B")


class TestSetDefaultSkill(SkillsManagerTestCase):
    def test_set_default_skill_switches_flag(self) -> None:
        save_skill({"name": "Ky nang A", "instructions": "A."}, self.skills_file)
        save_skill({"name": "Ky nang B", "instructions": "B."}, self.skills_file)

        self.assertTrue(set_default_skill("ky-nang-b", self.skills_file))
        default = get_default_skill(self.skills_file)
        self.assertEqual(default["id"], "ky-nang-b")

        self.assertTrue(set_default_skill("duyet-bai-bao", self.skills_file))
        self.assertEqual(get_default_skill(self.skills_file)["id"], "duyet-bai-bao")

    def test_set_default_skill_unknown_id(self) -> None:
        get_skills(self.skills_file)  # khoi tao file
        self.assertFalse(set_default_skill("khong-co", self.skills_file))


class TestDeleteSkill(SkillsManagerTestCase):
    def test_delete_skill_success(self) -> None:
        save_skill({"name": "Ky nang A", "instructions": "A."}, self.skills_file)
        self.assertTrue(delete_skill("ky-nang-a", self.skills_file))
        self.assertIsNone(get_skill("ky-nang-a", self.skills_file))

    def test_delete_unknown_skill(self) -> None:
        get_skills(self.skills_file)
        self.assertFalse(delete_skill("khong-ton-tai", self.skills_file))

    def test_delete_last_skill_refused(self) -> None:
        """Khong cho xoa ky nang mac dinh duy nhat."""
        get_skills(self.skills_file)  # chi co duyet-bai-bao
        self.assertFalse(delete_skill("duyet-bai-bao", self.skills_file))
        self.assertIsNotNone(get_skill("duyet-bai-bao", self.skills_file))

    def test_delete_default_promotes_another(self) -> None:
        save_skill({"name": "Ky nang A", "instructions": "A."}, self.skills_file)
        # Luc nay mac dinh van la duyet-bai-bao, ky nang A la thu 2.
        self.assertTrue(delete_skill("duyet-bai-bao", self.skills_file))
        default = get_default_skill(self.skills_file)
        self.assertEqual(default["id"], "ky-nang-a")
        self.assertTrue(default["is_default"])


class TestGetDefaultSkill(SkillsManagerTestCase):
    def test_get_default_skill_returns_seed_first(self) -> None:
        default = get_default_skill(self.skills_file)
        self.assertEqual(default["id"], "duyet-bai-bao")
        self.assertTrue(default["is_default"])

    def test_get_default_skill_repairs_missing_default(self) -> None:
        """File bi sua dong tay (khong ai is_default) → tu dong sua lai."""
        self.skills_file.parent.mkdir(parents=True, exist_ok=True)
        self.skills_file.write_text(
            json.dumps(
                {
                    "skills": [
                        {
                            "id": "ky-nang-a",
                            "name": "Ky nang A",
                            "instructions": "A.",
                            "is_default": False,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        default = get_default_skill(self.skills_file)
        self.assertEqual(default["id"], "ky-nang-a")

        reloaded = get_skills(self.skills_file)
        self.assertTrue(reloaded[0]["is_default"])


class TestResetToDefaultSkills(SkillsManagerTestCase):
    def test_reset_removes_custom_skills(self) -> None:
        save_skill({"name": "Ky nang A", "instructions": "A."}, self.skills_file)
        save_skill({"name": "Ky nang B", "instructions": "B."}, self.skills_file)
        self.assertEqual(len(get_skills(self.skills_file)), 3)

        restored = reset_to_default_skills(self.skills_file)
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0]["id"], "duyet-bai-bao")

        skills = get_skills(self.skills_file)
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0]["id"], "duyet-bai-bao")


class TestGetActiveSkillsPromptSync(SkillsManagerTestCase):
    def test_single_skill_prompt(self) -> None:
        from core.skills_manager import get_active_skills_prompt_sync

        prompt = get_active_skills_prompt_sync("duyet-bai-bao", self.skills_file)
        self.assertIn("duyet-bai-bao", prompt)
        self.assertIn("Biên tập viên", prompt)

    def test_multi_skills_prompt(self) -> None:
        from core.skills_manager import get_active_skills_prompt_sync

        save_skill({"id": "skill-extra", "name": "Kỹ Năng Phụ", "instructions": "Quy tắc bổ sung phụ."}, self.skills_file)
        prompt = get_active_skills_prompt_sync(["duyet-bai-bao", "skill-extra"], self.skills_file)
        self.assertIn("duyet-bai-bao", prompt)
        self.assertIn("Kỹ Năng Phụ", prompt)
        self.assertIn("Quy tắc bổ sung phụ.", prompt)

    def test_empty_or_none_uses_default(self) -> None:
        from core.skills_manager import get_active_skills_prompt_sync

        prompt = get_active_skills_prompt_sync([], self.skills_file)
        self.assertIn("duyet-bai-bao", prompt)


if __name__ == "__main__":
    unittest.main()

