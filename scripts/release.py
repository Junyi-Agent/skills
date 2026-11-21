#!/usr/bin/env python3
"""Check the repo version (VERSION + CHANGELOG.md) against the release rules in ADR 0005.

Usage: release.py check version [--base REF] [--ref REF] [-o text|json]

Rules:
  - VERSION holds one semver string, e.g. 1.2.3
  - the first "## " heading of CHANGELOG.md starts with that version
  - with --base, when VERSION or anything under skills/ changed since the merge base with
    REF: VERSION is not lower than the one at REF, and when skills/ changed, it is higher.
    A branch that changed neither is not compared, so being behind REF is fine.

The files are read from the work tree, or with --ref from that commit (pre-push passes
the commit being pushed, which need not be the one checked out). A --ref of 40 zeros is
git's null object, what pre-push gives for a deleted branch: there is nothing to check.

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
NULL_REF = "0" * 40


class GitError(Exception):
    pass


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def parse(version: str) -> tuple[int, ...] | None:
    m = SEMVER.match(version)
    return tuple(int(g) for g in m.groups()) if m else None


def read(name: str, ref: str | None) -> str | None:
    """A file's text in the work tree (ref None) or at `ref`; None if it does not exist."""
    if ref is None:
        p = ROOT / name
        return p.read_text() if p.is_file() else None
    try:
        return git("show", f"{ref}:{name}")
    except GitError:
        return None


def check_version(base: str | None, ref: str | None = None) -> tuple[str, list[str]]:
    """(VERSION, problems). Raises GitError when base or ref cannot be read."""
    problems = []
    if ref is not None:
        git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")

    vtext = read("VERSION", ref)
    version = (vtext or "").strip()
    if vtext is None:
        problems.append("VERSION 文件不存在")
    elif parse(version) is None:
        problems.append(f"VERSION 不是 X.Y.Z 格式：{version!r}")

    changelog = read("CHANGELOG.md", ref)
    m = HEADING.search(changelog) if changelog is not None else None
    if changelog is None:
        problems.append("CHANGELOG.md 不存在")
    elif not m or m.group(1) != version:
        found = m.group(1) if m else "没有 ## 标题"
        problems.append(f"CHANGELOG.md 第一个版本标题是 {found}，应为 {version}")

    new = parse(version)
    if base is None or new is None:
        return version, problems
    return version, problems + bump_problems(base, ref or "HEAD", version, new)


def bump_problems(base: str, ref: str, version: str, new: tuple[int, ...]) -> list[str]:
    """The --base rules for a valid VERSION at `ref`."""
    old_text = read("VERSION", base)  # None when base predates VERSION: any version is fine
    old = (old_text or "").strip()
    since = f"{base}...{ref}"  # changes on the branch since the merge base
    skills = git("diff", "--name-only", since, "--", "skills/").split()
    bumped = bool(git("diff", "--name-only", since, "--", "VERSION").strip())
    if not parse(old) or not (skills or bumped):
        return []

    if new < parse(old):
        return [
            f"VERSION {version} 低于 {base} 上的 {old}：分支落后于 {base}，"
            f"先 rebase 到 {base}，再把版本升到 {old} 以上"
        ]
    if skills and new == parse(old):
        return [
            f"skills/ 有改动（{len(skills)} 个文件，如 {skills[0]}），"
            f"VERSION 要从 {old} 升上去并在 CHANGELOG.md 加一节"
        ]
    return []


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="release.py", allow_abbrev=False)
    verbs = p.add_subparsers(dest="verb", required=True, metavar="VERB")
    check = verbs.add_parser("check", allow_abbrev=False, help="check release rules")
    resources = check.add_subparsers(dest="resource", required=True, metavar="RESOURCE")
    v = resources.add_parser(
        "version", allow_abbrev=False, help="VERSION, CHANGELOG.md and the bump rule"
    )
    v.add_argument("--base", help="compare with this ref, e.g. origin/main")
    v.add_argument("--ref", help="read the files from this commit instead of the work tree")
    v.add_argument("-o", "--output", choices=["text", "json"], default="text")
    return p.parse_args(argv)


def main() -> int:
    args = parse_args(sys.argv[1:])
    if args.ref == NULL_REF:
        return 0

    try:
        version, problems = check_version(args.base, args.ref)
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
