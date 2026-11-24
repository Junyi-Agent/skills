#!/usr/bin/env python3
"""PreToolUse hook (Bash): enforce the worktree workflow from CLAUDE.md.

Denies switching branches in place, creating commits on main (commit, merge, cherry-pick,
revert, am), pushing to main, skipping the git hooks (--no-verify or a prefix of it such as
--no-veri, SKIP=, core.hooksPath, including through GIT_CONFIG_* variables set in front of
a command, by `export`/`declare`/`typeset`, or by a bare assignment), and merging a PR whose CI checks have not all
passed (`gh pr merge`, or the merge endpoint through `gh api`).

The branch is read from the directory each git command actually runs in: `cd <dir>`,
`pushd`/`popd` earlier in the same command, `( ... )` subshells, `env -C <dir>` and
`git -C <dir>` are all followed. Commands are matched by their base name, so
`/usr/bin/git` and `/opt/homebrew/bin/gh` count as git and gh. Wrappers are looked through: `env VAR=x`, `command`,
`exec`, `nohup`, `time` (also by path, `/usr/bin/env`), `env -S` (with their options, parsed like getopt), and
`bash -c '...'`, which also inherits the `VAR=x` words in front of it. Git aliases are
expanded. Text fed to a shell that reads its script from stdin (`bash <<EOF`,
`bash <<< '...'`, `cat <<EOF | bash`, `echo '...' | bash`) is checked as a script, in the
directory and environment of that shell; other heredoc bodies are data and are not
checked. Shell comments are skipped and backslash-newline line continuations are joined.
If the hook input cannot be read, the check itself fails, or a command (or shell-fed text)
that mentions git/gh cannot be parsed, it is denied.

Loop mode (JY_MAINTAIN_LOOP=1 in the hook's own environment, set by the launchd job of the
jy-repo-maintain loop, docs/adr/0006): `gh pr merge` must name the PR and also pass
`maintain.py check pr` (no guardrail files, no X bump) with the config of this checkout;
only the gh commands in LOOP_GH run, `gh api` only as a GET; and the loop cannot put the
labels by which a human accepts or approves an issue (`accept_label`, `approve_label`).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

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
# `env` options that take a value (GNU and BSD), as short letters and long names.
ENV_VALUE_SHORT = "uCSPa"
ENV_VALUE_LONG = {"--unset", "--chdir", "--split-string", "--argv0"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
# Shell builtins that put `VAR=value` words into the environment of later commands.
EXPORTERS = {"export", "declare", "typeset", "local", "readonly"}
# Shell options that take the next word as their value: `-o pipefail`, `-O extglob`.
SHELL_VALUE_FLAGS = "oO"
SHELL_VALUE_LONG = {"--rcfile", "--init-file"}
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

# Loop mode (docs/adr/0006). The script and config come from the checkout this hook runs
# from, never from the worktree of the PR being merged, so a PR cannot loosen its own check.
PROJECT = Path(__file__).resolve().parents[2]
MAINTAIN_CONFIG = PROJECT / ".claude" / "maintain.json"
# gh commands the loop may run, per command its subcommands (None: any).
LOOP_GH: dict[str, set[str] | None] = {
    "issue": {"list", "view", "create", "comment", "edit", "close", "reopen", "status"},
    "pr": {"list", "view", "create", "comment", "edit", "checks", "diff", "merge", "ready",
           "close", "status"},
    "run": {"list", "view", "watch"},
    "repo": {"view"},
    "label": {"list"},
    "release": {"list", "view"},
    "auth": {"status"},
    "search": None,
    "api": None,  # GET only, see api_method
}  # fmt: skip
LABEL_OPTS = {"--add-label", "--label", "-l"}
API_FIELD_OPTS = {"-f", "-F", "--field", "--raw-field", "--input"}
LOOP_GH_MSG = (
    "The maintain loop may not run `gh {cmd}`: it only reads, comments, labels and "
    "opens/merges PRs (see LOOP_GH in .claude/hooks/git_guard.py, docs/adr/0006). Note the "
    "question in the maintain-log issue for a human instead."
)
LOOP_API_MSG = (
    "The maintain loop may only read through `gh api` (GET, without -f/-F/--input). Use the "
    "gh issue/pr subcommands to write (docs/adr/0006)."
)
LOOP_LABEL_MSG = (
    "The maintain loop may not add the label(s) {labels}: only a human accepts or approves "
    "an issue (docs/adr/0006). Ask in the issue and wait for the label."
)


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
    A `\\` before a newline, outside single quotes, is a line continuation: both go.
    Heredoc bodies and here-string words are cut out of the command and returned
    separately, so an apostrophe in them does not break parsing; each leaves a HEREDOC
    marker word in its command, so the caller knows which command reads it.
    """
    out: list[str] = []
    bodies: list[str] = []
    pending: list[tuple[int, str, bool]] = []  # heredocs waiting for the end of the line
    quote = ""
    word_start, word_quoted = 0, False
    i, n = 0, len(command)

    while i < n:
        c = command[i]
        if c == "\\" and command.startswith("\n", i + 1) and quote != "'":
            i += 2  # line continuation
            continue

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

            if op in ("<<", "<<-", "<<<"):
                out.append(f" {HEREDOC}{len(bodies)}")
                # a here-string's word is its text; a heredoc's body is filled in below
                bodies.append(dequote(command[start:i]) if op == "<<<" else "")
            if op in ("<<", "<<-"):
                delim = re.sub(r"[\"'\\]", "", command[start:i])
                pending.append((len(bodies) - 1, delim, op == "<<-"))
            out.append(" ")
            word_start, word_quoted = len(out), False
            continue

        out.append(c)
        i += 1
        if c == "\n" and pending:
            for index, delim, strip_tabs in pending:
                lines = []
                while i < n:
                    j = command.find("\n", i)
                    j = n if j < 0 else j
                    line = command[i:j]
                    i = j + 1
                    if (line.lstrip("\t") if strip_tabs else line) == delim:
                        break
                    lines.append(line)
                bodies[index] = "\n".join(lines)
            pending.clear()
        if c in WORD_END:
            word_start, word_quoted = len(out), False

    return "".join(out), bodies


