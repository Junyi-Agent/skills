#!/usr/bin/env python3
"""Collect facts about a repo's workflow constraints for jy-repo-constrain-check.

Read-only: never writes, never runs hooks or tests. Stdlib only.

Usage: check.py get checks [NUM ...] [--repo PATH] [-o text|json] [--no-remote]
Exits 2 on a bad argument or when --repo is not a directory.

Prints one markdown section per check (or, with -o json, one JSON object with a
"sections" list) with a preliminary verdict:
  PASS / WARN / FAIL   decided from the facts alone
  JUDGE                facts found, the model must read the snippets and decide
  N/A                  check does not apply (item 8 on an app repo)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

RULES_DIR = Path(__file__).resolve().parent.parent / "rules"
# Merge order: list fields keep it (evidence order depends on it), "common" goes last.
LANGS = ["python", "javascript", "go", "rust", "swift", "kotlin", "other", "common"]


def load_rules(rules_dir: Path = RULES_DIR, langs: list[str] = LANGS) -> dict[str, list]:
    """Concatenate each field of rules/<lang>/rules.json over langs, in order."""
    merged: dict[str, list] = {}
    for lang in langs:
        data = json.loads((rules_dir / lang / "rules.json").read_text(encoding="utf-8"))
        for key, value in data.items():
            merged.setdefault(key, []).extend(value)
    return merged


def alternation(parts: list[str], prefix: str = "(?i)", wrap: bool = False) -> re.Pattern:
    """One regex matching any of parts; wrap puts them in a \\b(...)\\b capture group."""
    body = "|".join(dict.fromkeys(parts))
    return re.compile(prefix + (rf"\b({body})\b" if wrap else body))


def detect_languages(root: Path) -> list[str]:
    """Languages (other than common) whose detect files, globs or suffixes occur in root."""
    rules = {
        lang: json.loads((RULES_DIR / lang / "rules.json").read_text(encoding="utf-8"))
        for lang in LANGS
    }
    suffixes = {p.suffix for p in walk(root, max_depth=6)}  # as deep as code_files()
    found = []
    for lang in LANGS[:-1]:
        d = rules[lang].get("detect", {})
        if (
            any((root / f).is_file() for f in d.get("files", []))
            or any(next(root.glob(g), None) for g in d.get("globs", []))
            or suffixes.intersection(d.get("suffixes", []))
        ):
            found.append(lang)
    return found


def use_rules(langs: list[str]) -> None:
    """Rebind the rule globals to the merge of langs (plus common, always last)."""
    global RULES, SKIP_DIRS, FORMATTERS, LINTERS, STRICT, WEAK, WEAK_IN_CONFIG, LINT_CONFIGS
    global CI_LINT, CI_TEST, CODE_SUFFIXES, CLI_DECL, LIB_DECL, APP_DECL, APP_PATHS, APP_GLOBS
    global CLI_CODE, APP_CODE
    # Scan scope and tool names stay on for every language: turning them off only hides
    # real things (node_modules gets scanned, a clang-format hook is not seen).
    every = load_rules()
    SKIP_DIRS = set(every["skip_dirs"])
    CODE_SUFFIXES = set(every["code_suffixes"])
    FORMATTERS = alternation(every["formatters"], wrap=True)
    LINTERS = alternation(every["linters"], wrap=True)
    LINT_CONFIGS = list(dict.fromkeys(every["lint_configs"]))
    # Patterns that misfire on another language's code or config follow the detected languages.
    RULES = load_rules(langs=[lang for lang in LANGS if lang in langs or lang == "common"])
    # Evidence only: the model reads the snippet and decides. shellcheck's `--severity` is not
    # listed: `style` is its default and every other level reports less.
    STRICT = alternation(RULES["strict"])
    # `|| true` only counts when it follows a check command on the same line
    WEAK = alternation(RULES["weak"])
    # (files, pattern): weakening only inside these top-level config files, e.g. mypy's
    # `ignore_errors = true`, which in code is shutil.rmtree's flag
    WEAK_IN_CONFIG = [
        (set(w["files"]), re.compile(w["pattern"])) for w in RULES.get("weak_in_config", [])
    ]
    CI_LINT = alternation(RULES["ci_lint"])
    CI_TEST = alternation(RULES["ci_test"])
    # (file, pattern): a declared command-line entry point / importable package / app or service
    CLI_DECL = [tuple(d) for d in RULES.get("cli_decl", [])]
    LIB_DECL = [tuple(d) for d in RULES.get("lib_decl", [])]
    APP_DECL = [tuple(d) for d in RULES.get("app_decl", [])]
    APP_PATHS = list(dict.fromkeys(RULES.get("app_paths", [])))
    # Xcode projects at the top level or one level down (Pods/ etc. are skipped)
    APP_GLOBS = list(dict.fromkeys(RULES.get("app_globs", [])))
    CLI_CODE = alternation(RULES.get("cli_code", []), prefix="")
    APP_CODE = alternation(RULES.get("app_code", []), prefix="")


use_rules(LANGS)
MAX_DEPTH = 4
MAX_FILE = 200_000


# ---------- helpers ----------


def skip_dir(d: Path, name: str) -> bool:
    """SKIP_DIRS, eval workspaces, and other repos inside this one (submodules, nested clones):
    their root has a `.git` file or directory."""
    return name in SKIP_DIRS or name.endswith("-workspace") or (d / name / ".git").exists()


def walk(root: Path, max_depth: int = MAX_DEPTH):
    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        d = Path(dirpath)
        dirnames[:] = [n for n in dirnames if not skip_dir(d, n)]
        if len(d.parts) - root_depth >= max_depth:
            dirnames[:] = []
        for f in filenames:
            yield d / f


def files_under(d: Path) -> list[Path]:
    """Files below d, skipping what walk() skips and compiled .pyc files."""
    return sorted(f for f in walk(d, max_depth=99) if f.suffix != ".pyc" and f.is_file())


def read(p: Path) -> str:
    try:
        if p.stat().st_size > MAX_FILE:
            return ""
        return p.read_text(errors="replace")
    except OSError:
        return ""


def git(root: Path, *args: str) -> str:
    try:
        r = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=5
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def rel(root: Path, p: Path) -> str:
    try:
        return str(p.relative_to(root))
    except ValueError:
        return str(p)


def grep(root: Path, p: Path, pattern: re.Pattern, ctx: int = 0, limit: int = 8) -> list[str]:
    lines = read(p).splitlines()
    out = []
    for i, line in enumerate(lines):
        if pattern.search(line):
            lo, hi = max(0, i - ctx), min(len(lines), i + ctx + 1)
            snippet = " ⏎ ".join(s.strip() for s in lines[lo:hi] if s.strip())
            out.append(f"`{rel(root, p)}:{i + 1}` {snippet[:240]}")
            if len(out) >= limit:
                break
    return out


class Section:
    def __init__(self, num: int, title: str):
        self.num, self.title = num, title
        self.verdict = "FAIL"
        self.facts: list[str] = []
        self.evidence: list[str] = []
        self.judge: list[str] = []

    def render(self) -> str:
        out = [f"## {self.num}. {self.title} — {self.verdict}", ""]
        out += [f"- {f}" for f in self.facts]
        if self.evidence:
            out += ["", "证据："] + [f"  - {e}" for e in dict.fromkeys(self.evidence)]
        if self.judge:
            out += ["", "需要判断："] + [f"  - {j}" for j in self.judge]
        return "\n".join(out) + "\n"

    def to_dict(self) -> dict:
        return {
            "num": self.num,
            "title": self.title,
            "verdict": self.verdict,
            "facts": self.facts,
            "evidence": list(dict.fromkeys(self.evidence)),
            "judge": self.judge,
        }


# ---------- 1. ADR ----------

ADR_DIR_NAMES = {
    "adr",
    "adrs",
    "decisions",
    "decision-records",
    "architecture-decisions",
    "decision-log",
}
ADR_FILE = re.compile(r"(?i)^(adr[-_ ]?\d+|\d{3,4}[-_]).*\.md$")
STATUS_LINE = re.compile(r"(?i)^\s*[-*]?\s*\**(status|状态)\**\s*[:：]\s*(.+)$")
STATUS_HEAD = re.compile(r"(?i)^#+\s*(status|状态)\s*$")
# 三种状态：提议 = 只写了文档；采纳 = 允许实现（可能已实现）；已验证 = 人类测试过代码并确认
VERIFIED = re.compile(
    r"(?i)(?<!un)(verified|validated|tested)|(?<![未待])(已验证|已测试|实测|测试过)"
)
ACCEPTED = re.compile(r"(?i)accepted|adopted|采纳")
PROPOSED = re.compile(r"(?i)proposed|draft|提议|草案|待定")
DEFINE_VERIFIED = re.compile(
    r"(?i)(已验证|verified).{0,40}(人|human|测试|tested)|(人|human).{0,20}(测试|tested).{0,40}(已验证|verified)"
)


def find_adrs(root: Path) -> tuple[set[Path], list[Path]]:
    """(ADR directories, every .md in them or named like an ADR, incl. README/template)."""
    adr_dirs, adr_files = set(), []
    for p in walk(root):
        if p.suffix.lower() != ".md":
            continue
        in_adr_dir = any(part.lower() in ADR_DIR_NAMES for part in p.relative_to(root).parts[:-1])
        if in_adr_dir or ADR_FILE.match(p.name):
            adr_files.append(p)
            if in_adr_dir:
                adr_dirs.add(p.parent)
    return adr_dirs, adr_files


def is_adr_doc(p: Path) -> bool:
    return not re.match(r"(?i)(readme|index|template)", p.name)


def adr_status(p: Path) -> str | None:
    lines = read(p).splitlines()
    for i, line in enumerate(lines[:60]):
        m = STATUS_LINE.match(line)
        if m:
            return m.group(2).strip()
        if STATUS_HEAD.match(line):
            return next((ln.strip() for ln in lines[i + 1 : i + 5] if ln.strip()), "")
    return None


def check_adr(root: Path) -> Section:
    s = Section(1, "ADR，状态区分提议 / 采纳 / 已验证")
    adr_dirs, adr_files = find_adrs(root)
    if not adr_files:
        s.facts.append("没有找到 ADR（找过 adr/ decisions/ 等目录和 ADR-NNN*.md 文件）")
        return s

    docs = [p for p in adr_files if is_adr_doc(p)]
    meta = [p for p in adr_files if p not in docs]
    if adr_dirs:
        where = "ADR 目录：" + ", ".join(sorted(rel(root, d) for d in adr_dirs))
    else:
        parents = sorted({rel(root, p.parent) for p in adr_files})
        where = "ADR 目录：无，ADR 文件所在目录：" + ", ".join(parents)
    s.facts.append(f"{where}；ADR {len(docs)} 篇，说明/模板 {len(meta)} 个")

    statuses: dict[str, list[str]] = {}
    no_status = []
    for p in docs:
        found = adr_status(p)
        if found:
            statuses.setdefault(found[:60], []).append(rel(root, p))
        else:
            no_status.append(rel(root, p))

    if statuses:
        s.facts.append(
            "状态取值：" + "；".join(f"`{k}` ×{len(v)}" for k, v in sorted(statuses.items()))
        )
    if no_status:
        s.facts.append(
            f"没有状态字段的 ADR {len(no_status)} 篇：{', '.join(no_status[:5])}{' …' if len(no_status) > 5 else ''}"
        )

    has_verified = any(VERIFIED.search(k) for k in statuses)
    has_accepted = any(ACCEPTED.search(k) and not VERIFIED.search(k) for k in statuses)
    has_proposed = any(PROPOSED.search(k) for k in statuses)
    for p in adr_files:
        s.evidence += grep(root, p, DEFINE_VERIFIED, limit=3)
    s.evidence = s.evidence[:6]

    if not statuses:
        s.verdict = "FAIL"
        s.facts.append("所有 ADR 都没有状态字段，无法区分提议 / 采纳 / 已验证")
    elif not has_verified and not s.evidence:
        s.verdict = "WARN"
        seen = [n for n, f in (("提议", has_proposed), ("采纳", has_accepted)) if f]
        s.facts.append(
            f"状态只有{' / '.join(seen) or '其他取值'}，没有“已验证”（人类实测确认）这一档"
        )
    else:
        s.verdict = "JUDGE"
        s.judge.append(
            "确认三种状态都有定义：提议 = 只写了文档，采纳 = 允许实现，"
            "已验证 = 人类测试了代码并确认；“已验证”不能由 agent 自己标"
        )
    return s


# ---------- 2/3/4 hooks ----------


def hooks_dir(root: Path) -> Path | None:
    hp = git(root, "config", "--get", "core.hooksPath")
    if hp:
        p = Path(hp).expanduser()
        return p if p.is_absolute() else root / p
    gp = git(root, "rev-parse", "--git-common-dir")
    if not gp:
        return None
    p = Path(gp)
    return (p if p.is_absolute() else root / p) / "hooks"


# git runs only files named after a hook (githooks(5)); other files in the hooks directory,
# such as Claude Code hook scripts and their tests, are not git hooks.
GIT_HOOK_NAMES = {
    "applypatch-msg",
    "pre-applypatch",
    "post-applypatch",
    "pre-commit",
    "pre-merge-commit",
    "prepare-commit-msg",
    "commit-msg",
    "post-commit",
    "pre-rebase",
    "post-checkout",
    "post-merge",
    "pre-push",
    "pre-receive",
    "update",
    "proc-receive",
    "post-receive",
    "post-update",
    "reference-transaction",
    "push-to-checkout",
    "pre-auto-gc",
    "post-rewrite",
    "sendemail-validate",
    "fsmonitor-watchman",
    "p4-changelist",
    "p4-prepare-changelist",
    "p4-post-changelist",
    "p4-pre-submit",
    "post-index-change",
}


def installed_hooks(hd: Path | None) -> list[Path]:
    if not hd or not hd.is_dir():
        return []
    return sorted(
        p
        for p in hd.iterdir()
        if p.name in GIT_HOOK_NAMES and p.is_file() and os.access(p, os.X_OK)
    )


HOOK_CONFIGS = [
    ".pre-commit-config.yaml",
    ".pre-commit-config.yml",
    "lefthook.yml",
    ".lefthook.yml",
    "lefthook.yaml",
    ".lefthook.yaml",
    ".husky",
    ".githooks",
    "githooks",
    ".git-hooks",
]
INSTALL_STEP = re.compile(
    r"pre-commit install|lefthook install|husky( install)?\b|core\.hooksPath|install[-_]hooks"
)


def missing_stages(root: Path, hooks: list[Path]) -> list[str]:
    """Hook stages declared in .pre-commit-config.yaml (besides pre-commit) that are not installed."""
    text = read(root / ".pre-commit-config.yaml") or read(root / ".pre-commit-config.yml")
    declared = set(
        re.findall(
            r"\b(pre-push|pre-merge-commit|commit-msg|post-merge|post-checkout|prepare-commit-msg)\b",
            text,
        )
    )
    installed = {h.name for h in hooks}
    return sorted(declared - installed)


def check_git_precommit(root: Path, hooks: list[Path]) -> Section:
    s = Section(2, "git 仓库，且安装了 pre-commit")
    top = git(root, "rev-parse", "--show-toplevel")
    if not top:
        s.facts.append("不是 git 仓库")
        return s
    s.facts.append(
        f"git 仓库，根目录 `{top}`，当前分支 `{git(root, 'branch', '--show-current') or '(detached)'}`"
    )
    configs = [c for c in HOOK_CONFIGS if (root / c).exists()]
    s.facts.append(f"hook 配置：{', '.join(configs) if configs else '无'}")
    hp = git(root, "config", "--get", "core.hooksPath")
    if hp:
        s.facts.append(f"core.hooksPath = `{hp}`")
    names = [h.name for h in hooks]
    s.facts.append(
        f"已安装（可执行、按 git hook 命名）的 hook：{', '.join(names) if names else '无'}"
    )

    install_refs = []
    docs = [*DOCS, "Makefile", "justfile", "package.json", ".pre-commit-config.yaml"]
    docs += [".pre-commit-config.yml"]
    places = [root / n for n in docs] + sorted((root / "docs").glob("*.md"))
    for p in dict.fromkeys(places):
        if p.is_file():
            install_refs += grep(root, p, INSTALL_STEP, limit=2)
    for p in (root / "scripts").glob("*") if (root / "scripts").is_dir() else []:
        if p.is_file():
            install_refs += grep(root, p, INSTALL_STEP, limit=1)
    s.evidence += install_refs[:5]

    miss = missing_stages(root, hooks)
    if miss:
        s.facts.append(
            f"配置里声明了 {', '.join(miss)} 阶段，但本机没有安装对应 hook（需 `pre-commit install --hook-type {miss[0]}`）"
        )
    if "pre-commit" in names:
        s.verdict = "PASS" if install_refs and not miss else "WARN"
        if not install_refs:
            s.facts.append(
                "本机装了 hook，但 repo 里没有安装步骤（新 clone 或重建 worktree 的机器不会自动有）"
            )
    elif configs:
        s.verdict = "WARN"
        s.facts.append("有 hook 配置但本机没有安装 pre-commit hook")
    return s


def hook_texts(root: Path, hooks: list[Path]) -> list[Path]:
    files = []
    for c in HOOK_CONFIGS:
        p = root / c
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files += [f for f in files_under(p) if f.parent.name != "_"]
    files += hooks
    return files


# Repo files a hook runs, so checks kept in `scripts/lint.py` or a Makefile are seen too.
TOPLEVEL = re.compile(r"\$\(git rev-parse --show-toplevel\)/")
INTERP_CALL = re.compile(
    r"(?:^|[\s;&|(`\"'])(?:(?:uv|poetry|pipenv|pdm|hatch)\s+run\s+)?"
    r"(?:(?:exec|python[\d.]*|sh|bash|zsh|node|ruby|perl)\s+)+(?:-[\w-]+\s+)*[\"']?"
    r"(\.?/?[\w.-]+(?:/[\w.-]+)*)"
)
BARE_PATH = re.compile(
    r"(?:^|[\s;&|(:\"'])(\.?/?(?:[\w.-]+/)+[\w.-]+\.(?:py|sh|bash|zsh|js|mjs|cjs|ts|rb|pl))\b"
)
TASK_CALL = re.compile(r"(?:^|[\s;&|(\"'])(make|just|npm run|pnpm run|yarn run|task)\b")
TASK_FILES = {
    "make": "Makefile",
    "just": "justfile",
    "npm run": "package.json",
    "pnpm run": "package.json",
    "yarn run": "package.json",
    "task": "Taskfile.yml",
}


def called_files(root: Path, files: list[Path], depth: int = 2) -> tuple[list[Path], list[str]]:
    """Repo files that `files` run (followed `depth` levels), and called paths that are missing."""
    top = root.resolve()
    seen = {f.resolve() for f in files}
    found, missing, frontier = [], [], list(files)
    for _ in range(depth):
        nxt = []
        for f in frontier:
            text = TOPLEVEL.sub("", read(f))
            calls = [(m.group(1), True) for m in INTERP_CALL.finditer(text)]
            calls += [(m.group(1), False) for m in BARE_PATH.finditer(text)]
            calls += [(TASK_FILES[m.group(1)], False) for m in TASK_CALL.finditer(text)]
            for c, is_call in calls:
                p = (root / c.removeprefix("./")).resolve()
                if TEST_FILE.search(p.name):  # a hook running unit tests is not a lint runner
                    continue
                if p.is_file() and p.is_relative_to(top):
                    if p not in seen:
                        seen.add(p)
                        found.append(root / p.relative_to(top))
                        nxt.append(found[-1])
                elif is_call and "/" in c and not p.exists():
                    missing.append(c)
        frontier = nxt
    return found, list(dict.fromkeys(missing))


def check_fmt_lint(root: Path, hooks: list[Path]) -> Section:
    s = Section(3, "pre-commit 做 formatter + 严格 linter")
    files = hook_texts(root, hooks)
    text = "\n".join(read(f) for f in files)
    called, missing = [], []
    if not (FORMATTERS.search(text) and LINTERS.search(text)):
        # Only when the hooks alone lack one: followed scripts hold tool names as data too.
        called, missing = called_files(root, files)
        files += called
        text += "\n" + "\n".join(read(f) for f in called)
    if not text.strip():
        s.facts.append("没有任何 hook 配置或已安装的 hook")
        return s
    fmts = sorted(
        {m.group(1).lower() for m in FORMATTERS.finditer(text)}
        - {"end-of-file-fixer", "trailing-whitespace"}
    )
    lints = sorted({m.group(1).lower() for m in LINTERS.finditer(text)})
    s.facts.append(f"formatter：{', '.join(fmts) or '无'}")
    s.facts.append(f"linter：{', '.join(lints) or '无'}")
    if called:
        s.facts.append("跟进了 hook 调用的文件：" + ", ".join(rel(root, f) for f in called))
        for f in called:
            hits = grep(root, f, FORMATTERS, limit=2) + grep(root, f, LINTERS, limit=2)
            s.evidence += [f"调用：{e}" for e in hits]

    strict, weak = [], []
    for f in files + [root / c for c in LINT_CONFIGS if (root / c).is_file()]:
        strict += grep(root, f, STRICT, limit=4)
        weak += grep(root, f, WEAK, limit=4)
        for names, pattern in WEAK_IN_CONFIG:
            if f.parent == root and f.name in names:
                weak += grep(root, f, pattern, limit=4)
    s.evidence += [f"严格：{e}" for e in strict[:6]] + [f"放宽：{e}" for e in weak[:6]]

    if (not fmts or not lints) and missing:
        s.verdict = "JUDGE"
        s.judge.append(
            f"hook 调用了 {', '.join(f'`{m}`' for m in missing)}，脚本在 repo 里找不到，"
            "请读 hook 判断 formatter / linter 在哪里跑"
        )
    elif not fmts or not lints:
        s.verdict = "FAIL"
    elif weak:
        s.verdict = "WARN"
        s.facts.append("有放宽信号，见证据")
    elif strict:
        s.verdict = "JUDGE"
        s.judge.append(
            "确认严格配置作用在 pre-commit 实际跑的那个 linter 上，且 hook 失败会阻止提交"
        )
    else:
        s.verdict = "WARN"
        s.facts.append(
            "formatter 和 linter 都有，但没看到严格模式的配置（如 ruff select ALL、mypy --strict、eslint --max-warnings 0、clippy -D warnings）"
        )
    return s


AGENT_CALL = re.compile(
    r"(?i)\bclaude\s+(-p|--print)\b|\bclaude\b.*--output-format|\bcodex\s+exec\b|"
    r"[\"']claude[\"']\s*,|"  # argv list: ["claude", "-p", ...]
    r"[\"']codex[\"']\s*,\s*[\"']exec[\"']|"  # argv list: ["codex", "exec", ...]
    r"\bwhich\(\s*[\"'](claude|codex)[\"']\s*\)|"  # exe = shutil.which("claude"); [exe, "-p", ...]
    r"\bgemini\s+-p\b|anthropic|subagent|claude_agent_sdk|\bAgent\("
)
QUORUM = re.compile(r"(?i)-ge\s+2|>=\s*2|2\s*/\s*3|2 of 3|quorum|majority|approv|多数|同意|通过票")
SQUASH = re.compile(r"(?i)squash|merge\s+--squash|worktree")
SCRIPT_DIRS = [
    "scripts",
    "script",
    "tools",
    "bin",
    "hooks",
    ".githooks",
    "githooks",
    ".claude/hooks",
    "ci",
]


def check_agent_gate(root: Path, hooks: list[Path]) -> Section:
    s = Section(4, "squash merge 时 3 个不同方向的 agent 审查，≥2 同意才允许 push")
    candidates = hook_texts(root, hooks)
    for d in SCRIPT_DIRS:
        p = root / d
        if p.is_dir():
            candidates += [f for f in files_under(p) if f.stat().st_size < MAX_FILE]

    seen, hits = set(), []
    for f in candidates:
        if f in seen:
            continue
        seen.add(f)
        a = grep(root, f, AGENT_CALL, limit=3)
        if a:
            hits.append((f, a, grep(root, f, QUORUM, limit=3), grep(root, f, SQUASH, limit=2)))

    stages = [
        h.name
        for h in hooks
        if h.name in ("pre-push", "pre-merge-commit", "post-merge", "pre-commit", "commit-msg")
    ]
    s.facts.append(f"已安装的相关 hook 阶段：{', '.join(stages) or '无'}")
    miss = missing_stages(root, hooks)
    if miss:
        s.facts.append(
            f"⚠ 配置声明了 {', '.join(miss)} 阶段但本机未安装，挂在这些阶段上的门禁实际不会运行"
        )

    if not hits:
        s.facts.append("hook 和脚本目录里没有调用 agent（claude -p / codex exec / SDK 等）的地方")
        return s

    for f, a, q, sq in hits:
        s.facts.append(
            f"`{rel(root, f)}`：agent 调用 {len(a)} 处，quorum 信号 {len(q)} 处，squash/worktree 信号 {len(sq)} 处"
        )
        s.evidence += a + q + sq
    s.evidence = s.evidence[:12]
    s.verdict = "JUDGE"
    s.judge += [
        "读上面文件里的 agent 调用：是否 3 个审查方向不同（如正确性 / 设计 / 测试）",
        "是否有 ≥2 同意的判定，且不满足时非零退出、真的阻止 push",
        "是否由已安装的 hook（pre-push 等）触发，且覆盖 worktree squash merge 的流程",
    ]
    return s


# ---------- 5. worktree rule in docs ----------

DOCS = [
    "README.md",
    "CLAUDE.md",
    "AGENTS.md",
    ".claude/CLAUDE.md",
    "CONTRIBUTING.md",
    "docs/CONTRIBUTING.md",
]
WORKTREE = re.compile(r"(?i)worktree")
OUTSIDE = re.compile(
    r"\.\./|~/|/tmp|outside|sibling|当前目录之外|仓库之外|repo 之外|上级目录|外部目录", re.I
)
KEEP_BRANCH = re.compile(
    r"(?i)不.{0,6}(切换?|checkout|switch|影响).{0,12}(分支|branch)|"
    r"(do not|don't|never|must not).{0,20}(checkout|switch|change).{0,20}branch|"
    r"(分支|branch).{0,12}不.{0,4}(变|动|受影响)"
)


def check_worktree_doc(root: Path) -> Section:
    s = Section(
        5, "README / CLAUDE.md / AGENTS.md 规定新功能在当前目录之外的 worktree 里做，不改当前分支"
    )
    present = [d for d in DOCS if (root / d).is_file()]
    s.facts.append(f"存在的文档：{', '.join(present) or '无'}")
    missing = [d for d in ("README.md", "CLAUDE.md", "AGENTS.md") if d not in present]
    if missing:
        s.facts.append(f"缺少：{', '.join(missing)}")

    wt, out, keep = [], [], []
    for d in present:
        p = root / d
        wt += grep(root, p, WORKTREE, ctx=1, limit=5)
        out += grep(
            root,
            p,
            re.compile(f"worktree.*({OUTSIDE.pattern})|({OUTSIDE.pattern}).*worktree", re.I),
            limit=3,
        )
        keep += grep(root, p, KEEP_BRANCH, limit=3)

    s.evidence += wt[:8]
    s.facts.append(
        f"提到 worktree {len(wt)} 处；worktree 位置在目录外的信号 {len(out)} 处；不改当前分支的信号 {len(keep)} 处"
    )
    s.evidence += [f"分支：{k}" for k in keep[:3]]

    if not wt:
        s.verdict = "FAIL"
    elif out and keep:
        s.verdict = "JUDGE"
        s.judge.append(
            "确认规则覆盖“每个新功能”，位置明确在当前目录之外，并明说不在当前目录 checkout/switch 分支"
        )
    else:
        s.verdict = "WARN"
        s.facts.append(
            "提到 worktree，但缺少"
            + "、".join(x for x, ok in (("位置在目录外", out), ("不改当前分支", keep)) if not ok)
            + "的规定"
        )
    return s


# ---------- 6. enforcement scripts ----------

BLOCK = re.compile(
    r"(?i)exit\s+[1-9]|sys\.exit\([1-9]|\"decision\"\s*:\s*\"(block|deny)\"|permissionDecision|"
    r"process\.exit\([1-9]"
)
BRANCH_GUARD = re.compile(r"(?i)(main|master)")
FLOW_NAME = re.compile(
    r"(?i)worktree|feature|start|finish|ship|merge|review|guard|check|gate|enforce|land|pr\b"
)


def check_enforcement(root: Path, hooks: list[Path]) -> Section:
    s = Section(6, "用脚本强制约束流程（防止 agent 自主偏移）")
    found = False
    for name in (".claude/settings.json", ".claude/settings.local.json"):
        p = root / name
        if p.is_file():
            try:
                cfg = json.loads(read(p))
            except json.JSONDecodeError:
                cfg = {}
            ev = sorted((cfg.get("hooks") or {}).keys())
            deny = (cfg.get("permissions") or {}).get("deny") or []
            if ev or deny:
                found = True
                s.facts.append(
                    f"`{name}`：Claude Code hooks {ev or '无'}，deny 规则 {len(deny)} 条"
                )

    ch = root / ".claude" / "hooks"
    if ch.is_dir():
        files = files_under(ch)
        if files:
            found = True
            s.facts.append(f"`.claude/hooks/` 下 {len(files)} 个脚本")
            for f in files[:6]:
                s.evidence += grep(root, f, BLOCK, limit=2)

    for h in hooks:
        txt = read(h)
        if BRANCH_GUARD.search(txt) and BLOCK.search(txt):
            found = True
            s.evidence += [
                f"git hook `{h.name}` 里有分支判断 + 非零退出",
                *grep(root, h, BRANCH_GUARD, limit=2),
            ]

    flow = []
    for d in ("scripts", "script", "bin", "tools"):
        p = root / d
        if p.is_dir():
            flow += [f for f in p.iterdir() if f.is_file() and FLOW_NAME.search(f.name)]

    for mk in ("Makefile", "justfile", "Taskfile.yml"):
        p = root / mk
        if p.is_file():
            t = grep(
                root,
                p,
                re.compile(
                    r"(?i)^[\w-]*(worktree|feature|ship|land|merge|review|check|gate)[\w-]*\s*:"
                ),
                limit=5,
            )
            if t:
                s.evidence += t
                found = True

    if flow:
        found = True
        s.facts.append("流程脚本：" + ", ".join(rel(root, f) for f in flow[:10]))
        for f in flow[:6]:
            b = grep(root, f, BLOCK, limit=1)
            s.evidence += b
    s.evidence = s.evidence[:12]

    if not found:
        s.facts.append(
            "没有 Claude Code hooks/deny 规则、阻断型 git hook 或流程脚本，流程只靠文档约定"
        )
        return s
    s.verdict = "JUDGE"
    s.judge += [
        "区分“会阻断”的约束（PreToolUse 返回 block/deny、hook 非零退出）和“只是辅助”的脚本",
        "对照第 5 项和第 4 项的流程：worktree、审查、push 这几步是否都有脚本兜底，哪一步只靠文档",
    ]
    return s


# ---------- 7. CI/CD ----------

CI_PATHS = [
    ".github/workflows",
    ".gitlab-ci.yml",
    "Jenkinsfile",
    ".circleci/config.yml",
    "azure-pipelines.yml",
    ".buildkite",
    "bitbucket-pipelines.yml",
    ".drone.yml",
    ".woodpecker.yml",
    ".travis.yml",
    "cloudbuild.yaml",
    "cloudbuild.yml",
]
CI_PRECOMMIT = re.compile(r"(?i)pre-commit run|pre-commit/action")
CI_DEPLOY = re.compile(
    r"(?i)deploy|release|publish|docker push|gh release|twine upload|npm publish|helm upgrade|kubectl apply"
)
# PR trigger in CI systems other than GitHub Actions (GitLab rules, Azure `pr:` ...)
CI_PR = re.compile(r"(?i)pull_request|merge_request|\bpr:")
# Top-level `on:` of a GitHub Actions workflow (YAML 1.1 parsers read a bare `on` as true)
GHA_ON = re.compile(r"""^(?:on|"on"|'on'|true)\s*:\s*([^#]*?)\s*(?:#.*)?$""")


def gha_triggers(text: str) -> tuple[dict[str, str], int]:
    """Events under the top-level `on:` of a workflow, each with its filter lines.

    Only the `on:` block is read, so `push: true` in a step or `pull_request` in an `if:`
    is not taken for a trigger. Also returns the 1-based line of `on:` (0 if missing).
    """
    lines = text.splitlines()
    for start, line in enumerate(lines):  # noqa: B007 (start is used after the loop)
        m = GHA_ON.match(line)
        if m:
            break
    else:
        return {}, 0

    inline = m.group(1)
    if inline.startswith("{"):  # flow mapping: keep the keys, filters are not parsed
        return dict.fromkeys(re.findall(r"([A-Za-z_]+)\s*:", inline), ""), start + 1
    if inline:  # `on: push` or `on: [push, pull_request]`
        return dict.fromkeys(re.findall(r"[A-Za-z_]+", inline), ""), start + 1

    events: dict[str, str] = {}
    indent, current = None, None
    for line in lines[start + 1 :]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        ind = len(line) - len(line.lstrip())
        # YAML allows the list under `on:` at the key's own indent: `on:\n- push`
        zero_item = ind == 0 and line.startswith("-") and indent in (None, 0)
        if (ind == 0 and not zero_item) or (indent is not None and ind < indent):
            break
        if indent is None:
            indent = ind
        if ind == indent:
            m = re.match(r"\s*(?:-\s*)?([A-Za-z_]+)", line)
            current = m.group(1) if m else None
            if current:
                events[current] = ""
        elif current:
            events[current] += line.strip() + "\n"
    return events, start + 1


def gha_main_push(events: dict[str, str]) -> bool:
    """A push trigger that can fire for branch pushes, not only for tags."""
    if "push" not in events:
        return False
    filters = events["push"]
    branches = re.search(r"(?m)^branches(-ignore)?\s*:", filters)
    tags = re.search(r"(?m)^tags(-ignore)?\s*:", filters)
    return bool(branches or not tags)


def check_ci(root: Path, remote: bool) -> Section:
    s = Section(7, "CI/CD 定义")
    files = []
    for c in CI_PATHS:
        p = root / c
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files += sorted(
                f for f in p.rglob("*") if f.is_file() and f.suffix in (".yml", ".yaml")
            )
    if not files:
        s.facts.append("没有 CI 配置")
        return s
    s.facts.append("CI 文件：" + ", ".join(rel(root, f) for f in files[:10]))
    text = "\n".join(read(f) for f in files)

    pc = root / ".pre-commit-config.yaml"
    if CI_PRECOMMIT.search(text) and pc.is_file():
        # CI runs the pre-commit config, so its lint and test hooks run in CI too
        text += "\n" + read(pc)
        s.facts.append("CI 跑 pre-commit，.pre-commit-config.yaml 里的检查一并计入")

    has = {
        k: bool(r.search(text))
        for k, r in (
            ("lint", CI_LINT),
            ("测试", CI_TEST),
            ("部署/发布", CI_DEPLOY),
        )
    }

    # GitHub Actions: read the `on:` block. Other CI systems build every push by default.
    gha = [f for f in files if ".github" in f.parts]
    other = [f for f in files if f not in gha]
    has["PR 触发"] = any(CI_PR.search(read(f)) for f in other)
    has["push 触发"] = bool(other)
    for f in gha:
        events, line = gha_triggers(read(f))
        has["PR 触发"] |= bool({"pull_request", "pull_request_target"} & events.keys())
        has["push 触发"] |= gha_main_push(events)
        if line:
            push = " ".join(events.get("push", "").split())
            s.evidence.append(
                f"触发：`{rel(root, f)}:{line}` {', '.join(events) or '（空）'}"
                + (f"；push 过滤：{push[:120]}" if push else "")
            )

    order = ("PR 触发", "push 触发", "lint", "测试", "部署/发布")
    s.facts.append("；".join(f"{k}：{'有' if has[k] else '无'}" for k in order))

    weak = []
    for f in files:
        weak += grep(root, f, WEAK, limit=3)
    s.evidence += [f"放宽：{w}" for w in weak[:5]]
    s.evidence += [f"lint：{e}" for f in files for e in grep(root, f, CI_LINT, limit=1)][:3]
    s.evidence += [f"测试：{e}" for f in files for e in grep(root, f, CI_TEST, limit=1)][:3]

    if remote:
        prot = branch_protection(root)
        if prot:
            s.facts.append(prot)

    core = has["PR 触发"] and has["push 触发"] and has["lint"] and has["测试"]
    s.verdict = "JUDGE" if core and not weak else "WARN"
    if not has["部署/发布"]:
        s.facts.append("没有部署/发布步骤；如果这个项目需要发布，这是缺口")
    s.judge += [
        "看“触发”证据，确认 PR 和主分支 push 都会触发（push 只过滤 tag、或 branches 不含主分支的不算）",
        "确认 CI 跑的检查和 pre-commit 一致（最好直接 `pre-commit run --all-files`），以及主分支是否要求这些检查通过才能合并",
    ]
    return s


def branch_protection(root: Path) -> str:
    url = git(root, "remote", "get-url", "origin")
    m = re.search(r"github\.com[:/]([^/]+)/(.+?)(?:\.git)?/?$", url)
    if not m or not shutil.which("gh"):
        return ""

    branch = (
        git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").removeprefix("origin/")
        or "main"
    )

    try:
        r = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{m.group(1)}/{m.group(2)}/branches/{branch}/protection/required_status_checks",
            ],
            capture_output=True,
            text=True,
            timeout=6,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "分支保护：查询超时"

    if r.returncode != 0:
        return f"分支保护：`{branch}` 没有要求状态检查（或无权限查询）"
    try:
        checks = json.loads(r.stdout).get("contexts", [])
    except json.JSONDecodeError:
        checks = []
    return f"分支保护：`{branch}` 要求的检查 {checks or '（空）'}"


# ---------- 8. Unix philosophy (tool repos only) ----------

MAX_CODE_FILES = 400

UNIX_SIGNALS = [
    ("读 stdin", re.compile(r"sys\.stdin|process\.stdin|os\.Stdin|io::stdin|\bfileinput\b|\bread -r\b")),
    ("写 stderr", re.compile(r"sys\.stderr|console\.error|os\.Stderr|eprintln!|>&2")),
    ("显式退出码", re.compile(r"sys\.exit\(|SystemExit|process\.exit\(|os\.Exit\(|process::exit|\bexit [0-9]")),
    ("机器可读输出", re.compile(r"--json|json\.dumps?\(|JSON\.stringify|serde_json|--porcelain|--format\b")),
    ("判断是否终端", re.compile(r"isatty|isTTY|NO_COLOR|IsTerminal|\batty\b|\[ -t [012] \]")),
    ("交互提问", re.compile(
        r"\binput\(|inquirer|readline\.question|click\.(prompt|confirm)|Confirm\.ask|\bread -p\b"
    )),
    ("子命令", re.compile(
        r"add_subparsers|\.add_parser\(|click\.group|@\w+\.group\(|add_typer|\.AddCommand\(|"
        r"\bSubcommand\b|\.command\(\s*(name\s*=\s*)?['\"]|cobra\.Command\b"
    )),
    ("-o/--output 选输出格式", re.compile(r"""['"]-o['"]|(?<![\w-])--output(?![\w-])""")),
]  # fmt: skip
# Names of declared subcommands (argparse, click/typer, commander/yargs, cobra), to judge
# whether they read as <verb> <resource>.
SUBCOMMAND_NAME = re.compile(
    r"""\.add_parser\(\s*['"]([\w-]+)|\.command\(\s*(?:name\s*=\s*)?['"]([\w-]+)|"""
    r"""\bUse:\s*"([\w-]+)"""
)
# An ADR exempting a CLI from the kubectl-style rule: mentions an exemption and the CLI style.
EXEMPT = re.compile(r"(?i)豁免|exempt")
EXEMPT_TOPIC = re.compile(r"(?i)kubectl|子命令|subcommand|动词|\bverb|\bCLI\b|命令行")


TEST_FILE = re.compile(r"(^test_|_test\.|\.(test|spec)\.|^conftest\.py$)")
TEST_DIRS = {"test", "tests", "__tests__", "spec", "testdata", "fixtures", "fixture"}
SELF = Path(__file__).resolve()


def code_files(root: Path) -> list[Path]:
    """Source files of the repo itself: no tests, fixtures, or this script (its regexes match)."""
    out = []
    for p in walk(root, max_depth=6):
        if TEST_FILE.search(p.name) or TEST_DIRS.intersection(p.relative_to(root).parts[:-1]):
            continue
        if p.resolve() == SELF:
            continue
        if p.suffix in CODE_SUFFIXES or (not p.suffix and read(p).startswith("#!")):
            out.append(p)
            if len(out) >= MAX_CODE_FILES:
                break
    return out


def decl_hits(root: Path, decls: list[tuple[str, str]]) -> list[str]:
    hits = []
    for name, pat in decls:
        p = root / name
        if p.is_file():
            hits += grep(root, p, re.compile(pat), limit=1)
    return hits


def check_unix(root: Path) -> Section:
    s = Section(8, "工具类 repo 符合 Unix 设计哲学（app 类不适用）")
    code = code_files(root)
    cli = decl_hits(root, CLI_DECL)
    lib = decl_hits(root, LIB_DECL)
    app = decl_hits(root, APP_DECL)
    app += [f"`{p}` 存在" for p in APP_PATHS if (root / p).exists()]
    app += [
        f"`{rel(root, p)}` 存在"
        for g in APP_GLOBS
        for p in sorted(root.glob(g))
        if p.is_dir() and not SKIP_DIRS.intersection(p.relative_to(root).parts)
    ]
    scripts = [f for f in code if read(f).startswith("#!")]
    cli_code, app_code = [], []
    for f in code:
        cli_code += grep(root, f, CLI_CODE, limit=1)
        app_code += grep(root, f, APP_CODE, limit=1)

    tool_side = bool(cli or lib or scripts or cli_code)
    app_side = bool(app or app_code)
    kind = {
        (True, False): "工具类",
        (False, True): "app 类",
        (True, True): "两类都有",
        (False, False): "不确定",
    }[(tool_side, app_side)]
    s.facts.append(
        f"类型初判：{kind}（CLI 入口声明 {len(cli)}，库声明 {len(lib)}，带 shebang 的脚本 "
        f"{len(scripts)}，参数解析 {len(cli_code)} 处；app 信号 {len(app) + len(app_code)}）"
    )
    s.facts.append(
        f"扫描了 {len(code)} 个代码文件" + ("（已达上限）" if len(code) >= MAX_CODE_FILES else "")
    )
    s.evidence += [f"工具：{e}" for e in (cli + lib + cli_code)[:4]]
    s.evidence += [f"app：{e}" for e in (app + app_code)[:4]]

    if kind == "app 类":
        s.verdict = "N/A"
        s.judge.append("确认是面向终端用户的 app 或服务；是的话第 8 项写“不适用”")
        return s

    counts = []
    for label, pat in UNIX_SIGNALS:
        hits = [h for f in code for h in grep(root, f, pat, limit=1)]
        counts.append(f"{label} {len(hits)}")
        s.evidence += [f"{label}：{h}" for h in hits[:2]]
    s.facts.append("代码里的 Unix 信号（命中文件数）：" + "，".join(counts))
    s.evidence = s.evidence[:20]

    names = []
    for f in code:
        for m in SUBCOMMAND_NAME.finditer(read(f)):
            names.append(next(g for g in m.groups() if g))
    names = list(dict.fromkeys(names))
    s.facts.append(
        "声明的子命令：" + ("、".join(f"`{n}`" for n in names[:30]) if names else "没找到")
    )

    exempt = []
    for p in find_adrs(root)[1]:
        text = read(p)
        if is_adr_doc(p) and EXEMPT.search(text) and EXEMPT_TOPIC.search(text):
            exempt.append(f"`{rel(root, p)}`（状态：{adr_status(p) or '没写'}）")
            s.evidence += grep(root, p, EXEMPT, limit=2)
    s.facts.append("提到 CLI 风格豁免的 ADR：" + ("，".join(exempt) if exempt else "没有"))

    s.verdict = "JUDGE"
    s.judge += [
        "先定类型：主要给终端或其他程序用的是工具类；面向终端用户的应用或服务是 app 类，写“不适用”",
        "工具类逐条核对 SKILL.md 第 8 项：kubectl 式命令结构、单一职责、stdin/stdout/stderr 分工、"
        "机器可读输出、非终端不带颜色、退出码、安静、不强制交互",
        "kubectl 式结构是必须项：每个命令行入口都是 `<tool> <动词> <资源> [名字] [flags]`，"
        "动词跨资源复用，输出格式用 -o/--output 选；不满足的入口要有状态为采纳或已验证、"
        "写明豁免对象、理由和批准人的 ADR，否则第 8 项不能给 ✅",
        "“交互提问”命中的地方，确认有参数或 stdin 能跳过提问",
    ]
    return s


# ---------- main ----------


CHECKS = range(1, 9)


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="check.py",
        allow_abbrev=False,
        description="Collect facts about a repo's workflow constraints (read-only).",
    )
    verbs = p.add_subparsers(dest="verb", required=True, metavar="VERB")
    get = verbs.add_parser("get", allow_abbrev=False, help="run checks and print the results")
    resources = get.add_subparsers(dest="resource", required=True, metavar="RESOURCE")
    checks = resources.add_parser(
        "checks",
        allow_abbrev=False,
        help="the eight workflow checks",
        description="Run the eight workflow checks.",
    )
    checks.add_argument(
        "names", nargs="*", type=int, metavar="NUM", help="check numbers 1-8 (default: all)"
    )
    checks.add_argument("--repo", default=".", help="repo to check (default: .)")
    checks.add_argument("-o", "--output", choices=["text", "json"], default="text")
    checks.add_argument(
        "--no-remote", action="store_true", help="skip the GitHub branch protection query"
    )

    args = p.parse_args(argv)

    bad = [n for n in args.names if n not in CHECKS]
    if bad:
        checks.error(f"no such check: {', '.join(map(str, bad))} (choose from 1-8)")
    if not Path(args.repo).is_dir():
        checks.error(f"not a directory: {Path(args.repo).resolve()}")
    return args


