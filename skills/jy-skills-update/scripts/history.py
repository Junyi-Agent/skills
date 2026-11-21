#!/usr/bin/env python3
"""List the sessions of a project where jy-* skills were invoked, with what followed.

Usage: history.py get invocations [--project DIR] [--prefix jy-] [--after N]
                                  [--projects-dir DIR] [-o text|json]

Claude Code keeps each session as ~/.claude/projects/<encoded path>/<id>.jsonl, one JSON
object per line. The encoded path is the absolute project path with every character other
than a letter or digit replaced by `-` (/Users/a/my.proj -> -Users-a-my-proj).
`--projects-dir` overrides the parent directory (default: $CLAUDE_CONFIG_DIR/projects, or
~/.claude/projects).

Which directories are read:
  - the project's own directory (--project, default: the git top level of the current
    directory, or the current directory without git);
  - the directories of its live worktrees (`git worktree list`);
  - other directories named <encoded project>-<anything>, which is how a worktree at
    ../<repo>-<feature> is encoded. A sibling directory like <repo>-old can be a separate
    repo with the same prefix, so each such directory is checked by the `cwd` its sessions
    recorded: if that path still exists and is not a worktree of this repo, the directory
    is skipped; if the path is gone (a removed worktree), it is kept and marked
    "unverified" in the output. A separate repo that was deleted from disk can therefore
    still be listed, marked unverified. `<repo>project` without the `-` never matches.

An invocation is a user message with <command-name>/jy-…</command-name> (also with a
plugin prefix, /plugin:jy-…), or an assistant Skill tool call whose skill starts with the
prefix. Sidechain (subagent) records are skipped. For each invocation it prints up to N
following user messages (tool results, task notifications, system reminders and local
command output left out; 500 characters each) and every tool result marked is_error before
the next invocation in the same session (300 characters each). Unreadable files and bad
lines are skipped.

Exits 0 on success (also when nothing was invoked), 1 when no session directory exists for
the project, 2 on a bad argument.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

MSG_LIMIT = 500
ERR_LIMIT = 300
NOISE_PREFIXES = (
    "<task-notification",
    "<system-reminder",
    "<local-command",
    "<command-name>",
    "<command-message>",
    "<bash-stdout",
    "<bash-stderr",
)


def encode(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def git(cwd: str, *args: str) -> str | None:
    # GIT_DIR / GIT_INDEX_FILE from a calling git hook would point -C at the wrong repo
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        r = subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def project_root(path: str) -> str:
    return git(path, "rev-parse", "--show-toplevel") or os.path.abspath(path)


def common_dir(path: str) -> str | None:
    out = git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return os.path.realpath(out) if out else None


def worktree_paths(root: str) -> list[str]:
    out = git(root, "worktree", "list", "--porcelain") or ""
    return [ln[len("worktree ") :] for ln in out.splitlines() if ln.startswith("worktree ")]


def recorded_cwd(directory: Path) -> str | None:
    """The first `cwd` any session in `directory` recorded."""
    for f in sorted(directory.glob("*.jsonl")):
        for rec in read_records(f):
            if isinstance(rec.get("cwd"), str):
                return rec["cwd"]
    return None


def session_dirs(root: str, projects: Path) -> list[tuple[Path, bool]]:
    """(directory, verified) pairs holding this project's sessions."""
    own = encode(root)
    found: dict[str, bool] = {}
    if (projects / own).is_dir():
        found[own] = True

    for wt in worktree_paths(root):
        if (projects / encode(wt)).is_dir():
            found[encode(wt)] = True

    repo = common_dir(root)
    if projects.is_dir():
        for d in sorted(projects.iterdir()):
            if d.name in found or not d.name.startswith(own + "-") or not d.is_dir():
                continue
            cwd = recorded_cwd(d)
            if cwd and os.path.isdir(cwd):
                if repo and common_dir(cwd) == repo:
                    found[d.name] = True
                continue
            found[d.name] = False

    return [(projects / name, verified) for name, verified in found.items()]


def read_records(path: Path) -> list[dict]:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def content_items(rec: dict) -> list:
    msg = rec.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return content if isinstance(content, list) else []


def item_text(item: dict) -> str:
    c = item.get("content", item.get("text", ""))
    if isinstance(c, list):
        return " ".join(x.get("text", "") for x in c if isinstance(x, dict))
    return c if isinstance(c, str) else ""


def invoked_skill(rec: dict, prefix: str) -> str | None:
    command = re.compile(
        rf"<command-name>/(?:[\w.-]+:)?({re.escape(prefix)}[\w.-]*)</command-name>"
    )
    for item in content_items(rec):
        if not isinstance(item, dict):
            continue
        if rec.get("type") == "user" and item.get("type") == "text":
            m = command.search(item.get("text", ""))
            if m:
                return m.group(1)
        if rec.get("type") == "assistant" and item.get("type") == "tool_use":
            skill = (item.get("input") or {}).get("skill", "")
            name = skill.rsplit(":", 1)[-1] if isinstance(skill, str) else ""
            if item.get("name") == "Skill" and name.startswith(prefix):
                return name
    return None