def dequote(word: str) -> str:
    """The text of a shell word (`'a b'` -> `a b`), or the word itself if it cannot be split."""
    try:
        return " ".join(shlex.split(word))
    except ValueError:
        return word


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


def is_no_verify(arg: str) -> bool:
    """`--no-verify` or a prefix git accepts for it (`--no-veri`; `--no-ver` is ambiguous)."""
    return arg.startswith("--no-veri") and "--no-verify".startswith(arg)


def unwrap(seg: list[str], cwd: str) -> tuple[list[str], set[str], str]:
    """Drop leading `VAR=x` words and wrappers (also called by path: `/usr/bin/env`).

    Returns the command, the hook-skipping flags its environment sets (see env_flags),
    and the directory it runs in (`env -C <dir>` changes it for this command only).
    Only the words in front of this command are seen: `export SKIP=x` in an earlier command
    is not tracked (an accepted gap, docs/adr/0001). `check` denies an `export` or bare
    assignment that sets core.hooksPath through GIT_CONFIG_* without tracking variables.
    """
    flags: set[str] = set()
    while seg:
        word = seg[0]
        name = os.path.basename(word)
        if ASSIGNMENT.match(word):
            flags |= env_flags(word)
            seg = seg[1:]
        elif name == "env":
            seg, cwd = unwrap_env(seg[1:], cwd)
        elif name in WRAPPERS:
            seg = seg[1:]
            while seg and seg[0].startswith("-"):
                seg = seg[2:] if seg[0] in WRAPPER_VALUE_OPTS else seg[1:]
        else:
            break
    return seg, flags, cwd


def unwrap_env(args: list[str], cwd: str) -> tuple[list[str], str]:
    """Parse `env` options like getopt: `-iS'git push'`, `-P <dir>`, `--unset=X`, `--`.

    Returns the words after the options and the directory (`-C <dir>`). `-S <string>` is
    split into words that are parsed again, so options inside it count too.
    """
    while args and args[0].startswith("-"):
        opt, args = args[0], args[1:]
        if opt == "--":
            break
        if opt.startswith("--"):
            name, eq, value = opt.partition("=")
            if name in ENV_VALUE_LONG and not eq:
                if not args:
                    break
                value, args = args[0], args[1:]
            if name == "--chdir":
                cwd = resolve(cwd, value)
            elif name == "--split-string":
                args = shlex.split(value) + args
            continue

        for k, letter in enumerate(opt[1:], 2):  # `-` alone is `-i`: no letters
            if letter not in ENV_VALUE_SHORT:
                continue
            value = opt[k:]
            if not value:
                if not args:
                    break
                value, args = args[0], args[1:]
            if letter == "C":
                cwd = resolve(cwd, value)
            elif letter == "S":
                args = shlex.split(value) + args
            break
    return args, cwd


