#!/usr/bin/env python3
"""PreToolUse hook (Bash): enforce the worktree workflow from CLAUDE.md.

Denies switching branches in place, creating commits on main (commit, merge, cherry-pick,
revert, am), pushing to main, skipping the git hooks (--no-verify, SKIP=, core.hooksPath,
including through GIT_CONFIG_* variables), and merging a PR whose CI checks have not all
passed (`gh pr merge`, or the merge endpoint through `gh api`).

The branch is read from the directory each git command actually runs in: `cd <dir>`,
`pushd`/`popd` earlier in the same command, `( ... )` subshells, `env -C <dir>` and
`git -C <dir>` are all followed. Wrappers are looked through: `env VAR=x`, `command`,
`exec`, `nohup`, `time`, `env -S` (with their options), and `bash -c '...'`, which also
inherits the `VAR=x` words in front of it. Git aliases are expanded. A heredoc body fed to
a shell (`bash <<EOF`, or `cat <<EOF | bash`) is checked as a script, in the directory
and environment of that shell; other heredoc bodies are data and are not checked. Shell
comments are skipped. If the hook input cannot be read, the check itself fails, or a
command (or shell-fed heredoc) that mentions git/gh cannot be parsed, it is denied.
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
GIT_WORD = re.compile(r"\b(git|gh)\b")

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
PARSE_MSG = (
    "git_guard could not parse this command (unbalanced quotes), and it mentions git/gh, so "
    "it is denied. Split it into simpler commands, or write long text to a file first."
)
# Commands that create commits on the current branch, and per command the options that
# make it not do so. `--skip` is not here: it goes on with the rest of the sequence.
NO_COMMIT_OPTS = {
    "commit": {"--dry-run"},
    "merge": {"--ff-only", "--abort", "--quit", "--no-commit", "--squash"},
    "cherry-pick": {"--abort", "--quit", "-n", "--no-commit"},
    "revert": {"--abort", "--quit", "-n", "--no-commit"},
    "am": {"--abort", "--quit", "--show-current-patch"},
}
# `git checkout` options that create or switch to a branch.
SWITCH_OPTS = {"-b", "-B", "--orphan", "--detach", "-t", "--track"}
# Prefixes that run the rest of the words as a command, and their options that take a value.
WRAPPERS = {"command", "exec", "nohup", "time", "builtin"}
WRAPPER_VALUE_OPTS = {"-a", "-f", "-o", "--format", "--output"}
ENV_VALUE_OPTS = {"-u", "--unset"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
# Git does not let an alias shadow a built-in command, so these are never looked up.
GIT_BUILTINS = {
    "add", "am", "apply", "archive", "bisect", "blame", "branch", "bundle", "cat-file",
    "check-ignore", "checkout", "cherry", "cherry-pick", "clean", "clone", "commit",
    "config", "count-objects", "describe", "diff", "fetch", "for-each-ref", "format-patch",
    "fsck", "gc", "grep", "hash-object", "help", "init", "log", "ls-files", "ls-remote",
    "ls-tree", "merge", "merge-base", "mv", "name-rev", "notes", "pull", "push",
    "range-diff", "rebase", "reflog", "remote", "repack", "replace", "reset", "restore",
    "rev-list", "rev-parse", "revert", "rm", "shortlog", "show", "show-ref",
    "sparse-checkout", "stash", "status", "submodule", "switch", "symbolic-ref", "tag",
    "update-index", "update-ref", "var", "verify-commit", "version", "worktree",
}  # fmt: skip
# `gh pr merge` options that take a value; everything else non-dash is the PR selector.
GH_VALUE_OPTS = {
    "-R", "--repo", "-t", "--subject", "-b", "--body", "-F", "--body-file",
    "-A", "--author-email", "--match-head-commit",
}  # fmt: skip


REDIRECT_OPS = ("&>>", "&>", "<<<", "<<-", "<<", ">>", ">&", ">|", "<&", "<>", ">", "<")
WORD_END = set(" \t\n;&|<>()")
# Word left in place of a heredoc: HEREDOC + index into the bodies list. NUL cannot occur
# in a real shell word, so the marker cannot be forged by the command text.
HEREDOC = "\x00heredoc:"


def strip_redirections(command: str) -> tuple[str, list[str]]:
    """Drop shell redirections (`2>&1`, `> log`, `<<EOF`) and comments outside quotes.

    Without this, `gh pr merge 6 2>&1` would pass `2>` to `gh pr checks` as the PR selector.
    A leading fd number counts only when it is the whole unquoted word, as in the shell.
    A `#` starts a comment only at the start of a word (`a#b` and `$#` are not comments).
    Heredoc bodies are cut out of the command and returned separately, so an apostrophe
    in them does not break parsing; each leaves a HEREDOC marker word in its command, so
    the caller knows which command reads it.
    """
    out: list[str] = []
    bodies: list[str] = []
    pending: list[tuple[str, bool]] = []  # heredoc delimiters waiting for the end of line
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
        if c == "#" and word_start == len(out):  # comment: skip to the end of the line
            j = command.find("\n", i)
            i = n if j < 0 else j
            continue
        op = next((o for o in REDIRECT_OPS if command.startswith(o, i)), "")
        if op and not command.startswith("(", i + len(op)):  # `<(..)` is process substitution
            word = "".join(out[word_start:])
            if word.isdigit() and not word_quoted:
                del out[word_start:]  # fd number, e.g. the 2 in 2>&1
            i += len(op)
            while i < n and command[i] in " \t":
                i += 1
            start = i
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
            if op in ("<<", "<<-"):
                delim = re.sub(r"[\"'\\]", "", command[start:i])
                out.append(f" {HEREDOC}{len(bodies) + len(pending)}")
                pending.append((delim, op == "<<-"))
            out.append(" ")
            word_start, word_quoted = len(out), False
            continue
        out.append(c)
        i += 1
        if c == "\n" and pending:
            for delim, strip_tabs in pending:
                lines = []
                while i < n:
                    j = command.find("\n", i)
                    j = n if j < 0 else j
                    line = command[i:j]
                    i = j + 1
                    if (line.lstrip("\t") if strip_tabs else line) == delim:
                        break
                    lines.append(line)
                bodies.append("\n".join(lines))
            pending.clear()
        if c in WORD_END:
            word_start, word_quoted = len(out), False
    return "".join(out), bodies


def segments(command: str) -> tuple[list[list[str]], list[str]]:
    """Split a command line into simple commands; also return its heredoc bodies.

    `(` and `)` come back as one-word segments so the caller can scope `cd` to a subshell,
    and a pipe `|` as `["|"]` so it can pass a heredoc on to the next command.
    """
    command, bodies = strip_redirections(command)
    lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=PUNCT)
    lexer.whitespace_split = True
    lexer.commenters = ""  # strip_redirections already dropped the comments
    out: list[list[str]] = [[]]
    for tok in lexer:
        if not tok or any(c not in PUNCT for c in tok):
            out[-1].append(tok)
            continue
        for j, c in enumerate(tok):  # a punctuation run such as `)&&`, `((` or `|&`
            if c in "()":
                out += [[c], []]
            elif c == "|" and "||" not in tok[max(j - 1, 0) : j + 2]:
                out += [["|"], []]
            elif out[-1]:
                out.append([])
    return [s for s in out if s], bodies


def env_flags(assignment: str) -> set[str]:
    """Hook-skipping effects of one `VAR=value` word: `skip` (SKIP=) or `hookspath`."""
    name, _, value = assignment.partition("=")
    flags = set()
    if name == "SKIP":
        flags.add("skip")
    if name.startswith("GIT_CONFIG") and "hookspath" in value.lower():
        flags.add("hookspath")
    return flags


def unwrap(seg: list[str], cwd: str) -> tuple[list[str], set[str], str]:
    """Drop leading `VAR=x` words and wrappers.

    Returns the command, the hook-skipping flags its environment sets (see env_flags),
    and the directory it runs in (`env -C <dir>` changes it for this command only).
    """
    flags: set[str] = set()
    while seg:
        word = seg[0]
        if ASSIGNMENT.match(word):
            flags |= env_flags(word)
            seg = seg[1:]
        elif word == "env":
            seg = seg[1:]
            while seg and seg[0].startswith("-"):
                opt = seg[0]
                if opt in ("-C", "--chdir") and len(seg) > 1:
                    cwd = resolve(cwd, seg[1])
                    seg = seg[2:]
                elif opt.startswith("--chdir="):
                    cwd = resolve(cwd, opt.split("=", 1)[1])
                    seg = seg[1:]
                elif opt in ("-S", "--split-string") and len(seg) > 1:
                    seg = shlex.split(seg[1]) + seg[2:]  # `env -S 'git push'`
                elif opt.startswith("--split-string="):
                    seg = shlex.split(opt.split("=", 1)[1]) + seg[1:]
                elif opt.startswith("-S") and len(opt) > 2:
                    seg = shlex.split(opt[2:]) + seg[1:]
                else:
                    seg = seg[2:] if opt in ENV_VALUE_OPTS else seg[1:]
        elif word in WRAPPERS:
            seg = seg[1:]
            while seg and seg[0].startswith("-"):
                seg = seg[2:] if seg[0] in WRAPPER_VALUE_OPTS else seg[1:]
        else:
            break
    return seg, flags, cwd


def shell_script(seg: list[str]) -> str | None:
    """The script of `bash -c '<script>'` (also `sh`, `zsh`, `-lc`, `-ec` ...), else None."""
    if not seg or os.path.basename(seg[0]) not in SHELLS:
        return None
    for i, a in enumerate(seg[1:-1], 1):
        if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", a):
            return seg[i + 1]
    return None


def reads_script_from_stdin(seg: list[str]) -> bool:
    """`bash`, `sh -e`, `bash -s arg` ...: a shell that runs whatever comes on stdin."""
    if not seg or os.path.basename(seg[0]) not in SHELLS:
        return False
    args = seg[1:]
    return all(a.startswith(("-", "+")) for a in args) or any(
        re.fullmatch(r"-[a-zA-Z]*s[a-zA-Z]*", a) for a in args
    )


def resolve(base: str, path: str) -> str:
    return os.path.normpath(os.path.join(base, os.path.expanduser(path)))


def clean_env() -> dict[str, str]:
    """Environment without GIT_* vars, which git sets while running its own hooks."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def run_git(cwd: str, *args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True,
            text=True,
            timeout=5,
            env=clean_env(),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def current_branch(cwd: str) -> str:
    r = run_git(cwd, "branch", "--show-current")
    return r.stdout.strip() if r else ""


