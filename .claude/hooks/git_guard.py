#!/usr/bin/env python3
"""PreToolUse hook (Bash): enforce the worktree workflow from CLAUDE.md.

Denies switching branches in place, committing on main, and pushing to main.
The branch is read from the directory each git command actually runs in:
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


def check(command: str, cwd: str) -> str | None:
    try:
        segs = segments(command)
    except ValueError:  # unbalanced quotes: let the shell report it
        return None
    for seg in segs:
        while seg and ASSIGNMENT.match(seg[0]):
            seg = seg[1:]
        if not seg:
            continue
        if seg[0] == "cd":
            cwd = resolve(cwd, seg[1]) if len(seg) > 1 else os.path.expanduser("~")
            continue
        if seg[0] != "git":
            continue

        git_cwd, sub, rest = parse_git(seg[1:], cwd)
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