def shell_script(seg: list[str]) -> str | None:
    """The script of `bash -c '<script>'` (also `sh`, `zsh`, `-lc`, `-ec` ...), else None."""
    if not seg or os.path.basename(seg[0]) not in SHELLS:
        return None
    for i, a in enumerate(seg[1:-1], 1):
        if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", a):
            return seg[i + 1]
    return None


def reads_script_from_stdin(seg: list[str]) -> bool:
    """`bash`, `bash -euo pipefail`, `bash -s arg` ...: a shell that runs what comes on stdin.

    It does unless a script file follows the options; `-s` reads stdin even then.
    """
    if not seg or os.path.basename(seg[0]) not in SHELLS:
        return False
    args, i, stdin_flag = seg[1:], 0, False

    while i < len(args):
        a = args[i]
        if a in ("-", "--"):
            i += 1
            break
        if a.startswith("--"):
            i += 2 if a in SHELL_VALUE_LONG else 1
            continue
        if len(a) < 2 or a[0] not in "-+":
            break
        stdin_flag |= a[0] == "-" and "s" in a
        i += 1 + sum(letter in SHELL_VALUE_FLAGS for letter in a[1:])

    return stdin_flag or i >= len(args)


def passes_stdin_on(seg: list[str]) -> bool:
    """`tee file` and `cat` (no files, or `-`) write their stdin on to the next command."""
    if not seg:
        return False
    files = [a for a in seg[1:] if a == "-" or not a.startswith("-")]
    return seg[0] == "tee" or (seg[0] == "cat" and (not files or "-" in files))


def printed_text(seg: list[str]) -> list[str]:
    """What `echo ...` or `printf ...` writes, as text a shell after a pipe would run."""
    if not seg or seg[0] not in ("echo", "printf"):
        return []
    args = seg[1:]
    if seg[0] == "echo":
        while args and re.fullmatch(r"-[neE]+", args[0]):
            args = args[1:]
        text = " ".join(args)
    else:
        if args[:1] == ["-v"]:  # `printf -v var` assigns, prints nothing
            return []
        text = "\n".join(args)
    return [text.replace("\\n", "\n")]


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


def in_submodule(cwd: str) -> bool:
    r = run_git(cwd, "rev-parse", "--show-superproject-working-tree")
    return r is not None and r.returncode == 0 and bool(r.stdout.strip())


def pins_submodule(rest: list[str], cwd: str) -> bool:
    """`git checkout [--detach] <commit>` in a submodule, where <commit> is not a local branch.

    This pins the submodule to a tag or commit, as `git submodule update` does; it does not
    switch a branch of the repo the agent works in.
    """
    if any(a.startswith("-") and a not in ("--detach", "-q", "--quiet") for a in rest):
        return False
    positional = [a for a in rest if not a.startswith("-")]
    if len(positional) != 1:
        return False
    target = positional[0]
    branch = run_git(cwd, "show-ref", "--verify", "--quiet", f"refs/heads/{target}")
    if branch is None or branch.returncode == 0:
        return False
    return is_commit(cwd, target) and in_submodule(cwd)


def checkout_switches(rest: list[str], cwd: str) -> bool:
    """Whether `git checkout <rest>` switches branch rather than restoring files."""
    if pins_submodule(rest, cwd):
        return False
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

    if loop_mode():
        reason = loop_merge_policy(selector, repo, cwd)
        if reason:
            return reason

    code, output = pr_checks(selector + repo, cwd)
    if code == 0:
        return None
    return (
        "Do not merge: the PR's CI checks have not all passed (gh pr checks exit "
        f"{code}). Wait for CI or fix it, then merge.\n{output[-1500:]}"
    )


def loop_mode() -> bool:
    return os.environ.get("JY_MAINTAIN_LOOP") == "1"


def maintain_script(project: Path = PROJECT) -> Path:
    """maintain.py of the checkout. jy-repo-maintain is an internal skill kept in
    .claude/skills/, not in the published skills/. A missing script fails the check."""
    return project / ".claude" / "skills" / "jy-repo-maintain" / "scripts" / "maintain.py"