def parse_git(args: list[str], cwd: str) -> tuple[str, str, list[str], list[str]]:
    """Return (directory, subcommand, subcommand args, `-c` values) for `git <args>`."""
    configs: list[str] = []
    i = 0
    while i < len(args) and args[i].startswith("-"):
        opt = args[i]
        if opt == "-C" and i + 1 < len(args):
            cwd = resolve(cwd, args[i + 1])
            i += 2
        elif opt == "-c" and i + 1 < len(args):
            configs.append(args[i + 1])
            i += 2
        elif opt in ("--git-dir", "--work-tree", "--namespace") and i + 1 < len(args):
            i += 2
        else:
            i += 1
    if i >= len(args):
        return cwd, "", [], configs
    return cwd, args[i], args[i + 1 :], configs


def alias_value(name: str, configs: list[str], cwd: str) -> str | None:
    key = f"alias.{name}".lower()
    for c in reversed(configs):
        k, _, v = c.partition("=")
        if k.lower() == key:
            return v
    r = run_git(cwd, "config", "--get", f"alias.{name}")
    return r.stdout.strip() if r and r.returncode == 0 else None


def expand_alias(
    sub: str, rest: list[str], configs: list[str], cwd: str
) -> tuple[str, list[str], str | None]:
    """Expand git aliases. Returns (subcommand, args, shell command of a `!` alias or None)."""
    for _ in range(5):  # aliases may chain; git itself stops on loops
        if not sub or sub in GIT_BUILTINS:
            break
        value = alias_value(sub, configs, cwd)
        if not value:
            break
        if value.startswith("!"):
            return sub, rest, " ".join([value[1:], *map(shlex.quote, rest)])
        try:
            words = shlex.split(value)
        except ValueError:
            break
        if not words:
            break
        sub, rest = words[0], words[1:] + rest
    return sub, rest, None


