#!/usr/bin/env python3
"""PreToolUse hook (Bash): enforce the worktree workflow from CLAUDE.md.

Denies switching branches in place, creating commits on main (commit, merge, cherry-pick,
revert, am), pushing to main, skipping the git hooks (--no-verify, SKIP=, core.hooksPath),
and merging a PR whose CI checks have not all passed (`gh pr merge`, or the merge endpoint
through `gh api`).

The branch is read from the directory each git command actually runs in: `cd <dir>` earlier
in the same command, `( ... )` subshells and `git -C <dir>` are all followed. Wrappers are
looked through: `env VAR=x`, `command`, `exec`, `nohup`, `time`, and `bash -c '...'`.
If the hook input cannot be read, or the check itself fails, the command is denied.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys

PROTECTED = "main"
PUNCT = ";&|()"
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

SWITCH_MSG = (
    "Do not switch branches in this directory. Create a worktree outside the repo: "
    "git worktree add ../<repo>-<feature> -b <feature> origin/main (see CLAUDE.md). "
    "To restore files, use `git checkout -- <path>` or `git restore <path>`."
)
COMMIT_MSG = (
    "Do not create commits on main (commit, merge, cherry-pick, revert, am). Work in a "
    "worktree on a feature branch and open a PR (see CLAUDE.md). To update main, use "
    "`git pull --ff-only` or `git merge --ff-only`."
)
PUSH_MSG = "Do not push to main. Push a feature branch and open a PR (see CLAUDE.md)."
BYPASS_MSG = (
    "Do not skip the git hooks (--no-verify, SKIP=, core.hooksPath): pre-commit and the "
    "pre-push review gate must run. Fix what they report instead (see CLAUDE.md)."
)
MERGE_API_MSG = "Merge PRs with `gh pr merge` so the CI check gate applies (see CLAUDE.md)."
# Commands that create commits on the current branch, and the options that make them not do so.
COMMIT_CMDS = {"commit", "merge", "cherry-pick", "revert", "am"}
NO_COMMIT_OPTS = {"--ff-only", "--abort", "--quit", "--skip"}
# `git checkout` options that create or switch to a branch.
SWITCH_OPTS = {"-b", "-B", "--orphan", "--detach", "-t", "--track"}
# Prefixes that run the rest of the words as a command.
WRAPPERS = {"command", "exec", "nohup", "time", "builtin"}
ENV_VALUE_OPTS = {"-u", "--unset", "-C", "--chdir"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
# `gh pr merge` options that take a value; everything else non-dash is the PR selector.
GH_VALUE_OPTS = {
    "-R", "--repo", "-t", "--subject", "-b", "--body", "-F", "--body-file",
    "-A", "--author-email", "--match-head-commit",
}  # fmt: skip


REDIRECT_OPS = ("&>>", "&>", "<<<", "<<-", "<<", ">>", ">&", ">|", "<&", "<>", ">", "<")
WORD_END = set(" \t\n;&|<>()")


def strip_redirections(command: str) -> str:
    """Drop shell redirections (`2>&1`, `> log`, `<<EOF`) outside quotes, with their targets.

    Without this, `gh pr merge 6 2>&1` would pass `2>` to `gh pr checks` as the PR selector.
    A leading fd number counts only when it is the whole unquoted word, as in the shell.
    """
    out: list[str] = []
    quote = ""
    word_start, word_quoted = 0, False
    i, n = 0, len(command)
    while i < n:
        c = command[i]
        if quote:
            out.append(c)
            if c == "\\" and quote == '"' and i + 1 < n:
                out.append(command[i + 1])
                i += 1
            elif c == quote:
                quote = ""
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            out.append(command[i : i + 2])
            word_quoted = True
            i += 2
            continue
        if c in "'\"":
            quote, word_quoted = c, True
            out.append(c)
            i += 1
            continue
        op = next((o for o in REDIRECT_OPS if command.startswith(o, i)), "")
        if op and not command.startswith("(", i + len(op)):  # `<(..)` is process substitution
            word = "".join(out[word_start:])
            if word.isdigit() and not word_quoted:
                del out[word_start:]  # fd number, e.g. the 2 in 2>&1
            i += len(op)
            while i < n and command[i] in " \t":
                i += 1
            target_quote = ""
            while i < n and (target_quote or command[i] not in WORD_END):
                ch = command[i]
                if target_quote:
                    target_quote = "" if ch == target_quote else target_quote
                elif ch in "'\"":
                    target_quote = ch
                elif ch == "\\":
                    i += 1
                i += 1
            out.append(" ")
            word_start, word_quoted = len(out), False
            continue
        out.append(c)
        i += 1
        if c in WORD_END:
            word_start, word_quoted = len(out), False
    return "".join(out)


def segments(command: str) -> list[list[str]]:
    """Split a command line into simple commands.

    `(` and `)` come back as one-word segments so the caller can scope `cd` to a subshell.
    """
    command = strip_redirections(command)
    lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=PUNCT)
    lexer.whitespace_split = True
    out: list[list[str]] = [[]]
    for tok in lexer:
        if not tok or any(c not in PUNCT for c in tok):
            out[-1].append(tok)
            continue
        for c in tok:  # a punctuation run such as `)&&` or `((`
            if c in "()":
                out += [[c], []]
            elif out[-1]:
                out.append([])
    return [s for s in out if s]


def unwrap(seg: list[str]) -> tuple[list[str], bool]:
    """Drop leading `VAR=x` words and wrappers; return the command and whether SKIP= was set."""
    skip_env = False
    while seg:
        if ASSIGNMENT.match(seg[0]):
            skip_env |= seg[0].startswith("SKIP=")
            seg = seg[1:]
        elif seg[0] == "env":
            seg = seg[1:]
            while seg and seg[0].startswith("-"):
                seg = seg[2:] if seg[0] in ENV_VALUE_OPTS else seg[1:]
        elif seg[0] in WRAPPERS:
            seg = seg[1:]
        else:
            break
    return seg, skip_env


def shell_script(seg: list[str]) -> str | None:
    """The script of `bash -c '<script>'` (also `sh`, `zsh`, `-lc`, `-ec` ...), else None."""
    if not seg or os.path.basename(seg[0]) not in SHELLS:
        return None
    for i, a in enumerate(seg[1:-1], 1):
        if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", a):
            return seg[i + 1]
    return None


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


def pushes_to_protected(refspecs: list[str], cwd: str) -> bool:
    for spec in refspecs:
        dst = spec.lstrip("+").split(":")[-1]
        if dst in ("HEAD", "@"):
            dst = current_branch(cwd)
        if dst in (PROTECTED, f"refs/heads/{PROTECTED}"):
            return True
    return False


def is_commit(cwd: str, rev: str) -> bool:
    try:
        r = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"],
            capture_output=True,
            timeout=5,
            env=clean_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def checkout_switches(rest: list[str], cwd: str) -> bool:
    """Whether `git checkout <rest>` switches branch rather than restoring files."""
    if any(a in SWITCH_OPTS or a.startswith(("--orphan=", "--track=")) for a in rest):
        return True
    if "--" in rest or any(a in ("-p", "--patch") for a in rest):
        return False
    positional = [a for a in rest if a == "-" or not a.startswith("-")]
    if len(positional) != 1:  # none: stays on the branch; two or more: <tree-ish> <paths>
        return False
    target = positional[0]
    if target == ".":
        return False
    return is_commit(cwd, target) or not os.path.exists(resolve(cwd, target))


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
    stack: list[str] = []
    for raw in segs:
        if raw == ["("]:
            stack.append(cwd)
            continue
        if raw == [")"]:
            cwd = stack.pop() if stack else cwd
            continue
        seg, skip_env = unwrap(raw)
        if not seg:
            continue
        script = shell_script(seg)
        if script is not None:
            reason = check(script, cwd)
            if reason:
                return reason
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
        if sub == "checkout" and checkout_switches(rest, git_cwd):
            return SWITCH_MSG
        if (
            sub in COMMIT_CMDS
            and not NO_COMMIT_OPTS.intersection(rest)
            and current_branch(git_cwd) == PROTECTED
        ):
            return COMMIT_MSG
        if sub == "push":
            positional = [a for a in rest if not a.startswith("-")]
            refspecs = positional[1:]
            if pushes_to_protected(refspecs, git_cwd):
                return PUSH_MSG
            if not refspecs and current_branch(git_cwd) == PROTECTED:
                return PUSH_MSG
    return None


def deny(reason: str) -> None:
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


def main() -> None:
    # Fail closed: a guard that crashes would otherwise let the command run.
    try:
        data = json.load(sys.stdin)
        command = data["tool_input"]["command"]
        if not isinstance(command, str):
            raise TypeError(f"command is {type(command).__name__}, not str")
        reason = check(command, data.get("cwd") or os.getcwd())
    except Exception as e:
        reason = (
            f"git_guard could not check this command ({type(e).__name__}: {e}), so it is "
            "denied. Fix .claude/hooks/git_guard.py or the hook input."
        )
    if reason:
        deny(reason)


if __name__ == "__main__":
    main()
