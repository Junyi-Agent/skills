#!/usr/bin/env python3
"""Tests for check.py on small fixture repos. Run: python3 skills/jy-repo-constrain-check/scripts/test_check.py"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check

CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
PRECOMMIT = """repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.8.6
    hooks:
      - id: ruff-format
      - id: ruff
  - repo: https://github.com/shellcheck-py/shellcheck-py
    rev: v0.10.0.1
    hooks:
      - id: shellcheck
{shellcheck_args}"""


class Fixture:
    def __init__(self, files: dict[str, str]):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        subprocess.run(
            ["git", "init", "-q", "-b", "main", str(self.root)], check=True, env=CLEAN_ENV
        )
        for name, text in files.items():
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)

    def __enter__(self) -> Path:
        return self.root

    def __exit__(self, *exc: object) -> None:
        self.tmp.cleanup()


def fmt_lint(files: dict[str, str]) -> check.Section:
    with Fixture(files) as root:
        return check.check_fmt_lint(root, [])


def ci(files: dict[str, str]) -> check.Section:
    with Fixture(files) as root:
        return check.check_ci(root, remote=False)


def facts(s: check.Section) -> str:
    return "\n".join(s.facts)


class StrictLintTest(unittest.TestCase):
    def test_shellcheck_severity_is_not_strict(self):
        for level in ("style", "info", "warning", "error"):
            args = f"        args: [--severity={level}]\n"
            s = fmt_lint({".pre-commit-config.yaml": PRECOMMIT.format(shellcheck_args=args)})
            self.assertEqual(s.verdict, "WARN", level)
            self.assertFalse(any(e.startswith("严格") for e in s.evidence), level)

    def test_shellcheck_enable_all_is_strict(self):
        args = "        args: [--enable=all]\n"
        s = fmt_lint({".pre-commit-config.yaml": PRECOMMIT.format(shellcheck_args=args)})
        self.assertEqual(s.verdict, "JUDGE")

    def test_explicit_ruff_select_is_evidence(self):
        for cfg in (
            'select = ["E", "F", "B", "UP", "SIM", "RUF"]\n',
            '[lint]\nselect = ["ALL"]\n',
            '[lint]\nextend-select = ["B"]\n',
        ):
            s = fmt_lint(
                {".pre-commit-config.yaml": PRECOMMIT.format(shellcheck_args=""), "ruff.toml": cfg}
            )
            self.assertEqual(s.verdict, "JUDGE", cfg)
            self.assertTrue(any("ruff.toml" in e for e in s.evidence), cfg)


WORKFLOW = "name: ci\non:\n{on}jobs:\n  t:\n    steps:\n      - run: pre-commit run --all-files\n      - run: pytest\n"


class CiTest(unittest.TestCase):
    def test_trigger_combinations(self):
        cases = {
            "  pull_request:\n  push:\n    branches: [main]\n": ("有", "有", "JUDGE"),
            "  push:\n    branches: [main]\n": ("无", "有", "WARN"),
            "  pull_request:\n    branches: [main]\n": ("有", "无", "WARN"),
        }
        for on, (pr, push, verdict) in cases.items():
            s = ci({".github/workflows/ci.yml": WORKFLOW.format(on=on)})
            self.assertIn(f"PR 触发：{pr}；push 触发：{push}", facts(s), on)
            self.assertEqual(s.verdict, verdict, on)

    def test_inline_on_forms(self):
        for on_line in ("on: [push, pull_request]\n", "on: [pull_request, push]  # both\n"):
            text = WORKFLOW.replace("on:\n{on}", on_line)
            s = ci({".github/workflows/ci.yml": text})
            self.assertIn("PR 触发：有；push 触发：有", facts(s), on_line)

    def test_only_yaml_files_under_workflows(self):
        s = ci(
            {
                ".github/workflows/ci.yml": WORKFLOW.format(on="  push:\n"),
                ".github/workflows/README.md": "on: pull_request\n",
                ".github/workflows/weird.yml/inner.txt": "pull_request\n",
            }
        )
        self.assertNotIn("README.md", facts(s))
        self.assertNotIn("inner.txt", facts(s))
        self.assertIn("PR 触发：无", facts(s))


class BranchProtectionParseTest(unittest.TestCase):
    def test_repo_and_branch_names(self):
        seen = []
        orig_git, orig_run, orig_which = check.git, check.subprocess.run, check.shutil.which

        def fake_git(root: Path, *args: str) -> str:
            if args[:2] == ("remote", "get-url"):
                return self.url
            if args[0] == "symbolic-ref":
                return self.head
            return ""

        def fake_run(cmd, **kw):
            seen.append(cmd[2])
            return subprocess.CompletedProcess(cmd, 0, '{"contexts": ["ci"]}', "")

        check.git, check.subprocess.run, check.shutil.which = fake_git, fake_run, lambda _: "gh"
        try:
            for self.url, self.head, want in (
                (
                    "git@github.com:user/foo.github.io.git",
                    "origin/main",
                    "user/foo.github.io/branches/main",
                ),
                (
                    "https://github.com/user/my.repo",
                    "origin/release/2.0",
                    "user/my.repo/branches/release/2.0",
                ),
                ("https://github.com/user/plain.git/", "", "user/plain/branches/main"),
            ):
                check.branch_protection(Path("."))
                self.assertEqual(re.sub(r"^repos/|/protection.*$", "", seen[-1]), want)
        finally:
            check.git, check.subprocess.run, check.shutil.which = orig_git, orig_run, orig_which


class FilesUnderTest(unittest.TestCase):
    def test_skips_pycache_and_pyc(self):
        with Fixture(
            {
                ".claude/hooks/guard.py": "x",
                ".claude/hooks/__pycache__/guard.cpython-312.pyc": "x",
                ".claude/hooks/old.pyc": "x",
            }
        ) as root:
            names = [f.name for f in check.files_under(root / ".claude" / "hooks")]
        self.assertEqual(names, ["guard.py"])


if __name__ == "__main__":
    unittest.main()
