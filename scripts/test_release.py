#!/usr/bin/env python3
"""Tests for release.py on throwaway git repos. Run: python3 scripts/test_release.py"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "release.py"
# git sets GIT_* (e.g. GIT_INDEX_FILE) while running hooks; keep them out of the test repos.
CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def changelog(*versions: str) -> str:
    return "# Changelog\n\n" + "".join(f"## {v} — 2026-11-21\n\n- x\n\n" for v in versions)


class Repo:
    """A git repo with release.py at scripts/, so the script's ROOT is this repo."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "scripts").mkdir()
        shutil.copy(SCRIPT, self.root / "scripts" / "release.py")
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
            cwd=self.root,
            check=True,
            env=CLEAN_ENV,
            capture_output=True,
        )

    def write(self, files: dict[str, str | None]) -> None:
        for name, text in files.items():
            p = self.root / name
            if text is None:
                p.unlink()
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)

    def commit(self, files: dict[str, str | None]) -> None:
        self.write(files)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "c")

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.root / "scripts" / "release.py"), *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            env=CLEAN_ENV,
            check=False,
        )

    def __enter__(self) -> Repo:
        return self

    def __exit__(self, *exc) -> None:
        self.tmp.cleanup()


CHECK = ("check", "version")


class FormatTest(unittest.TestCase):
    def test_ok_is_quiet(self):
        with Repo() as r:
            r.write({"VERSION": "1.0.0\n", "CHANGELOG.md": changelog("1.0.0")})
            p = r.run(*CHECK)
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "", ""))

    def test_bad_files(self):
        cases = {
            "VERSION 文件不存在": {"CHANGELOG.md": changelog("1.0.0")},
            "不是 X.Y.Z": {"VERSION": "v1.0\n", "CHANGELOG.md": changelog("v1.0")},
            "CHANGELOG.md 不存在": {"VERSION": "1.0.0\n"},
            "第一个版本标题是 0.9.0": {"VERSION": "1.0.0\n", "CHANGELOG.md": changelog("0.9.0")},
            "没有 ## 标题": {"VERSION": "1.0.0\n", "CHANGELOG.md": "# Changelog\n"},
        }
        for want, files in cases.items():
            with Repo() as r:
                r.write(files)
                p = r.run(*CHECK)
            self.assertEqual(p.returncode, 1, want)
            self.assertIn(want, p.stderr)

    def test_json_output(self):
        with Repo() as r:
            r.write({"VERSION": "1.0.0\n", "CHANGELOG.md": changelog("0.9.0")})
            p = r.run(*CHECK, "-o", "json")
        self.assertEqual(p.returncode, 1)
        data = json.loads(p.stdout)
        self.assertEqual((data["version"], data["ok"]), ("1.0.0", False))
        self.assertEqual(len(data["problems"]), 1)

    def test_bad_arguments(self):
        with Repo() as r:
            for args in ([], ["check"], ["get", "version"], [*CHECK, "--bas", "main"]):
                p = r.run(*args)
                self.assertEqual(p.returncode, 2, args)
                self.assertIn("usage:", p.stderr, args)


