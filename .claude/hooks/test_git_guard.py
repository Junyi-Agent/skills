#!/usr/bin/env python3
"""Tests for git_guard.py. Run: python3 .claude/hooks/test_git_guard.py"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import git_guard
from git_guard import check, clean_env


def make_repo(root: Path, name: str, branch: str) -> Path:
    path = root / name
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True, env=clean_env())
    return path


class GitGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.main = str(make_repo(root, "repo", "main"))
        cls.feat = str(make_repo(root, "repo-feature", "feature"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def denied(self, cmd: str, cwd: str) -> bool:
        return check(cmd, cwd) is not None

    def test_branch_switching(self):
        for cmd in ("git switch main", "git checkout main", "git checkout -b foo"):
            self.assertTrue(self.denied(cmd, self.feat), cmd)
        for cmd in ("git checkout -- a.py", "git checkout .", "git worktree add ../x -b x"):
            self.assertFalse(self.denied(cmd, self.feat), cmd)

    def test_commit_uses_session_cwd(self):
        self.assertTrue(self.denied("git commit -m x", self.main))
        self.assertFalse(self.denied("git commit -m x", self.feat))

    def test_commit_follows_cd(self):
        self.assertFalse(self.denied(f"cd {self.feat} && git add . && git commit -m x", self.main))
        self.assertTrue(self.denied(f"cd {self.main} && git commit -m x", self.feat))
        self.assertTrue(self.denied("cd ../repo && git commit -m x", self.feat))

    def test_commit_follows_dash_c(self):
        self.assertFalse(self.denied(f"git -C {self.feat} commit -m x", self.main))
        self.assertTrue(self.denied(f"git -C {self.main} commit -m x", self.feat))
        self.assertTrue(self.denied(f"git -c user.name=x -C {self.main} commit -m x", self.feat))

    def test_push(self):
        self.assertTrue(self.denied("git push", self.main))
        self.assertTrue(self.denied("git push origin main", self.feat))
        self.assertTrue(self.denied("git push origin HEAD:main", self.feat))
        self.assertTrue(self.denied(f"git -C {self.main} push", self.feat))
        self.assertFalse(self.denied("git push -u origin feature", self.main))
        self.assertFalse(self.denied("git push", self.feat))
        self.assertFalse(self.denied(f"cd {self.feat} && git push", self.main))

    def test_non_git_and_quoted_text(self):
        self.assertFalse(self.denied("git status", self.main))
        self.assertFalse(self.denied("echo 'git commit -m x'", self.main))

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
        finally:
            git_guard.pr_checks = orig

    def test_merge_via_api(self):
        self.assertTrue(self.denied("gh api -X PUT repos/o/r/pulls/3/merge", self.main))
        self.assertFalse(self.denied("gh api repos/o/r/pulls/3", self.main))


if __name__ == "__main__":
    unittest.main()