def user_text(rec: dict) -> str | None:
    """The text a person typed, or None for tool results and harness-injected messages."""
    if rec.get("type") != "user" or rec.get("isMeta"):
        return None
    items = [i for i in content_items(rec) if isinstance(i, dict)]
    if any(i.get("type") == "tool_result" for i in items):
        return None

    text = " ".join(i.get("text", "") for i in items if i.get("type") == "text").strip()
    if not text or text.startswith(NOISE_PREFIXES):
        return None
    return text


def tool_errors(rec: dict) -> list[str]:
    if rec.get("type") != "user":
        return []
    return [
        item_text(i)
        for i in content_items(rec)
        if isinstance(i, dict) and i.get("type") == "tool_result" and i.get("is_error")
    ]


def scan_session(path: Path, source: str, verified: bool, prefix: str, after: int) -> list[dict]:
    found: list[dict] = []
    for rec in read_records(path):
        if rec.get("isSidechain"):
            continue
        ts = rec.get("timestamp", "")

        skill = invoked_skill(rec, prefix)
        if skill:
            last = found[-1] if found else None
            # `/jy-x` followed by the model's own Skill call for it is one invocation
            if last and last["skill"] == skill and not last["user_messages"]:
                continue
            found.append(
                {
                    "session": path.name,
                    "source_dir": source,
                    "verified": verified,
                    "timestamp": ts,
                    "skill": skill,
                    "user_messages": [],
                    "errors": [],
                }
            )
            continue
        if not found:
            continue

        cur = found[-1]
        text = user_text(rec)
        if text and len(cur["user_messages"]) < after:
            cur["user_messages"].append({"timestamp": ts, "text": text[:MSG_LIMIT]})
        for err in tool_errors(rec):
            cur["errors"].append({"timestamp": ts, "text": err.strip()[:ERR_LIMIT]})

    return found


def collect(dirs: list[tuple[Path, bool]], prefix: str, after: int) -> list[dict]:
    out = []
    for d, verified in dirs:
        for f in sorted(d.glob("*.jsonl")):
            out.extend(scan_session(f, d.name, verified, prefix, after))
    return sorted(out, key=lambda x: x["timestamp"])


def render_text(invocations: list[dict]) -> str:
    if not invocations:
        return "没有找到 skill 调用。"
    blocks = []
    for inv in invocations:
        mark = "" if inv["verified"] else "（unverified：会话里的目录已不存在）"
        lines = [
            f"## {inv['timestamp'][:16]} {inv['skill']}  {inv['source_dir']}/{inv['session']}{mark}"
        ]
        lines += [
            f"  用户 [{m['timestamp'][:16]}] {oneline(m['text'])}" for m in inv["user_messages"]
        ]
        lines += [f"  报错 [{e['timestamp'][:16]}] {oneline(e['text'])}" for e in inv["errors"]]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def oneline(text: str) -> str:
    return " / ".join(ln.strip() for ln in text.splitlines() if ln.strip())


def default_projects_dir() -> str:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    return os.path.join(base, "projects")


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="history.py",
        allow_abbrev=False,
        description="Find Claude Code sessions of a project that invoked jy-* skills.",
    )
    verbs = p.add_subparsers(dest="verb", required=True, metavar="VERB")
    get = verbs.add_parser("get", allow_abbrev=False, help="print resources")
    resources = get.add_subparsers(dest="resource", required=True, metavar="RESOURCE")
    inv = resources.add_parser(
        "invocations", allow_abbrev=False, help="skill invocations and what followed them"
    )
    inv.add_argument(
        "--project", default=".", help="project directory (default: git top level of .)"
    )
    inv.add_argument("--prefix", default="jy-", help="skill name prefix (default: jy-)")
    inv.add_argument("--after", type=int, default=5, help="user messages kept per invocation")
    inv.add_argument("--projects-dir", default=default_projects_dir(), help=argparse.SUPPRESS)
    inv.add_argument("-o", "--output", choices=["text", "json"], default="text")

    args = p.parse_args(argv)
    if args.after < 0:
        p.error("--after must be >= 0")
    return args


def main() -> None:
    args = parse_args(sys.argv[1:])
    root = project_root(args.project)
    dirs = session_dirs(root, Path(args.projects_dir))
    if not dirs:
        print(f"没有找到 {root} 的会话目录（{args.projects_dir}/{encode(root)}）", file=sys.stderr)
        sys.exit(1)

    invocations = collect(dirs, args.prefix, args.after)
    if args.output == "json":
        print(json.dumps(invocations, ensure_ascii=False, indent=2))
    else:
        print(render_text(invocations))


if __name__ == "__main__":
    main()
