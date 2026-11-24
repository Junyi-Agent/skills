#!/usr/bin/env python3
"""Tests for git_guard.py. Run: python3 .claude/hooks/test_git_guard.py"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
        for cmd in (
            "git merge --ff-only origin/main",
            "git merge --abort",
            "git pull",
            "git merge --no-commit feature",
            "git merge --squash feature",
            "git cherry-pick -n abc",
            "git revert --no-commit abc",
            "git am --show-current-patch=diff",
        ):
            self.assertFalse(self.denied(cmd, self.main), cmd)
        # --skip goes on with the rest of the sequence, which creates commits
        for cmd in ("git cherry-pick --skip", "git am --skip", "git revert --skip"):
            self.assertTrue(self.denied(cmd, self.main), cmd)
        # -n is --no-stat for merge, which still commits
        self.assertTrue(self.denied("git merge -n feature", self.main))

    def test_heredoc(self):
        text = "cat > notes.md <<'EOF'\nDon't forget\nEOF\n"
        self.assertTrue(self.denied(text + "git commit -am x", self.main))
        self.assertTrue(self.denied(f"{text}git switch main", self.feat))
        self.assertTrue(self.denied("git commit -F - <<EOF\nDon't\nEOF", self.main))
        self.assertTrue(self.denied("cat <<-EOF\n\tit's\n\tEOF\ngit commit -m x", self.main))
        self.assertFalse(self.denied(text + "git commit -am x", self.feat))
        self.assertFalse(self.denied(text + "git status", self.main))
        # the body of a heredoc fed to a shell is a script
        self.assertTrue(self.denied("bash <<'EOF'\ngit push origin main\nEOF", self.feat))
        # unparseable and mentions git: denied; unparseable without git: left to the shell
        self.assertTrue(self.denied("echo it's && git commit -m x", self.feat))
        self.assertFalse(self.denied("echo it's", self.feat))

    def test_heredoc_data_is_not_a_script(self):
        for cmd in (
            "cat > notes.md <<'EOF'\ngit switch main\nEOF",
            "gh pr create --title t --body-file - <<'EOF'\nRun:\ngit checkout -b foo\nEOF",
            "git commit -F - <<'EOF'\nDocs\n\ngit push origin main is blocked\nEOF",
            "tee x.md <<EOF >/dev/null\ngit commit -m x\nEOF",
        ):
            self.assertFalse(self.denied(cmd, self.feat), cmd)

    def test_heredoc_fed_to_shell_runs_in_its_context(self):
        body = "<<'EOF'\ngit commit -m x\nEOF"
        self.assertTrue(self.denied(f"cd {self.main} && bash {body}", self.feat))
        self.assertFalse(self.denied(f"cd {self.feat} && bash {body}", self.main))
        self.assertTrue(self.denied(f"bash -s {body}", self.main))
        text = "\ngit commit -m x\nEOF"
        self.assertTrue(self.denied(f"cat <<'EOF' | sh{text}", self.main))
        self.assertTrue(self.denied(f"cat <<'EOF' |& bash -e{text}", self.main))
        self.assertFalse(self.denied(f"cat <<'EOF' || bash{text}", self.main))  # not a pipe
        self.assertFalse(self.denied(f"cat <<'EOF' | grep x{text}", self.main))
        self.assertTrue(
            self.denied("SKIP=review-gate bash <<'EOF'\ngit push origin feature\nEOF", self.feat)
        )
        # a shell-fed body is a script: unparseable with git in it is denied
        self.assertTrue(self.denied("bash <<'EOF'\ngit commit -m x\necho it's\nEOF", self.main))

    def test_comments(self):
        for cmd in (
            "echo hi # note\ngit commit -m x",
            "# step 1\ngit commit -m x",
            "echo a#b\ngit commit -m x",
            "echo a#b; git commit -m x",
            "curl -s http://x/#frag\ngit commit -m x",
            "echo $# ; git commit -m x",
            "bash <<'EOF'\n# don't\ngit commit -m x\nEOF",
        ):
            self.assertTrue(self.denied(cmd, self.main), cmd)
        # on one line, everything after an unquoted `#` word is a comment, as in the shell
        self.assertFalse(self.denied("echo hi # git commit -m x", self.main))
        self.assertTrue(self.denied("git commit -m 'fix #12'", self.main))

    def test_agent_style_commands(self):
        """Multi-line commands in the shape agents write: comments, URLs, heredocs."""
        main, feat = self.main, self.feat
        deny_on_main = [
            "# Check the state first\ngit status\n\n# Then commit\ngit add -A && git commit -m x",
            f"cd {main}\n# open the docs: https://example.invalid/guide#setup\ngit commit -m x",
            "git commit -q -m \"$(cat <<'EOF'\nFix the parser\n\nDon't drop lines\nEOF\n)\"",
            "git add a.py && git commit -q -F - <<'EOF'\nAdd thing\n\nIt's done.\nEOF",
            "for f in a b; do echo $f; done  # loop\ngit commit -m x",
            "git add -A && \\\n  git commit -q \\\n    -m 'Fix the parser' \\\n    -m 'Body'",
            "git \\\n  -c user.name=x \\\n  commit -m x",
        ]
        allow_on_feat = [
            "# Check the state first\ngit status\n\n# Then commit\ngit add -A && git commit -m x",
            "gh pr create --base main --title t --body-file - <<'EOF'\n## Summary\n"
            "- Don't run `git switch main` in place.\ngit checkout -b example\nEOF",
            "cat > notes.md <<'EOF'\n# Notes\ngit push origin main  # blocked by the hook\nEOF",
            "git push -u origin feature 2>&1 | tail -30",
            "git push -u origin feature \\\n  2>&1 | tail -30",
            "gh pr create --base main --title t \\\n  --body-file - <<'EOF'\nDon't merge yet\nEOF",
        ]
        for cmd in deny_on_main:
            self.assertTrue(self.denied(cmd, main), cmd)
        for cmd in allow_on_feat:
            self.assertFalse(self.denied(cmd, feat), cmd)

    def test_line_continuation(self):
        """`\\` + newline joins the lines, as in the shell; it does not end the command."""
        self.assertTrue(self.denied("git \\\n  commit -m x", self.main))
        self.assertTrue(self.denied('git commit -m "a \\\n b"', self.main))
        for cmd in (
            "git commit -m x \\\n  --no-verify",
            "git push origin \\\n  feature:main",
            "git push -u origin feature \\\n  --no-verify",
            f"git -C {self.main} \\\n  commit -m x",
        ):
            self.assertTrue(self.denied(cmd, self.feat), cmd)
        self.assertFalse(self.denied("git checkout \\\n  -- a.py", self.feat))
        self.assertFalse(self.denied("git commit -m x \\\n  -m y", self.feat))
        # inside single quotes a backslash is literal and the newline stays
        self.assertFalse(self.denied("echo 'a \\\n git commit -m x'", self.main))

    def test_shell_reading_stdin(self):
        """A shell with no script argument (or with -s) runs what comes on stdin."""
        body = "<<'EOF'\ngit commit -m x\nEOF"
        for shell in (
            "bash -o pipefail",
            "bash -euo pipefail",
            "bash -O extglob",
            "bash +o posix",
            "bash --norc --noprofile",
            "bash --rcfile /dev/null",
            "bash -s arg1",
            "bash -",
            "bash -- ",
            "zsh -o errexit",
        ):
            self.assertTrue(self.denied(f"{shell} {body}", self.main), shell)
        self.assertTrue(
            self.denied("cat <<'EOF' | bash -o pipefail\ngit commit -m x\nEOF", self.main)
        )
        for shell in ("bash script.sh", "bash -o pipefail script.sh", "bash -- script.sh"):
            self.assertFalse(self.denied(f"{shell} {body}", self.main), shell)

    def test_here_string_fed_to_shell(self):
        for cmd in (
            "bash <<< 'git commit -m x'",
            'bash <<< "git commit -m x"',
            "sh -e <<<'git commit -m x'",
            "bash <<< 'git commit -m x' <<'EOF'\nignored\nEOF",
        ):
            self.assertTrue(self.denied(cmd, self.main), cmd)
        self.assertTrue(self.denied("bash <<< 'git push origin main'", self.feat))
        for cmd in ("cat <<< 'git commit -m x'", 'grep x <<< "git commit -m x"'):
            self.assertFalse(self.denied(cmd, self.main), cmd)

    def test_echo_piped_to_shell(self):
        for cmd in (
            "echo 'git commit -m x' | bash",
            "echo -n git commit -m x | sh",
            "printf '%s\\n' 'git commit -m x' | bash",
            "printf 'git status\\ngit commit -m x\\n' | bash -e",
        ):
            self.assertTrue(self.denied(cmd, self.main), cmd)
        # `tee` and `cat` pass their stdin on
        for cmd in (
            "cat <<'EOF' | tee log.txt | bash\ngit commit -m x\nEOF",
            "echo 'git commit -m x' | cat | sh",
        ):
            self.assertTrue(self.denied(cmd, self.main), cmd)
        for cmd in (
            "echo 'git commit -m x' | grep git",
            "echo 'git status' | bash",
            "echo 'git commit -m x' | cat notes.md | bash",
        ):
            self.assertFalse(self.denied(cmd, self.main), cmd)

    def test_env_options_like_getopt(self):
        for cmd in (
            "env -iS'git push origin main'",
            "env -vS 'git push origin main'",
            "env -iS 'git push origin main'",
            "env -P /usr/bin git push origin main",
            "env -P/usr/bin git push origin main",
            "env -uFOO git push origin main",
            "env --unset FOO git push origin main",
            "env --unset=FOO git push origin main",
            "env -i -- git push origin main",
            "env - git push origin main",
            "env -S '-i git push origin main'",
            f"env -iC {self.main} git commit -m x",
            f"env -iC{self.main} git commit -m x",
        ):
            self.assertTrue(self.denied(cmd, self.feat), cmd)
        self.assertFalse(self.denied(f"env -iC {self.feat} git commit -m x", self.main))
        self.assertFalse(self.denied("env -vS 'git push origin feature'", self.feat))

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
            "SKIP=review-gate bash -c 'git push'",
            "env SKIP=review-gate sh -c 'git push -u origin feature'",
            "time -p git push origin main",
            "nohup -- git push origin main",
            "command -p git push origin main",
            f"env -C {self.main} git commit -m x",
            f"env --chdir={self.main} git commit -m x",
            f"pushd {self.main} && git commit -m x",
            "env -S 'git push origin main'",
            "env '-Sgit push origin main'",
            "env --split-string='git push origin main'",
            "env -S 'SKIP=review-gate git push'",
        ):
            self.assertTrue(self.denied(cmd, self.feat), cmd)
        self.assertFalse(self.denied("env FOO=1 git push", self.feat))
        self.assertFalse(self.denied("FOO=1 bash -c 'git push'", self.feat))
        self.assertFalse(self.denied("bash -c 'git status'", self.main))
        self.assertFalse(self.denied(f"env -C {self.feat} git commit -m x", self.main))
        self.assertFalse(self.denied(f"pushd {self.main} && popd && git commit -m x", self.feat))

    def test_aliases(self):
        self.assertTrue(self.denied("git -c alias.ci=commit ci -m x", self.main))
        self.assertTrue(self.denied("git -c alias.p='push origin main' p", self.feat))
        self.assertTrue(self.denied("git -c 'alias.p=!git push origin main' p", self.feat))
        self.assertTrue(self.denied("git -c alias.ci='commit --no-verify' ci -m x", self.feat))
        self.assertFalse(self.denied("git -c alias.ci=commit ci -m x", self.feat))
        git(self.feat, "config", "alias.sw", "switch")
        try:
            self.assertTrue(self.denied("git sw main", self.feat))
        finally:
            git(self.feat, "config", "--unset", "alias.sw")

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
        def strip(cmd: str) -> str:
            return git_guard.strip_redirections(cmd)[0]

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
            "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/x git push",
            "GIT_CONFIG_PARAMETERS=\"'core.hookspath'='/x'\" git commit -m x",
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


class MaintainScriptTest(unittest.TestCase):
    """maintain.py is found in skills/ (this repo) or .claude/skills/ (a repo that copies
    the hook and links the skills in)."""

    def test_locations(self):
        rel = Path("jy-repo-maintain") / "scripts" / "maintain.py"
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.assertEqual(git_guard.maintain_script(root), root / "skills" / rel)
            linked = root / ".claude" / "skills" / rel
            linked.parent.mkdir(parents=True)
            linked.write_text("")
            self.assertEqual(git_guard.maintain_script(root), linked)
            own = root / "skills" / rel
            own.parent.mkdir(parents=True)
            own.write_text("")
            self.assertEqual(git_guard.maintain_script(root), own)

    def test_this_repo(self):
        self.assertTrue(git_guard.maintain_script().is_file())


class LoopModeTest(unittest.TestCase):
    """JY_MAINTAIN_LOOP=1: the rules for the jy-repo-maintain loop (docs/adr/0006)."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"JY_MAINTAIN_LOOP": "1"})
        self.env.start()
        self.checks = mock.patch.object(git_guard, "pr_checks", return_value=(0, ""))
        self.checks.start()
        self.policy = mock.patch.object(git_guard, "maintain_policy", return_value=(0, ""))
        self.policy_mock = self.policy.start()

    def tearDown(self):
        mock.patch.stopall()

    def denied(self, cmd: str) -> bool:
        return check(cmd, "/") is not None

    def test_off_without_env(self):
        with mock.patch.dict(os.environ, {"JY_MAINTAIN_LOOP": ""}):
            self.assertFalse(self.denied("gh secret set X"))
            self.assertFalse(self.denied("gh issue edit 3 --add-label accepted"))

    def test_only_listed_gh_commands(self):
        for cmd in (
            "gh issue list --state open",
            "gh issue comment 3 --body-file c.md",
            "gh pr create --title t --body b --label loop",
            "gh pr checks 4 --watch",
            "gh label list",
            "gh search issues foo",
        ):
            self.assertFalse(self.denied(cmd), cmd)
        for cmd in (
            "gh secret set X",
            "gh repo edit --visibility private",
            "gh label edit accepted --name x",
            "gh workflow disable ci",
            "gh release create v9.9.9",
            "GH_TOKEN=x gh repo delete o/r --yes",
            "gh",
        ):
            self.assertTrue(self.denied(cmd), cmd)

    def test_api_is_read_only(self):
        self.assertFalse(self.denied("gh api --paginate repos/o/r/issues?state=open"))
        self.assertFalse(self.denied("gh api -X GET search/issues -f q=x"))
        for cmd in (
            "gh api repos/o/r/issues/3/labels -f labels[]=accepted",
            "gh api -XPATCH repos/o/r/issues/3",
            "gh api --method=DELETE repos/o/r/labels/x",
            "gh api repos/o/r/issues --input body.json",
            "gh api graphql -F query=@q.graphql",
        ):
            self.assertTrue(self.denied(cmd), cmd)

    def test_human_labels(self):
        for cmd in (
            "gh issue edit 3 --add-label accepted",
            "gh issue edit 3 --add-label bug,approved",
            "gh issue edit 3 --add-label=approved",
            "gh issue create --title t --body b -l accepted",
            "gh pr edit 4 --add-label approved",
        ):
            self.assertTrue(self.denied(cmd), cmd)
        self.assertFalse(self.denied("gh issue edit 3 --add-label bug,needs-decision"))
        self.assertFalse(self.denied("gh issue edit 3 --remove-label approved"))
        self.assertFalse(self.denied("gh issue list -l accepted"))

    def test_merge_runs_policy_with_checkout_config(self):
        self.assertFalse(self.denied("gh pr merge -R o/r 7 --squash"))
        args = self.policy_mock.call_args.args[0]
        self.assertEqual(args[:3], ["7", "--repo", "o/r"])
        self.assertEqual(args[3:], ["--config", str(git_guard.MAINTAIN_CONFIG)])

        self.policy_mock.return_value = (1, "改到了护栏文件")
        self.assertIn("护栏", check("gh pr merge 7 --squash", "/"))
        self.assertTrue(self.denied("gh pr merge --squash"))  # PR must be named


if __name__ == "__main__":
    unittest.main()