def main() -> None:
    args = parse_args(sys.argv[1:])
    root = Path(args.repo).resolve()
    top = git(root, "rev-parse", "--show-toplevel")
    if top:
        root = Path(top)
    hooks = installed_hooks(hooks_dir(root)) if top else []
    langs = detect_languages(root)
    # Nothing recognised: keep every language's rules, as before per-language rules existed
    use_rules(langs or LANGS)

    run = {
        1: lambda: check_adr(root),
        2: lambda: check_git_precommit(root, hooks),
        3: lambda: check_fmt_lint(root, hooks),
        4: lambda: check_agent_gate(root, hooks),
        5: lambda: check_worktree_doc(root),
        6: lambda: check_enforcement(root, hooks),
        7: lambda: check_ci(root, not args.no_remote),
        8: lambda: check_unix(root),
    }

    sections = [run[n]() for n in sorted(set(args.names or CHECKS))]

    if args.output == "json":
        out = {"root": str(root), "languages": langs, "sections": [s.to_dict() for s in sections]}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    print(f"# 流程约束检查：{root}\n")
    print("识别到的语言：" + (", ".join(langs) if langs else "无（使用全部语言的规则）") + "\n")
    print("| # | 检查项 | 初判 |\n|---|---|---|")
    for s in sections:
        print(f"| {s.num} | {s.title} | {s.verdict} |")
    print()
    for s in sections:
        print(s.render())


if __name__ == "__main__":
    main()
