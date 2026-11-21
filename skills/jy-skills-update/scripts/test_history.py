#!/usr/bin/env python3
"""Tests for history.py, on synthetic session files. Run: python3 test_history.py"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import history

SCRIPT = Path(__file__).resolve().parent / "history.py"


def user(text, ts="2026-11-20T10:00:00Z", **extra):
    return {"type": "user", "timestamp": ts, "message": {"content": text}, **extra}


def skill_call(name, ts="2026-11-20T10:00:01Z"):
    item = {"type": "tool_use", "name": "Skill", "input": {"skill": name}}
    return {"type": "assistant", "timestamp": ts, "message": {"content": [item]}}


def tool_result(text, error, ts="2026-11-20T10:00:02Z"):
    item = {"type": "tool_result", "tool_use_id": "t", "content": text, "is_error": error}
    return {"type": "user", "timestamp": ts, "message": {"content": [item]}}


def command(name, ts="2026-11-20T10:00:00Z"):
    return user(
        f"<command-message>{name}</command-message>\n<command-name>/{name}</command-name>", ts
    )


def write_session(path: Path, records: list, bad_lines: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r) for r in records]
    if bad_lines:
        lines.insert(1, "{not json")
        lines.insert(2, "[1, 2]")
    path.write_text("\n".join(lines) + "\n")


def git(cwd, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}  # set by git hooks
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, env=env)


class ScanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def scan(self, records, after=5, bad_lines=False):
        f = self.tmp / "s.jsonl"
        write_session(f, records, bad_lines)
        return history.scan_session(f, "dir", True, "jy-", after)

    def test_command_invocation_with_following_messages(self):
        found = self.scan(
            [
                user("hello before"),
                command("jy-repo-review"),
                user("<system-reminder>ignore</system-reminder>"),
                user("<task-notification>x</task-notification>"),
                tool_result("ok", False),
                user("这条不对，重来"),
                user("y" * 900),
            ],
            bad_lines=True,
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["skill"], "jy-repo-review")
        texts = [m["text"] for m in found[0]["user_messages"]]
        self.assertEqual(texts[0], "这条不对，重来")
        self.assertEqual(len(texts[1]), history.MSG_LIMIT)
        self.assertEqual(found[0]["errors"], [])

    def test_skill_tool_call_with_plugin_prefix_and_errors(self):
        found = self.scan(
            [
                user("check"),
                skill_call("plug:jy-repo-quiz"),
                tool_result("Traceback " + "e" * 500, True),
                skill_call("other-skill"),
            ]
        )
        self.assertEqual([f["skill"] for f in found], ["jy-repo-quiz"])
        self.assertEqual(len(found[0]["errors"]), 1)
        self.assertEqual(len(found[0]["errors"][0]["text"]), history.ERR_LIMIT)

    def test_window_ends_at_next_invocation_and_after_limit(self):
        found = self.scan(
            [
                command("jy-a"),
                user("one"),
                user("two"),
                user("three"),
                command("jy-b"),
                tool_result("boom", True),
            ],
            after=2,
        )
        self.assertEqual([f["skill"] for f in found], ["jy-a", "jy-b"])
        self.assertEqual([m["text"] for m in found[0]["user_messages"]], ["one", "two"])
        self.assertEqual(found[0]["errors"], [])
        self.assertEqual(len(found[1]["errors"]), 1)

    def test_command_then_skill_call_counts_once(self):
        found = self.scan([command("jy-a"), skill_call("jy-a"), user("ok")])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["user_messages"][0]["text"], "ok")

    def test_sidechain_and_meta_skipped(self):
        found = self.scan(
            [
                user("<command-name>/jy-a</command-name>", isSidechain=True),
                command("jy-b"),
                user("meta", isMeta=True),
                user("real"),
            ]
        )
        self.assertEqual([f["skill"] for f in found], ["jy-b"])
        self.assertEqual([m["text"] for m in found[0]["user_messages"]], ["real"])

    def test_unreadable_file(self):
        self.assertEqual(history.read_records(self.tmp / "missing.jsonl"), [])


class SessionDirsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self.projects = self.tmp / "projects"
        self.repo = self.tmp / "my.proj"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(
            self.repo,
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "x",
        )

    def session_dir(self, path: Path, cwd: str) -> Path:
        d = self.projects / history.encode(str(path))
        write_session(d / "a.jsonl", [{"type": "user", "cwd": cwd}, command("jy-a")])
        return d

    def test_encode(self):
        self.assertEqual(history.encode("/Users/a/my.proj"), "-Users-a-my-proj")

    def test_worktrees_siblings_and_removed_worktrees(self):
        wt = self.tmp / "my.proj-feature"
        git(self.repo, "worktree", "add", "-q", str(wt), "-b", "feature")
        other = self.tmp / "my.proj-old"
        other.mkdir()
        git(other, "init", "-q")

        self.session_dir(self.repo, str(self.repo))
        self.session_dir(wt, str(wt))
        self.session_dir(other, str(other))
        gone = self.tmp / "my.proj-removed"
        self.session_dir(gone, str(gone))
        self.session_dir(self.tmp / "my.project", str(self.tmp / "nowhere"))

        dirs = {d.name: v for d, v in history.session_dirs(str(self.repo), self.projects)}
        self.assertEqual(
            dirs,
            {
                history.encode(str(self.repo)): True,
                history.encode(str(wt)): True,
                history.encode(str(gone)): False,
            },
        )

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False
        )

    def test_cli_json_and_exit_codes(self):
        self.session_dir(self.repo, str(self.repo))
        base = [
            "get",
            "invocations",
            "--project",
            str(self.repo),
            "--projects-dir",
            str(self.projects),
        ]

        r = self.run_cli(*base, "-o", "json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([x["skill"] for x in json.loads(r.stdout)], ["jy-a"])
        self.assertIn("jy-a", self.run_cli(*base).stdout)

        empty = ["--projects-dir", str(self.tmp / "none")]
        self.assertEqual(
            self.run_cli("get", "invocations", "--project", str(self.repo), *empty).returncode, 1
        )
        self.assertEqual(self.run_cli("get", "sessions").returncode, 2)
        self.assertEqual(self.run_cli(*base, "--after", "-1").returncode, 2)


if __name__ == "__main__":
    unittest.main()
