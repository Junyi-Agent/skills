#!/usr/bin/env python3
"""Tests for check.py on small fixture repos. Run: python3 skills/jy-repo-constrain-check/scripts/test_check.py"""

from __future__ import annotations

import json
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

    def test_verbose_is_not_a_weakening(self):
        args = "        args: [--enable=all]\n        verbose: true\n"
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


class AgentGateTest(unittest.TestCase):
    def test_agent_call_forms(self):
        found = (
            'subprocess.run(["claude", "-p", prompt])',
            'claude -p "$PROMPT" < diff.txt',
            'cmd = ["codex", "exec", prompt]',
            'claude = shutil.which("claude")',
            "exe = which('codex')",
        )
        for line in found:
            self.assertTrue(check.AGENT_CALL.search(line), line)
        for line in ('print("claude")', "which(python)", 'run(["codex", "--version"])'):
            self.assertFalse(check.AGENT_CALL.search(line), line)

    def test_which_then_argv_list_is_detected(self):
        gate = (
            "import shutil, subprocess, sys\n"
            'claude = shutil.which("claude")\n'
            'cmd = [\n    claude,\n    "-p",\n    PROMPT,\n]\n'
            "sys.exit(0 if approvals >= 2 else 1)\n"
        )
        with Fixture({"tools/review_gate.py": gate}) as root:
            s = check.check_agent_gate(root, [])
        self.assertEqual(s.verdict, "JUDGE")
        self.assertTrue(any("review_gate.py" in f for f in s.facts))


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

    def test_only_the_on_block_counts(self):
        docker = WORKFLOW.format(on="  pull_request:\n") + (
            "      - uses: docker/build-push-action@v6\n        with:\n          push: true\n"
        )
        guarded = WORKFLOW.format(on="  push:\n    branches: [main]\n").replace(
            "    steps:", "    if: github.event_name != 'pull_request'\n    steps:"
        )
        cases = {docker: ("有", "无"), guarded: ("无", "有")}
        for text, (pr, push) in cases.items():
            s = ci({".github/workflows/ci.yml": text})
            self.assertIn(f"PR 触发：{pr}；push 触发：{push}", facts(s), text)
            self.assertEqual(s.verdict, "WARN", text)

    def test_zero_indent_event_list(self):
        text = WORKFLOW.format(on="- push\n- pull_request\n")
        self.assertIn("PR 触发：有；push 触发：有", facts(ci({".github/workflows/ci.yml": text})))
        events, _ = check.gha_triggers("on:\n- push\njobs:\n  t:\n    runs-on: x\n")
        self.assertEqual(list(events), ["push"])

    def test_tag_only_push_is_not_a_branch_push(self):
        files = {
            ".github/workflows/ci.yml": WORKFLOW.format(on="  pull_request:\n"),
            ".github/workflows/release.yml": WORKFLOW.format(on="  push:\n    tags: ['v*']\n"),
        }
        s = ci(files)
        self.assertIn("push 触发：无", facts(s))
        self.assertTrue(any("release.yml" in e and "tags" in e for e in s.evidence), s.evidence)
        files[".github/workflows/release.yml"] = WORKFLOW.format(
            on="  push:\n    tags: ['v*']\n    branches: [main]\n"
        )
        self.assertIn("push 触发：有", facts(ci(files)))

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


def unix(files: dict[str, str]) -> check.Section:
    with Fixture(files) as root:
        return check.check_unix(root)


CLI_PY = """import argparse, json, sys

def main() -> int:
    data = sys.stdin.read()
    if not data:
        print("no input", file=sys.stderr)
        return 1
    json.dump({"n": len(data)}, sys.stdout)
    return 0

sys.exit(main())
"""