def maintain_policy(args: list[str], cwd: str) -> tuple[int, str]:
    """Run `maintain.py check pr <args>`; exit 0 means the loop may merge."""
    try:
        r = subprocess.run(
            [sys.executable, str(maintain_script()), "check", "pr", *args],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=cwd,
            env=clean_env(),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, f"could not run maintain.py: {e}"
    return r.returncode, (r.stdout + r.stderr).strip()


def loop_merge_policy(selector: list[str], repo: list[str], cwd: str) -> str | None:
    if not selector:
        return "In loop mode, name the PR: `gh pr merge <number> --squash` (docs/adr/0006)."

    repo_args = ["--repo", repo[1]] if len(repo) == 2 else repo
    args = [*selector, *repo_args, "--config", str(MAINTAIN_CONFIG)]
    code, output = maintain_policy(args, cwd)
    if code == 0:
        return None
    return (
        "The maintain loop may not merge this PR by itself (docs/adr/0006); add the "
        f"needs-human-merge label and note it in the maintain-log issue.\n{output[-1500:]}"
    )


def human_labels() -> set[str]:
    try:
        config = json.loads(MAINTAIN_CONFIG.read_text())
    except (OSError, ValueError):
        config = {}
    return {config.get("accept_label", "accepted"), config.get("approve_label", "approved")}


def label_values(args: list[str]) -> set[str]:
    values, i = [], 0
    while i < len(args):
        a = args[i]
        name, eq, val = a.partition("=")
        if a in LABEL_OPTS and i + 1 < len(args):
            values.append(args[i + 1])
            i += 1
        elif eq and name in LABEL_OPTS:
            values.append(val)
        i += 1
    return {v.strip() for vs in values for v in vs.split(",")}


def api_method(args: list[str]) -> str:
    """The HTTP method `gh api` uses: -X/--method, else POST when fields are given."""
    method, fields, i = None, False, 0
    while i < len(args):
        a = args[i]
        if a in ("-X", "--method") and i + 1 < len(args):
            method = args[i + 1]
            i += 1
        elif a.startswith("--method="):
            method = a.split("=", 1)[1]
        elif a.startswith("-X") and len(a) > 2:
            method = a[2:]
        elif (
            a in API_FIELD_OPTS
            or a.split("=", 1)[0] in API_FIELD_OPTS
            or (a[:2] in ("-f", "-F") and len(a) > 2)
        ):
            fields = True
        i += 1
    return (method or ("POST" if fields else "GET")).upper()


def check_gh_loop(seg: list[str]) -> str | None:
    cmd = seg[1] if len(seg) > 1 else ""
    sub = seg[2] if len(seg) > 2 else ""
    allowed = LOOP_GH.get(cmd, set())
    if cmd not in LOOP_GH or (allowed is not None and sub not in allowed):
        return LOOP_GH_MSG.format(cmd=" ".join(seg[1:3]))

    if cmd == "api":
        rest = seg[2:]
        graphql = any(a == "graphql" for a in rest if not a.startswith("-"))
        return LOOP_API_MSG if graphql or api_method(rest) != "GET" else None

    held = label_values(seg[3:]) & human_labels() if sub in ("create", "edit") else set()
    if held:
        return LOOP_LABEL_MSG.format(labels=", ".join(sorted(held)))
    return None


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

    if sub in ("commit", "push", "merge") and (
        "skip" in flags or any(is_no_verify(a) for a in rest)
    ):
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
    piped: list[str] = []  # text the previous command pipes into this one
    last: list[str] = []  # heredoc bodies and echo/printf text of the previous command

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

        own = [bodies[int(w[len(HEREDOC) :])] for w in raw if w.startswith(HEREDOC)]
        stdin = own + stdin
        seg, flags, seg_cwd = unwrap([w for w in raw if not w.startswith(HEREDOC)], cwd)
        flags |= inherited
        last = own + printed_text(seg) + (stdin if passes_stdin_on(seg) else [])
        if not seg:  # only assignments: `GIT_CONFIG_PARAMETERS=...; export ...; git push`
            if "hookspath" in flags:
                return BYPASS_MSG
            continue
        name = os.path.basename(seg[0])
        if name in EXPORTERS and any(
            "hookspath" in env_flags(a) for a in seg[1:] if ASSIGNMENT.match(a)
        ):
            return BYPASS_MSG

        script = shell_script(seg)
        if script is not None:
            reason = check(script, seg_cwd, flags)
            if reason:
                return reason
            continue
        if reads_script_from_stdin(seg):  # `bash <<EOF`, `cat <<EOF | bash`, `echo .. | sh`
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

        if name == "gh" and loop_mode():
            reason = check_gh_loop(seg)
            if reason:
                return reason
        if name == "gh" and seg[1:3] == ["pr", "merge"]:
            reason = check_pr_merge(seg[3:], seg_cwd)
            if reason:
                return reason
            continue
        if name == "gh" and len(seg) > 1 and seg[1] == "api":
            if any(re.search(r"/pulls/[^/\s]+/merge\b", a) for a in seg[2:]):
                return MERGE_API_MSG
            continue
        if name == "git":
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
