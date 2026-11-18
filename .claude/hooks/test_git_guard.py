#!/usr/bin/env python3
"""Tests for git_guard.py. Run: python3 .claude/hooks/test_git_guard.py"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
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
        self.assertFalse(self.denied("gh pr merge 3 --squash", self.main))


if __name__ == "__main__":
    unittest.main()
