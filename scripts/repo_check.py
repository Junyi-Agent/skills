#!/usr/bin/env python3
"""Check that skills and ADRs are registered consistently with the rules in CLAUDE.md.

Usage: repo_check.py check (skills|adrs) [-o text|json]

check skills (CLAUDE.md, README.md):
  - every skills/<name>/ with a SKILL.md has the symlink .claude/skills/<name> -> ../../skills/<name>
  - every entry in .claude/skills/ is such a symlink to an existing skill (none dangling)
  - the frontmatter name in SKILL.md equals the directory name
  - README.md lists the skill as [`<name>`](skills/<name>)

check adrs (docs/adr/README.md):
  - every docs/adr/NNNN-*.md has a line "状态：<提议|采纳|已验证|已取代（见 NNNN）>"
  - the list in docs/adr/README.md has one row per ADR, and its status matches the file
  - an ADR in 已验证 has its 验证 section filled in by a human: 验证人 and a YYYY-MM-DD date

Text output prints nothing on success and the problems on stderr on failure.
Exits 0 when all rules hold, 1 when one fails, 2 on a bad argument.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ADR_FILE = re.compile(r"^\d{4}-.+\.md$")
STATUS_LINE = re.compile(r"^状态：(.+?)\s*$", re.MULTILINE)
STATUS_OK = re.compile(r"^(提议|采纳|已验证|已取代（见 \d{4}）)$")
INDEX_ROW = re.compile(r"^\|\s*\[(\d{4})\]\(([^)]+)\)\s*\|.*\|\s*([^|]+?)\s*\|\s*$", re.MULTILINE)
VERIFY_SECTION = re.compile(r"^## 验证\s*$(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
PLACEHOLDER = "（只由人类填写"
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
FRONTMATTER_NAME = re.compile(r"\A---\n(?:.*\n)*?name:\s*\"?([^\"\n]+?)\"?\s*\n(?:.*\n)*?---\n")


def adr_status(text: str) -> str | None:
    m = STATUS_LINE.search(text)
    return m.group(1) if m else None


def adr_verification(text: str) -> str:
    """The 验证 section without the template's placeholder line."""
    m = VERIFY_SECTION.search(text)
    lines = m.group(1).splitlines() if m else []
    return "\n".join(ln for ln in lines if not ln.strip().startswith(PLACEHOLDER)).strip()


def check_skills(root: Path) -> list[str]:
    problems = []
    skills_dir = root / "skills"
    if not skills_dir.is_dir():  # a repo that copies this script has no skills of its own
        return problems
    links_dir = root / ".claude" / "skills"
    readme = root / "README.md"
    readme_text = readme.read_text() if readme.is_file() else ""
    names = sorted(
        p.name for p in skills_dir.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()
    )

    for name in names:
        link = links_dir / name
        want = f"../../skills/{name}"
        if not link.is_symlink():
            problems.append(
                f".claude/skills/{name} 不存在或不是软链接：运行 "
                f"ln -s {want} .claude/skills/{name}（见 CLAUDE.md）"
            )
        elif (target := str(link.readlink())) != want:
            problems.append(
                f".claude/skills/{name} 指向 {target}，应为 {want}：运行 "
                f"ln -sfn {want} .claude/skills/{name}"
            )

        m = FRONTMATTER_NAME.match((skills_dir / name / "SKILL.md").read_text())
        if not m:
            problems.append(f"skills/{name}/SKILL.md 的 frontmatter 没有 name")
        elif m.group(1) != name:
            problems.append(
                f"skills/{name}/SKILL.md 的 name 是 {m.group(1)!r}，应与目录名 {name!r} 一致"
            )

        if f"[`{name}`](skills/{name})" not in readme_text:
            problems.append(
                f"README.md 的 skill 表里没有 {name}：加一行 | [`{name}`](skills/{name}) | <用途> |"
            )

    if links_dir.is_dir():
        for link in sorted(links_dir.iterdir()):
            if link.name not in names:
                problems.append(
                    f".claude/skills/{link.name} 没有对应的 skills/{link.name}/SKILL.md："
                    "删掉这个软链接，或把 skill 放进 skills/"
                )
    return problems


def check_adrs(root: Path) -> list[str]:
    problems = []
    adr_dir = root / "docs" / "adr"
    index = adr_dir / "README.md"
    files = sorted(p for p in adr_dir.iterdir() if ADR_FILE.match(p.name))

    statuses = {}
    for path in files:
        text = path.read_text()
        status = adr_status(text)
        statuses[path.name] = status
        if status is None or not STATUS_OK.match(status):
            problems.append(
                f"docs/adr/{path.name}：状态行应为「状态：提议 / 采纳 / 已验证 / 已取代（见 NNNN）」，"
                f"现在是 {status!r}"
            )
        if status == "已验证":
            body = adr_verification(text)
            if "验证人" not in body or not DATE.search(body):
                problems.append(
                    f"docs/adr/{path.name} 状态是已验证，但「验证」一节没有验证人和日期。"
                    "已验证只能由人类改，并由人类填写这一节；agent 把状态改回采纳，请用户测试"
                )

    rows: dict[str, list[tuple[str, str]]] = {}
    for num, href, status in INDEX_ROW.findall(index.read_text() if index.is_file() else ""):
        rows.setdefault(href, []).append((num, status))

    for path in files:
        entries = rows.pop(path.name, [])
        if not entries:
            problems.append(
                f"docs/adr/README.md 的列表里没有 {path.name}：加一行 "
                f"| [{path.name[:4]}]({path.name}) | <决策> | {statuses[path.name]} |"
            )
            continue
        if len(entries) > 1:
            problems.append(f"docs/adr/README.md 的列表里 {path.name} 出现了 {len(entries)} 次")
        num, status = entries[0]
        if num != path.name[:4]:
            problems.append(f"docs/adr/README.md：{path.name} 的编号写成了 {num}")
        if status != statuses[path.name]:
            problems.append(
                f"docs/adr/README.md：{path.name} 的状态写成「{status}」，"
                f"文件里是「{statuses[path.name]}」，以文件为准改列表"
            )
    for href in rows:
        problems.append(f"docs/adr/README.md 的列表指向不存在的文件 {href}")
    return problems


CHECKS = {"skills": check_skills, "adrs": check_adrs}


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="repo_check.py", allow_abbrev=False)
    verbs = p.add_subparsers(dest="verb", required=True, metavar="VERB")
    check = verbs.add_parser("check", allow_abbrev=False, help="check repo consistency")
    resources = check.add_subparsers(dest="resource", required=True, metavar="RESOURCE")
    for name, help_text in [
        ("skills", "skill symlinks, SKILL.md names and the README table"),
        ("adrs", "ADR status lines, the ADR list and human-only verification"),
    ]:
        r = resources.add_parser(name, allow_abbrev=False, help=help_text)
        r.add_argument("-o", "--output", choices=["text", "json"], default="text")
    return p.parse_args(argv)


def main() -> int:
    args = parse_args(sys.argv[1:])
    problems = CHECKS[args.resource](ROOT)

    if args.output == "json":
        print(json.dumps({"resource": args.resource, "ok": not problems, "problems": problems}))
    for problem in problems:
        print(f"repo_check.py: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
