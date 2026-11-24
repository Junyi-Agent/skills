#!/usr/bin/env python3
"""Deterministic parts of the jy-repo-maintain loop: which issues it may act on, whether
it may merge a PR by itself, and the launchd job that runs one tick every few hours.

Usage:
  maintain.py get issues [--repo OWNER/NAME] [--config PATH] [-o text|json]
  maintain.py check pr NUMBER [--repo OWNER/NAME] [--config PATH] [-o text|json]
  maintain.py create schedule --checkout DIR [--every-hours N] [-o text|json]

get issues
  Open issues (pull requests excluded) with their kind and whether the loop may act:
  actionable when `require_accept_label` is false or when the issue carries `accept_label`,
  whoever opened it: agents file issues under the owner's account. A proposal also needs
  `approve_label` before the loop implements it (`approved` in the output). Issues with
  `log_label` or `wontfix` are skipped.

check pr
  Exit 0 when the loop may merge the PR by itself: its diff touches no path in
  `guardrails` (both sides of a rename count) and does not raise the X of VERSION.
  CI is checked separately by .claude/hooks/git_guard.py.

create schedule
  Print a launchd plist that runs one tick in DIR every N hours (default 6), with
  JY_MAINTAIN_LOOP=1 so git_guard applies the loop rules. The tick does not start when
  DIR has uncommitted changes, and fast-forwards DIR to origin before it starts. Install it with
  `launchctl bootstrap gui/$(id -u) <file>`.

The config is `.claude/maintain.json` at the repository root (see DEFAULTS for the keys).
Exits 0 on success (check pr: merge allowed), 1 when the check fails or gh fails,
2 on a bad argument.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

KINDS = ("bug", "proposal", "question", "needs-info")
DEFAULTS = {
    "require_accept_label": True,
    "accept_label": "accepted",
    "approve_label": "approved",
    "log_label": "maintain-log",
    "guardrails": [],
}
VERSION_LINE = re.compile(r"^([-+])(\d+)\.\d+\.\d+\s*$")
DIFF_HEADER = re.compile(r"^diff --git a/(.+?) b/(.+)$")


def load_config(path: str | None) -> dict:
    if path is None:
        root = git_root()
        path = str(Path(root) / ".claude" / "maintain.json") if root else ""

    config = dict(DEFAULTS)
    if path and Path(path).is_file():
        config.update(json.loads(Path(path).read_text()))
    return config


def git_root() -> str | None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}  # set by git hooks
    r = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    return r.stdout.strip() if r.returncode == 0 else None


def gh(*args: str) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def repo_name(repo: str | None) -> str:
    return repo or gh("repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner").strip()


def classify(issue: dict, config: dict) -> dict | None:
    """Return the loop's view of one issue from the REST API, or None to skip it."""
    labels = {lb["name"] for lb in issue.get("labels", [])}
    if config["log_label"] in labels or "wontfix" in labels:
        return None

    kind = next((k for k in KINDS if k in labels), "untriaged")

    if not config["require_accept_label"]:
        actionable, reason = True, "require_accept_label is off"
    elif config["accept_label"] in labels:
        actionable, reason = True, f"labelled {config['accept_label']}"
    else:
        actionable, reason = False, f"waiting for the {config['accept_label']} label"

    return {
        "number": issue["number"],
        "title": issue["title"],
        "kind": kind,
        "actionable": actionable,
        "approved": config["approve_label"] in labels,
        "reason": reason,
        "labels": sorted(labels),
        "url": issue["html_url"],
    }


