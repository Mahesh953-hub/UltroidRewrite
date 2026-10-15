import unittest

from pyUltroid.agent._skills import (
    SKILLS,
    available_skills,
    render_skills,
    skill_names,
)


class SkillTests(unittest.TestCase):
    def test_skill_registry_is_declarative_and_unique(self):
        names = [skill.name for skill in SKILLS]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(names)

    def test_surface_filtering(self):
        self.assertIn("diagnostics", skill_names(".agent"))
        self.assertNotIn("diagnostics", skill_names(".ailocal"))

    def test_render_is_empty_when_no_skills_apply(self):
        self.assertEqual(render_skills(".nonexistent"), "")
        self.assertIn("research", render_skills(".ai"))


if __name__ == "__main__":
    unittest.main()
