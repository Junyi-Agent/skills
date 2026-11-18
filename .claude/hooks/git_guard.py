#!/usr/bin/env python3
"""PreToolUse hook (Bash): enforce the worktree workflow from CLAUDE.md.

Denies switching branches in place, committing on main, pushing to main, skipping the
git hooks (--no-verify, SKIP=, core.hooksPath), and merging a PR whose CI checks have not
all passed (`gh pr merge`, or the merge endpoint through `gh api`). The branch is read from the directory each git command actually runs in:
`cd <dir>` earlier in the same command and `git -C <dir>` are both followed.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys

PROTECTED = "main"
SEPARATORS = {";", "&&", "||", "|", "&", "\n"}
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

SWITCH_MSG = (
    "Do not switch branches in this directory. Create a worktree outside the repo: "
    "git worktree add ../<repo>-<feature> -b <feature> origin/main (see CLAUDE.md)."
)
COMMIT_MSG = (
    "Do not commit on main. Work in a worktree on a feature branch and open a PR (see CLAUDE.md)."
)
PUSH_MSG = "Do not push to main. Push a feature branch and open a PR (see CLAUDE.md)."
BYPASS_MSG = (
    "Do not skip the git hooks (--no-verify, SKIP=, core.hooksPath): pre-commit and the "
    "pre-push review gate must run. Fix what they report instead (see CLAUDE.md)."
)
MERGE_API_MSG = "Merge PRs with `gh pr merge` so the CI check gate applies (see CLAUDE.md)."
# `gh pr merge` options that take a value; everything else non-dash is the PR selector.
GH_VALUE_OPTS = {
    "-R", "--repo", "-t", "--subject", "-b", "--body", "-F", "--body-file",
    "-A", "--author-email", "--match-head-commit",
}  # fmt: skip


def segments(command: str) -> list[list[str]]:
    lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=";&|")
    lexer.whitespace_split = True
    out: list[list[str]] = [[]]
    for tok in lexer:
        if tok in SEPARATORS:
            out.append([])
        else:
            out[-1].append(tok)
    return [s for s in out if s]


def resolve(base: str, path: str) -> str:
    return os.path.normpath(os.path.join(base, os.path.expanduser(path)))


def clean_env() -> dict[str, str]:
    """Environment without GIT_* vars, which git sets while running its own hooks."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def current_branch(cwd: str) -> str:
    try:
        r = subprocess.run(
            ["git", "-C", cwd, "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=5,
            env=clean_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout.strip()


def parse_git(args: list[str], cwd: str) -> tuple[str, str, list[str]]:
    """Return (directory, subcommand, subcommand args) for `git <args>`."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        opt = args[i]
        if opt == "-C" and i + 1 < len(args):
            cwd = resolve(cwd, args[i + 1])
            i += 2
        elif opt in ("-c", "--git-dir", "--work-tree", "--namespace") and i + 1 < len(args):
            i += 2
        else:
            i += 1
    if i >= len(args):
        return cwd, "", []
    return cwd, args[i], args[i + 1 :]


def pushes_to_protected(refspecs: list[str]) -> bool:
    for spec in refspecs:
        dst = spec.lstrip("+").split(":")[-1]
        if dst in (PROTECTED, f"refs/heads/{PROTECTED}"):
            return True
    return False


def pr_checks(args: list[str], cwd: str) -> tuple[int, str]:
    """Run `gh pr checks <args>`; exit 0 means every check passed."""
    try:
        r = subprocess.run(
            ["gh", "pr", "checks", *args],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=cwd,
            env=clean_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, f"could not run gh pr checks: {e}"
    return r.returncode, (r.stdout + r.stderr).strip()


def check_pr_merge(rest: list[str], cwd: str) -> str | None:
    selector, repo, i = [], [], 0
    while i < len(rest):
        a = rest[i]
        if a in GH_VALUE_OPTS and i + 1 < len(rest):
            if a in ("-R", "--repo"):
                repo = [a, rest[i + 1]]
            i += 2
            continue
        if a.startswith("--repo="):
            repo = [a]
        elif not a.startswith("-"):
            selector = [a]
        i += 1
    code, output = pr_checks(selector + repo, cwd)
    if code == 0:
        return None
    return (
        "Do not merge: the PR's CI checks have not all passed (gh pr checks exit "
        f"{code}). Wait for CI or fix it, then merge.\n{output[-1500:]}"
    )


def check(command: str, cwd: str) -> str | None:
    try:
        segs = segments(command)
    except ValueError:  # unbalanced quotes: let the shell report it
        return None
    for seg in segs:
        skip_env = False
        while seg and ASSIGNMENT.match(seg[0]):
            skip_env |= seg[0].startswith("SKIP=")
            seg = seg[1:]
        if not seg:
            continue
        if seg[0] == "cd":
            cwd = resolve(cwd, seg[1]) if len(seg) > 1 else os.path.expanduser("~")
            continue
        if seg[0] == "gh" and seg[1:3] == ["pr", "merge"]:
            reason = check_pr_merge(seg[3:], cwd)
            if reason:
                return reason
            continue
        if seg[0] == "gh" and len(seg) > 1 and seg[1] == "api":
            if any(re.search(r"/pulls/[^/\s]+/merge\b", a) for a in seg[2:]):
                return MERGE_API_MSG
            continue
        if seg[0] != "git":
            continue

        git_cwd, sub, rest = parse_git(seg[1:], cwd)
        opts = seg[1 : len(seg) - len(rest)]  # global options before the subcommand
        if any("hookspath" in a.lower() for a in opts) or (
            sub == "config" and any("hookspath" in a.lower() for a in rest)
        ):
            return BYPASS_MSG
        if sub in ("commit", "push", "merge") and (skip_env or "--no-verify" in rest):
            return BYPASS_MSG
        if sub == "commit" and any(re.fullmatch(r"-[a-zA-Z]*n[a-zA-Z]*", a) for a in rest):
            return BYPASS_MSG
        if sub == "switch":
            return SWITCH_MSG
        if sub == "checkout" and rest and rest[0] not in ("--", "."):
            return SWITCH_MSG
        if sub == "commit" and current_branch(git_cwd) == PROTECTED:
            return COMMIT_MSG
        if sub == "push":
            positional = [a for a in rest if not a.startswith("-")]
            refspecs = positional[1:]
            if pushes_to_protected(refspecs):
                return PUSH_MSG
            if not refspecs and current_branch(git_cwd) == PROTECTED:
                return PUSH_MSG
    return None


def main() -> None:
    data = json.load(sys.stdin)
    command = data.get("tool_input", {}).get("command", "")
    reason = check(command, data.get("cwd") or os.getcwd())
    if reason:
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            },
            sys.stdout,
        )


if __name__ == "__main__":
    main()