class BumpTest(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()
        self.repo.commit(
            {
                "VERSION": "1.2.0\n",
                "CHANGELOG.md": changelog("1.2.0"),
                "skills/a/SKILL.md": "a\n",
                "README.md": "r\n",
            }
        )
        self.repo.git("branch", "base")

    def tearDown(self):
        self.repo.__exit__()

    def test_skill_change_without_bump_fails(self):
        self.repo.commit({"skills/a/SKILL.md": "b\n"})
        p = self.repo.run(*CHECK, "--base", "base")
        self.assertEqual(p.returncode, 1)
        self.assertIn("skills/a/SKILL.md", p.stderr)
        self.assertIn("要从 1.2.0 升上去", p.stderr)

    def test_skill_change_with_bump_passes(self):
        for v in ("1.2.1", "1.3.0", "2.0.0"):
            self.repo.commit(
                {
                    "skills/a/SKILL.md": v,
                    "VERSION": v + "\n",
                    "CHANGELOG.md": changelog(v, "1.2.0"),
                }
            )
            p = self.repo.run(*CHECK, "--base", "base")
            self.assertEqual(p.returncode, 0, (v, p.stderr))

    def test_other_changes_need_no_bump(self):
        self.repo.commit({"README.md": "changed\n", "scripts/x.py": "x\n"})
        self.assertEqual(self.repo.run(*CHECK, "--base", "base").returncode, 0)

    def test_lower_version_fails(self):
        self.repo.commit({"VERSION": "1.1.9\n", "CHANGELOG.md": changelog("1.1.9")})
        p = self.repo.run(*CHECK, "--base", "base")
        self.assertEqual(p.returncode, 1)
        self.assertIn("低于 base 上的 1.2.0", p.stderr)

    def test_version_compares_numerically(self):
        self.repo.commit(
            {"skills/a/SKILL.md": "b\n", "VERSION": "1.10.0\n", "CHANGELOG.md": changelog("1.10.0")}
        )
        self.assertEqual(self.repo.run(*CHECK, "--base", "base").returncode, 0)

    def test_base_without_version_file(self):
        self.repo.commit({"VERSION": None, "CHANGELOG.md": None})
        self.repo.git("branch", "-f", "base")
        self.repo.commit(
            {"skills/a/SKILL.md": "b\n", "VERSION": "1.0.0\n", "CHANGELOG.md": changelog("1.0.0")}
        )
        self.assertEqual(self.repo.run(*CHECK, "--base", "base").returncode, 0)

    def test_behind_branch_without_skill_changes_passes(self):
        """A branch made before base bumped the version, touching only other files."""
        self.repo.git("checkout", "-q", "-b", "feat")
        self.repo.commit({"README.md": "changed\n"})
        self.repo.git("checkout", "-q", "base")
        self.repo.commit(
            {"skills/a/SKILL.md": "b\n", "VERSION": "1.3.0\n", "CHANGELOG.md": changelog("1.3.0")}
        )
        self.repo.git("checkout", "-q", "feat")
        p = self.repo.run(*CHECK, "--base", "base")
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_behind_branch_with_skill_change_needs_rebase(self):
        self.repo.git("checkout", "-q", "-b", "feat")
        self.repo.commit(
            {"skills/a/SKILL.md": "f\n", "VERSION": "1.2.1\n", "CHANGELOG.md": changelog("1.2.1")}
        )
        self.repo.git("checkout", "-q", "base")
        self.repo.commit({"VERSION": "1.3.0\n", "CHANGELOG.md": changelog("1.3.0")})
        self.repo.git("checkout", "-q", "feat")
        p = self.repo.run(*CHECK, "--base", "base")
        self.assertEqual(p.returncode, 1)
        self.assertIn("低于 base 上的 1.3.0", p.stderr)
        self.assertIn("rebase", p.stderr)

    def test_ref_is_checked_instead_of_the_work_tree(self):
        """pre-push passes the pushed commit, which need not be the checked-out one."""
        self.repo.commit({"skills/a/SKILL.md": "no bump\n"})
        self.repo.git("branch", "bad")
        self.repo.git("reset", "-q", "--hard", "base")
        self.repo.commit({"README.md": "fine\n"})
        p = self.repo.run(*CHECK, "--base", "base", "--ref", "bad")
        self.assertEqual(p.returncode, 1)
        self.assertIn("skills/a/SKILL.md", p.stderr)
        self.assertEqual(self.repo.run(*CHECK, "--base", "base", "--ref", "HEAD").returncode, 0)
        # the work tree does not matter with --ref
        self.repo.write({"VERSION": "bad\n"})
        self.assertEqual(self.repo.run(*CHECK, "--base", "base", "--ref", "HEAD").returncode, 0)

    def test_ref_without_files(self):
        self.repo.commit({"VERSION": None})
        p = self.repo.run(*CHECK, "--ref", "HEAD")
        self.assertEqual(p.returncode, 1)
        self.assertIn("VERSION 文件不存在", p.stderr)

    def test_null_ref_is_a_deleted_branch(self):
        p = self.repo.run(*CHECK, "--base", "base", "--ref", "0" * 40)
        self.assertEqual((p.returncode, p.stderr), (0, ""))

    def test_unknown_base_is_a_git_error(self):
        p = self.repo.run(*CHECK, "--base", "no-such-ref")
        self.assertEqual(p.returncode, 2)
        self.assertIn("release.py: git diff", p.stderr)
        p = self.repo.run(*CHECK, "--ref", "no-such-ref")
        self.assertEqual(p.returncode, 2)
        self.assertIn("release.py: git rev-parse", p.stderr)


if __name__ == "__main__":
    unittest.main()