def pushes_to_protected(refspecs: list[str], cwd: str) -> bool:
    for spec in refspecs:
        dst = spec.lstrip("+").split(":")[-1]
        if dst in ("HEAD", "@"):
            dst = current_branch(cwd)
        if dst in (PROTECTED, f"refs/heads/{PROTECTED}"):
            return True
    return False


def is_commit(cwd: str, rev: str) -> bool:
    r = run_git(cwd, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    return r is not None and r.returncode == 0


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


def creates_commit(sub: str, rest: list[str]) -> bool:
    if sub not in NO_COMMIT_OPTS:
        return False
    return not any(a.split("=", 1)[0] in NO_COMMIT_OPTS[sub] for a in rest)


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
            check=False,
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


def check_git(seg: list[str], cwd: str, flags: set[str]) -> str | None:
    git_cwd, sub, rest, configs = parse_git(seg[1:], cwd)
    opts = seg[1 : len(seg) - len(rest)]  # global options before the subcommand
    if (
        "hookspath" in flags
        or any("hookspath" in a.lower() for a in opts)
        or (sub == "config" and any("hookspath" in a.lower() for a in rest))
    ):
        return BYPASS_MSG
    sub, rest, shell = expand_alias(sub, rest, configs, git_cwd)
    if shell is not None:  # `!` alias: git runs it from the top of the work tree
        return check(shell, git_cwd, flags)
    if sub in ("commit", "push", "merge") and ("skip" in flags or "--no-verify" in rest):
        return BYPASS_MSG
    if sub == "commit" and any(re.fullmatch(r"-[a-zA-Z]*n[a-zA-Z]*", a) for a in rest):
        return BYPASS_MSG
    if sub == "switch":
        return SWITCH_MSG
    if sub == "checkout" and checkout_switches(rest, git_cwd):
        return SWITCH_MSG
    if creates_commit(sub, rest) and current_branch(git_cwd) == PROTECTED:
        return COMMIT_MSG
    if sub == "push":
        positional = [a for a in rest if not a.startswith("-")]
        refspecs = positional[1:]
        if pushes_to_protected(refspecs, git_cwd):
            return PUSH_MSG
        if not refspecs and current_branch(git_cwd) == PROTECTED:
            return PUSH_MSG
    return None


def check(command: str, cwd: str, inherited: set[str] | None = None) -> str | None:
    """Return the reason to deny `command` run in `cwd`, or None to allow it.

    `inherited` holds env_flags set by the caller (`SKIP=x bash -c '...'`).
    """
    inherited = inherited or set()
    try:
        segs, bodies = segments(command)
    except ValueError:  # unbalanced quotes
        return PARSE_MSG if GIT_WORD.search(command) else None
    stack: list[str] = []  # cwd outside each `( ... )`
    dirs: list[str] = []  # pushd stack
    piped: list[str] = []  # heredoc bodies of the previous command, if it pipes into this one
    last: list[str] = []  # heredoc bodies of the previous command
    for raw in segs:
        if raw == ["|"]:
            piped = last
            continue
        stdin, piped = piped, []
        if raw == ["("]:
            stack.append(cwd)
            continue
        if raw == [")"]:
            cwd = stack.pop() if stack else cwd
            continue
        last = [bodies[int(w[len(HEREDOC) :])] for w in raw if w.startswith(HEREDOC)]
        stdin = last + stdin
        seg, flags, seg_cwd = unwrap([w for w in raw if not w.startswith(HEREDOC)], cwd)
        flags |= inherited
        if not seg:
            continue
        script = shell_script(seg)
        if script is not None:
            reason = check(script, seg_cwd, flags)
            if reason:
                return reason
            continue
        if reads_script_from_stdin(seg):  # `bash <<EOF` or `cat <<EOF | bash`
            for body in stdin:
                reason = check(body, seg_cwd, flags)
                if reason:
                    return reason
            continue
        if seg[0] == "cd":
            cwd = resolve(cwd, seg[1]) if len(seg) > 1 else os.path.expanduser("~")
            continue
        if seg[0] == "pushd":
            args = [a for a in seg[1:] if not a.startswith(("-", "+"))]
            if args:
                dirs.append(cwd)
                cwd = resolve(cwd, args[0])
            continue
        if seg[0] == "popd":
            cwd = dirs.pop() if dirs else cwd
            continue
        if seg[0] == "gh" and seg[1:3] == ["pr", "merge"]:
            reason = check_pr_merge(seg[3:], seg_cwd)
            if reason:
                return reason
            continue
        if seg[0] == "gh" and len(seg) > 1 and seg[1] == "api":
            if any(re.search(r"/pulls/[^/\s]+/merge\b", a) for a in seg[2:]):
                return MERGE_API_MSG
            continue
        if seg[0] == "git":
            reason = check_git(seg, seg_cwd, flags)
            if reason:
                return reason
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