def get_issues(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    raw = json.loads(
        gh("api", "--paginate", f"repos/{repo_name(args.repo)}/issues?state=open&per_page=100")
    )

    issues = [c for i in raw if "pull_request" not in i and (c := classify(i, config))]
    if args.output == "json":
        json.dump(issues, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0

    for i in issues:
        mark = "act " if i["actionable"] else "wait"
        extra = " approved" if i["approved"] else ""
        print(f"{mark} #{i['number']} [{i['kind']}{extra}] {i['title']} ({i['reason']})")
    return 0


def diff_paths(diff: str) -> set[str]:
    paths = set()
    for line in diff.splitlines():
        m = DIFF_HEADER.match(line)
        if m:
            paths.update(m.groups())
    return paths


def major_change(diff: str) -> tuple[int, int] | None:
    """(old X, new X) from the VERSION hunk of a diff, or None when VERSION is unchanged."""
    in_version, old, new = False, None, None
    for line in diff.splitlines():
        m = DIFF_HEADER.match(line)
        if m:
            in_version = "VERSION" in m.groups()
            continue

        v = VERSION_LINE.match(line) if in_version else None
        if v and v.group(1) == "-":
            old = int(v.group(2))
        elif v:
            new = int(v.group(2))
    return (old, new) if old is not None and new is not None else None


def is_guarded(path: str, patterns: list[str]) -> bool:
    for p in patterns:
        if p.endswith("/") and path.startswith(p):
            return True
        if fnmatch.fnmatchcase(path, p):
            return True
    return False


def policy_problems(diff: str, config: dict) -> list[str]:
    problems = []
    guarded = sorted(p for p in diff_paths(diff) if is_guarded(p, config["guardrails"]))
    if guarded:
        problems.append("改到了护栏文件，要人来合并：" + ", ".join(guarded))

    change = major_change(diff)
    if change and change[1] > change[0]:
        problems.append(f"VERSION 的 X 从 {change[0]} 升到 {change[1]}（不兼容），要人来合并")
    return problems


def check_pr(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    repo = ["--repo", args.repo] if args.repo else []
    problems = policy_problems(gh("pr", "diff", args.number, *repo), config)

    if args.output == "json":
        json.dump({"mergeable": not problems, "problems": problems}, sys.stdout, ensure_ascii=False)
        print()
    elif problems:
        print("\n".join(problems), file=sys.stderr)
    return 1 if problems else 0


def plist(checkout: Path, hours: int) -> str:
    label = f"jy.repo-maintain.{checkout.name}"
    log = Path.home() / "Library" / "Logs" / f"{label}.log"
    tick = (
        f"cd {shlex.quote(str(checkout))} && "
        # an agent edit left in the checkout would change the hook and config next tick
        'test -z "$(git status --porcelain)" && git pull --ff-only -q && '
        "claude -p /jy-repo-maintain --permission-mode bypassPermissions"
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{escape(label)}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string><string>-lic</string><string>{escape(tick)}</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict><key>JY_MAINTAIN_LOOP</key><string>1</string></dict>
  <key>StartInterval</key><integer>{hours * 3600}</integer>
  <key>StandardOutPath</key><string>{escape(str(log))}</string>
  <key>StandardErrorPath</key><string>{escape(str(log))}</string>
</dict>
</plist>
"""


def create_schedule(args: argparse.Namespace) -> int:
    checkout = Path(args.checkout).expanduser().resolve()
    if not (checkout / ".git").exists():
        print(f"{checkout} 不是 git 仓库的根目录", file=sys.stderr)
        return 2
    if args.every_hours < 1:
        print("--every-hours 至少是 1", file=sys.stderr)
        return 2

    text = plist(checkout, args.every_hours)
    if args.output == "json":
        json.dump({"label": f"jy.repo-maintain.{checkout.name}", "plist": text}, sys.stdout)
        print()
    else:
        sys.stdout.write(text)
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="maintain.py", description=__doc__.split("\n\n")[0])
    verbs = p.add_subparsers(dest="verb", required=True)
    get = verbs.add_parser("get").add_subparsers(dest="resource", required=True)
    chk = verbs.add_parser("check").add_subparsers(dest="resource", required=True)
    create = verbs.add_parser("create").add_subparsers(dest="resource", required=True)

    issues = get.add_parser("issues", help="open issues and whether the loop may act")
    issues.set_defaults(func=get_issues)
    issues.add_argument("--repo")
    issues.add_argument("--config")

    pr = chk.add_parser("pr", help="whether the loop may merge this PR by itself")
    pr.set_defaults(func=check_pr)
    pr.add_argument("number")
    pr.add_argument("--repo")
    pr.add_argument("--config")

    sched = create.add_parser("schedule", help="launchd plist that runs the loop")
    sched.set_defaults(func=create_schedule)
    sched.add_argument("--checkout", required=True)
    sched.add_argument("--every-hours", type=int, default=6)

    for sub in (issues, pr, sched):
        sub.add_argument("-o", "--output", choices=("text", "json"), default="text")
    return p


def main() -> int:
    args = parser().parse_args()
    try:
        return args.func(args)
    except (RuntimeError, json.JSONDecodeError, KeyError) as e:
        print(e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