class UnixTest(unittest.TestCase):
    def test_app_repo_is_not_applicable(self):
        s = unix(
            {
                "package.json": '{"dependencies": {"next": "14", "react": "18"}}',
                "src/pages/index.tsx": "export default function Home() { return null }",
            }
        )
        self.assertEqual(s.verdict, "N/A")
        self.assertIn("类型初判：app 类", facts(s))

    def test_cli_repo_collects_signals(self):
        s = unix(
            {
                "pyproject.toml": '[project]\nname = "t"\n[project.scripts]\nt = "t.cli:main"\n',
                "t/cli.py": CLI_PY,
                "tests/test_cli.py": "import sys\ninput()\n",
            }
        )
        self.assertEqual(s.verdict, "JUDGE")
        self.assertIn("类型初判：工具类", facts(s))
        for signal in ("读 stdin 1", "写 stderr 1", "显式退出码 1", "机器可读输出 1", "交互提问 0"):
            self.assertIn(signal, facts(s))
        self.assertFalse(any("tests/" in e for e in s.evidence))

    def test_lists_subcommand_names(self):
        s = unix(
            {
                "t/cli.py": (
                    "import argparse\np = argparse.ArgumentParser()\n"
                    "sub = p.add_subparsers()\nsub.add_parser('get')\n"
                    'sub.add_parser("delete")\np.add_argument("-o", "--output")\n'
                ),
                "t/more.py": "import click\n@cli.command(name='apply')\ndef apply(): ...\n",
                "cmd/root.go": 'var c = &cobra.Command{Use: "describe"}\n',
            }
        )
        line = next(f for f in s.facts if f.startswith("声明的子命令："))
        self.assertEqual(
            sorted(re.findall(r"`([\w-]+)`", line)), ["apply", "delete", "describe", "get"]
        )
        self.assertIn("子命令 3", facts(s))
        self.assertIn("-o/--output 选输出格式 1", facts(s))

    def test_finds_exemption_adrs(self):
        s = unix(
            {
                "t/cli.py": CLI_PY,
                "docs/adr/0001-exempt.md": (
                    "# 0001 豁免 t 的 kubectl 式结构\n\n状态：采纳\n\n"
                    "只有一个功能。批准人：用户（2026-11-20）\n"
                ),
                "docs/adr/0002-other.md": "# 0002 用 ruff\n\n状态：采纳\n",
                "docs/adr/0003-db.md": "# 0003 豁免数据库迁移评审\n\n状态：提议\n",
                "docs/adr/README.md": "CLI 豁免要写 ADR\n",
            }
        )
        self.assertIn("提到 CLI 风格豁免的 ADR：`docs/adr/0001-exempt.md`（状态：采纳）", facts(s))
        self.assertNotIn("0002", facts(s))
        self.assertNotIn("0003", facts(s))
        self.assertNotIn("README", facts(s))
        self.assertTrue(any("0001-exempt.md:1" in e for e in s.evidence))

    def test_no_subcommands_or_exemptions(self):
        s = unix({"t/cli.py": CLI_PY})
        self.assertIn("声明的子命令：没找到", facts(s))
        self.assertIn("提到 CLI 风格豁免的 ADR：没有", facts(s))

    def test_mixed_and_unknown(self):
        s = unix({"package.json": '{"bin": {"t": "cli.js"}, "dependencies": {"express": "4"}}'})
        self.assertIn("类型初判：两类都有", facts(s))
        self.assertEqual(s.verdict, "JUDGE")
        s = unix({"README.md": "# docs only\n"})
        self.assertIn("类型初判：不确定", facts(s))


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


class CliTest(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        script = Path(check.__file__).resolve()
        return subprocess.run(
            [sys.executable, str(script), *args],
            capture_output=True,
            text=True,
            env=CLEAN_ENV,
            check=False,
        )

    def test_rejects_unknown_arguments(self):
        for args in (["--no-remot"], ["-x"], [".", "extra"]):
            r = self.run_cli(*args)
            self.assertEqual(r.returncode, 2, args)
            self.assertEqual(r.stdout, "", args)
            self.assertIn("Usage", r.stderr, args)

    def test_rejects_missing_or_non_directory_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "file.txt"
            f.write_text("x")
            for path in (str(Path(tmp) / "missing"), str(f)):
                r = self.run_cli(path, "--no-remote")
                self.assertEqual(r.returncode, 2, path)
                self.assertEqual(r.stdout, "", path)
                self.assertIn("not a directory", r.stderr, path)

    def test_json_output(self):
        with Fixture({"README.md": "# x\n"}) as root:
            r = self.run_cli(str(root), "--no-remote", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual([s["num"] for s in data["sections"]], list(range(1, 9)))
        for s in data["sections"]:
            self.assertIn(s["verdict"], {"PASS", "WARN", "FAIL", "JUDGE", "N/A"})
            for key in ("title", "facts", "evidence", "judge"):
                self.assertIn(key, s)

    def test_help(self):
        r = self.run_cli("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("Usage", r.stdout)


if __name__ == "__main__":
    unittest.main()
