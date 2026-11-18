#!/usr/bin/env python3
"""Tests for git_guard.py. Run: python3 .claude/hooks/test_git_guard.py"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import git_guard
from git_guard import check, clean_env

HOOK = Path(__file__).resolve().parent / "git_guard.py"


def make_repo(root: Path, name: str, branch: str) -> Path:
    path = root / name
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True, env=clean_env())
    return path


def git(path: str, *args: str) -> None:
    ident = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run(["git", "-C", path, *ident, *args], check=True, env=clean_env())


def run_hook(stdin: str) -> str:
    r = subprocess.run(
        [sys.executable, str(HOOK)], input=stdin, capture_output=True, text=True, check=False
    )
    return r.stdout


class GitGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.main = str(make_repo(root, "repo", "main"))
        cls.feat = str(make_repo(root, "repo-feature", "feature"))
        # feature repo with history: a tracked file and a second branch
        (Path(cls.feat) / "a.py").write_text("x = 1\n")
        git(cls.feat, "add", "a.py")
        git(cls.feat, "commit", "-qm", "init")
        git(cls.feat, "branch", "other")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def denied(self, cmd: str, cwd: str) -> bool:
        return check(cmd, cwd) is not None

    def test_branch_switching(self):
        for cmd in (
            "git switch main",
            "git checkout main",
            "git checkout other",
            "git checkout -b foo",
            "git checkout -",
            "git checkout --track origin/x",
        ):
            self.assertTrue(self.denied(cmd, self.feat), cmd)
        for cmd in (
            "git checkout -- a.py",
            "git checkout .",
            "git checkout a.py",
            "git checkout HEAD -- a.py",
            "git checkout other a.py",
            "git checkout -p a.py",
            "git worktree add ../x -b x",
        ):
            self.assertFalse(self.denied(cmd, self.feat), cmd)

    def test_commit_uses_session_cwd(self):
        self.assertTrue(self.denied("git commit -m x", self.main))
        self.assertFalse(self.denied("git commit -m x", self.feat))

    def test_commit_follows_cd(self):
        self.assertFalse(self.denied(f"cd {self.feat} && git add . && git commit -m x", self.main))
        self.assertTrue(self.denied(f"cd {self.main} && git commit -m x", self.feat))
        self.assertTrue(self.denied("cd ../repo && git commit -m x", self.feat))

    def test_other_commit_creating_commands_on_main(self):
        for cmd in (
            "git merge feature",
            "git cherry-pick abc",
            "git revert abc",
            "git am x.patch",
        ):
            self.assertTrue(self.denied(cmd, self.main), cmd)
            self.assertFalse(self.denied(cmd, self.feat), cmd)
        for cmd in ("git merge --ff-only origin/main", "git merge --abort", "git pull"):
            self.assertFalse(self.denied(cmd, self.main), cmd)

    def test_subshell_scopes_cd(self):
        self.assertTrue(self.denied(f"(cd {self.main} && git commit -m x)", self.feat))
        self.assertTrue(self.denied(f"(cd {self.main}&&git commit -m x)", self.feat))
        self.assertFalse(self.denied(f"(cd {self.main}) && git commit -m x", self.feat))
        self.assertTrue(self.denied(f"(cd {self.feat}) && git commit -m x", self.main))
        self.assertTrue(self.denied("echo $(git push origin main)", self.feat))

    def test_wrappers(self):
        for cmd in (
            "env SKIP=review-gate git push",
            "env -u FOO SKIP=review-gate git push",
            "bash -c 'git push origin main'",
            "sh -lc 'git commit --no-verify -m x'",
            f"bash -c 'cd {self.main} && git commit -m x'",
            "command git push origin main",
            "nohup git push origin main",
            "env git push origin main",
        ):
            self.assertTrue(self.denied(cmd, self.feat), cmd)
        self.assertFalse(self.denied("env FOO=1 git push", self.feat))
        self.assertFalse(self.denied("bash -c 'git status'", self.main))

    def test_commit_follows_dash_c(self):
        self.assertFalse(self.denied(f"git -C {self.feat} commit -m x", self.main))
        self.assertTrue(self.denied(f"git -C {self.main} commit -m x", self.feat))
        self.assertTrue(self.denied(f"git -c user.name=x -C {self.main} commit -m x", self.feat))

    def test_push(self):
        self.assertTrue(self.denied("git push", self.main))
        self.assertTrue(self.denied("git push origin main", self.feat))
        self.assertTrue(self.denied("git push origin HEAD:main", self.feat))
        self.assertTrue(self.denied(f"git -C {self.main} push", self.feat))
        for cmd in ("git push origin HEAD", "git push origin @", "git push -u origin +HEAD"):
            self.assertTrue(self.denied(cmd, self.main), cmd)
            self.assertFalse(self.denied(cmd, self.feat), cmd)
        self.assertFalse(self.denied("git push -u origin feature", self.main))
        self.assertFalse(self.denied("git push", self.feat))
        self.assertFalse(self.denied(f"cd {self.feat} && git push", self.main))

    def test_non_git_and_quoted_text(self):
        self.assertFalse(self.denied("git status", self.main))
        self.assertFalse(self.denied("echo 'git commit -m x'", self.main))

    def test_redirections(self):
        strip = git_guard.strip_redirections
        self.assertEqual(strip("a 2>&1 | b").split(), ["a", "|", "b"])
        self.assertEqual(strip("a >out <in b").split(), ["a", "b"])
        self.assertEqual(strip("a x2>f").split(), ["a", "x2"])  # not an fd: stdout redirect
        self.assertEqual(strip("a '2>x' \"3>y\"").split(), ["a", "'2>x'", '"3>y"'])
        self.assertEqual(strip("a > 'my log' b").split(), ["a", "b"])
        self.assertEqual(strip("a && b").split(), ["a", "&&", "b"])
        self.assertTrue(self.denied("git push origin main 2>&1", self.feat))
        self.assertTrue(self.denied("git push 2>&1 | tail", self.main))
        self.assertTrue(self.denied("git commit -m x >/dev/null", self.main))

    def test_hook_bypass(self):
        for cmd in (
            "git push --no-verify",
            "SKIP=review-gate git push -u origin feature",
            "git commit --no-verify -m x",
            "git commit -nm x",
            "git -c core.hooksPath=/dev/null push",
            "git config core.hooksPath /tmp/none",
        ):
            self.assertTrue(self.denied(cmd, self.feat), cmd)
        self.assertFalse(self.denied("git push -n origin feature", self.feat))
        self.assertFalse(self.denied("git commit -am x", self.feat))
        self.assertFalse(self.denied("git grep hooksPath", self.feat))

    def test_pr_merge_requires_green_checks(self):
        calls = []

        def fake(code):
            def run(args, cwd):
                calls.append(args)
                return code, "out"

            return run

        orig = git_guard.pr_checks
        try:
            git_guard.pr_checks = fake(0)
            self.assertFalse(self.denied("gh pr merge 3 --squash", self.main))
            self.assertFalse(self.denied("gh pr merge -R o/r 3 -t title --squash", self.main))
            self.assertEqual(calls[-1], ["3", "-R", "o/r"])
            git_guard.pr_checks = fake(8)  # pending
            self.assertTrue(self.denied("gh pr merge 3 --squash", self.main))
            git_guard.pr_checks = fake(1)  # failed / no checks
            self.assertTrue(self.denied("gh pr merge --squash --delete-branch", self.main))
            self.assertEqual(calls[-1], [])
            git_guard.pr_checks = fake(0)
            for cmd in (
                "gh pr merge 6 --squash 2>&1 | tail -5",
                "gh pr merge 6 >/dev/null 2>&1",
                "gh pr merge 6 &> log",
                "gh pr merge 2>err.txt 6",
            ):
                self.assertFalse(self.denied(cmd, self.main), cmd)
                self.assertEqual(calls[-1], ["6"], cmd)
            self.assertFalse(self.denied("gh pr merge 7 --squash > out", self.main))
            self.assertEqual(calls[-1], ["7"])
        finally:
            git_guard.pr_checks = orig

    def test_bad_input_fails_closed(self):
        for stdin in ("not json", "", "[]", '{"tool_input": null}', '{"tool_input": {}}'):
            out = run_hook(stdin)
            self.assertIn('"deny"', out, stdin)
        ok = json.dumps({"tool_input": {"command": "git status"}, "cwd": self.feat})
        self.assertEqual(run_hook(ok), "")
        bad = json.dumps({"tool_input": {"command": "git switch x"}, "cwd": self.feat})
        self.assertIn('"deny"', run_hook(bad))

    def test_merge_via_api(self):
        self.assertTrue(self.denied("gh api -X PUT repos/o/r/pulls/3/merge", self.main))
        self.assertFalse(self.denied("gh api repos/o/r/pulls/3", self.main))


if __name__ == "__main__":
    unittest.main()
