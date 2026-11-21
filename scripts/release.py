#!/usr/bin/env python3
"""Check the repo version (VERSION + CHANGELOG.md) against the release rules in ADR 0005.

Usage: release.py check version [--base REF] [-o text|json]

Rules:
  - VERSION holds one semver string, e.g. 1.2.3
  - the first "## " heading of CHANGELOG.md starts with that version
  - with --base: VERSION is not lower than the one at REF, and when anything under skills/
    changed since the merge base with REF, VERSION is higher than the one at REF

Text output prints nothing on success and the problems on stderr on failure.
Exits 0 when all rules hold, 1 when one fails, 2 on a bad argument or a git error.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
HEADING = re.compile(r"^## (\S+)", re.MULTILINE)


class GitError(Exception):
    pass


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def parse(version: str) -> tuple[int, int, int] | None:
    m = SEMVER.match(version)
    return tuple(int(g) for g in m.groups()) if m else None


def check_version(base: str | None) -> tuple[str, list[str]]:
    """(VERSION, problems). Raises GitError when base cannot be read."""
    problems = []

    vfile = ROOT / "VERSION"
    version = vfile.read_text().strip() if vfile.is_file() else ""
    if not vfile.is_file():
        problems.append("VERSION 文件不存在")
    elif parse(version) is None:
        problems.append(f"VERSION 不是 X.Y.Z 格式：{version!r}")

    changelog = ROOT / "CHANGELOG.md"
    m = HEADING.search(changelog.read_text()) if changelog.is_file() else None
    if not changelog.is_file():
        problems.append("CHANGELOG.md 不存在")
    elif not m or m.group(1) != version:
        found = m.group(1) if m else "没有 ## 标题"
        problems.append(f"CHANGELOG.md 第一个版本标题是 {found}，应为 {version}")

    if base is None or parse(version) is None:
        return version, problems

    try:
        old = git("show", f"{base}:VERSION").strip()
    except GitError:
        old = ""  # base predates VERSION: any version is fine
    changed = git("diff", "--name-only", f"{base}...HEAD", "--", "skills/").split()

    if parse(old) and parse(version) < parse(old):
        problems.append(f"VERSION {version} 低于 {base} 上的 {old}")
    elif parse(old) and changed and parse(version) == parse(old):
        problems.append(
            f"skills/ 有改动（{len(changed)} 个文件，如 {changed[0]}），"
            f"VERSION 要从 {old} 升上去并在 CHANGELOG.md 加一节"
        )
    return version, problems


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="release.py", allow_abbrev=False)
    verbs = p.add_subparsers(dest="verb", required=True, metavar="VERB")
    check = verbs.add_parser("check", allow_abbrev=False, help="check release rules")
    resources = check.add_subparsers(dest="resource", required=True, metavar="RESOURCE")
    v = resources.add_parser(
        "version", allow_abbrev=False, help="VERSION, CHANGELOG.md and the bump rule"
    )
    v.add_argument("--base", help="compare with this ref, e.g. origin/main")
    v.add_argument("-o", "--output", choices=["text", "json"], default="text")
    return p.parse_args(argv)


def main() -> int:
    args = parse_args(sys.argv[1:])

    try:
        version, problems = check_version(args.base)
    except GitError as e:
        print(f"release.py: {e}", file=sys.stderr)
        return 2

    if args.output == "json":
        print(json.dumps({"version": version, "ok": not problems, "problems": problems}))
    for problem in problems:
        print(f"release.py: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
