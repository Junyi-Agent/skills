#!/usr/bin/env python3
"""Tests for repo_check.py on throwaway trees. Run: python3 scripts/test_repo_check.py"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from repo_check import check_adrs, check_skills

HERE = Path(__file__).resolve().parent

SCRIPT = HERE / "repo_check.py"


def adr(status: str, verification: str = "（只由人类填写。）") -> str:
    return f"# 0001 x\n\n状态：{status}\n\n## 背景\n\nx\n\n## 验证\n\n{verification}\n"


def index(*rows: tuple[str, str, str]) -> str:
    body = "".join(f"| [{n}]({f}) | 决策 | {s} |\n" for n, f, s in rows)
    return "# ADR\n\n## 列表\n\n| # | 决策 | 状态 |\n|---|---|---|\n" + body


class Tree:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".claude" / "skills").mkdir(parents=True)
        (self.root / "docs" / "adr").mkdir(parents=True)

    def write(self, rel: str, text: str) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def skill(self, name: str, link: bool = True, readme: bool = True) -> None:
        self.write(f"skills/{name}/SKILL.md", f'---\nname: "{name}"\ndescription: "x"\n---\n')
        if link:
            (self.root / ".claude" / "skills" / name).symlink_to(f"../../skills/{name}")
        if readme:
            old = (
                (self.root / "README.md").read_text() if (self.root / "README.md").exists() else ""
            )
            self.write("README.md", old + f"| [`{name}`](skills/{name}) | x |\n")


class SkillsTest(unittest.TestCase):
    def setUp(self):
        self.t = Tree()

    def tearDown(self):
        self.t.tmp.cleanup()

    def test_no_skills_directory(self):
        """A repo that copies this script (and links the skills in from a submodule)."""
        (self.t.root / ".claude" / "skills" / "jy-a").symlink_to("../jy-skills/skills/jy-a")
        self.assertEqual(check_skills(self.t.root), [])

    def test_consistent(self):
        self.t.skill("jy-a")
        self.t.skill("jy-b")
        self.assertEqual(check_skills(self.t.root), [])

    def test_missing_symlink(self):
        self.t.skill("jy-a", link=False)
        [p] = check_skills(self.t.root)
        self.assertIn("ln -s ../../skills/jy-a .claude/skills/jy-a", p)

    def test_wrong_target(self):
        self.t.skill("jy-a", link=False)
        (self.t.root / ".claude" / "skills" / "jy-a").symlink_to("/abs/skills/jy-a")
        [p] = check_skills(self.t.root)
        self.assertIn("应为 ../../skills/jy-a", p)

    def test_plain_directory_instead_of_symlink(self):
        self.t.skill("jy-a", link=False)
        (self.t.root / ".claude" / "skills" / "jy-a").mkdir()
        [p] = check_skills(self.t.root)
        self.assertIn("不是软链接", p)

    def test_dangling_symlink(self):
        self.t.skill("jy-a")
        (self.t.root / ".claude" / "skills" / "jy-gone").symlink_to("../../skills/jy-gone")
        [p] = check_skills(self.t.root)
        self.assertIn("jy-gone", p)

    def test_missing_readme_row(self):
        self.t.skill("jy-a", readme=False)
        self.t.write("README.md", "")
        [p] = check_skills(self.t.root)
        self.assertIn("README.md 的 skill 表里没有 jy-a", p)

    def test_frontmatter_name_mismatch(self):
        self.t.skill("jy-a")
        self.t.write("skills/jy-a/SKILL.md", '---\nname: "jy-b"\n---\n')
        [p] = check_skills(self.t.root)
        self.assertIn("应与目录名", p)

    def test_internal_skill(self):
        """A real directory with a SKILL.md in .claude/skills/ is an internal skill."""
        self.t.skill("jy-a")
        self.t.write(".claude/skills/jy-internal/SKILL.md", '---\nname: "jy-internal"\n---\n')
        self.assertEqual(check_skills(self.t.root), [])

    def test_internal_skill_name_mismatch(self):
        self.t.skill("jy-a")
        self.t.write(".claude/skills/jy-internal/SKILL.md", '---\nname: "jy-x"\n---\n')
        [p] = check_skills(self.t.root)
        self.assertIn(".claude/skills/jy-internal/SKILL.md 的 name", p)

    def test_internal_skill_in_readme(self):
        self.t.skill("jy-a")
        self.t.write(".claude/skills/jy-internal/SKILL.md", '---\nname: "jy-internal"\n---\n')
        self.t.write("README.md", (self.t.root / "README.md").read_text() + "jy-internal\n")
        [p] = check_skills(self.t.root)
        self.assertIn("README.md 提到了内部 skill jy-internal", p)


class AdrsTest(unittest.TestCase):
    def setUp(self):
        self.t = Tree()

    def tearDown(self):
        self.t.tmp.cleanup()

    def test_consistent(self):
        self.t.write("docs/adr/0001-a.md", adr("采纳"))
        self.t.write("docs/adr/0002-b.md", adr("已取代（见 0001）"))
        self.t.write("docs/adr/template.md", adr("提议"))
        self.t.write(
            "docs/adr/README.md",
            index(("0001", "0001-a.md", "采纳"), ("0002", "0002-b.md", "已取代（见 0001）")),
        )
        self.assertEqual(check_adrs(self.t.root), [])

    def test_missing_row(self):
        self.t.write("docs/adr/0001-a.md", adr("采纳"))
        self.t.write("docs/adr/README.md", index())
        [p] = check_adrs(self.t.root)
        self.assertIn("列表里没有 0001-a.md", p)

    def test_status_mismatch(self):
        self.t.write("docs/adr/0001-a.md", adr("采纳"))
        self.t.write("docs/adr/README.md", index(("0001", "0001-a.md", "提议")))
        [p] = check_adrs(self.t.root)
        self.assertIn("以文件为准", p)

    def test_row_to_missing_file(self):
        self.t.write("docs/adr/README.md", index(("0009", "0009-z.md", "采纳")))
        [p] = check_adrs(self.t.root)
        self.assertIn("不存在的文件 0009-z.md", p)

    def test_bad_status(self):
        self.t.write("docs/adr/0001-a.md", adr("Accepted"))
        self.t.write("docs/adr/README.md", index(("0001", "0001-a.md", "Accepted")))
        [p] = check_adrs(self.t.root)
        self.assertIn("状态行应为", p)

    def test_verified_without_human_verification(self):
        self.t.write("docs/adr/0001-a.md", adr("已验证"))
        self.t.write("docs/adr/README.md", index(("0001", "0001-a.md", "已验证")))
        [p] = check_adrs(self.t.root)
        self.assertIn("没有验证人和日期", p)

    def test_verified_with_human_verification(self):
        text = adr("已验证", "（只由人类填写。）\n\n验证人：Junyi，2026-11-21，测了 x，通过。")
        self.t.write("docs/adr/0001-a.md", text)
        self.t.write("docs/adr/README.md", index(("0001", "0001-a.md", "已验证")))
        self.assertEqual(check_adrs(self.t.root), [])


class CliTest(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False
        )

    def test_this_repo_passes(self):
        for resource in ("skills", "adrs"):
            r = self.run_cli("check", resource, "-o", "json")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(
                json.loads(r.stdout), {"resource": resource, "ok": True, "problems": []}
            )

    def test_bad_arguments(self):
        self.assertEqual(self.run_cli("check").returncode, 2)
        self.assertEqual(self.run_cli("check", "things").returncode, 2)


if __name__ == "__main__":
    unittest.main()
