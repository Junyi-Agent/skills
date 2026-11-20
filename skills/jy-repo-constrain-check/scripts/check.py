#!/usr/bin/env python3
"""Collect facts about a repo's workflow constraints for jy-repo-constrain-check.

Read-only: never writes, never runs hooks or tests. Stdlib only.

Usage: check.py [repo_path] [--no-remote]
Exits 2 on an unknown argument.

Prints one markdown section per check with a preliminary verdict:
  PASS / WARN / FAIL   decided from the facts alone
  JUDGE                facts found, the model must read the snippets and decide
  N/A                  check does not apply (item 8 on an app repo)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "vendor",
    "dist",
    "build",
    "target",
    "__pycache__",
    ".tox",
    "third_party",
    ".mypy_cache",
    ".ruff_cache",
    ".next",
}
MAX_DEPTH = 4
MAX_FILE = 200_000


# ---------- helpers ----------


def walk(root: Path, max_depth: int = MAX_DEPTH):
    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        d = Path(dirpath)
        dirnames[:] = [n for n in dirnames if n not in SKIP_DIRS and not n.endswith("-workspace")]
        if len(d.parts) - root_depth >= max_depth:
            dirnames[:] = []
        for f in filenames:
            yield d / f


def files_under(d: Path) -> list[Path]:
    """Files below d, skipping SKIP_DIRS (e.g. __pycache__) and compiled .pyc files."""
    return [
        f
        for f in d.rglob("*")
        if f.is_file()
        and f.suffix != ".pyc"
        and not SKIP_DIRS.intersection(f.relative_to(d).parts[:-1])
    ]


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


def check_adr(root: Path) -> Section:
    s = Section(1, "ADR，状态区分提议 / 采纳 / 已验证")
    adr_dirs, adr_files = set(), []
    for p in walk(root):
        if p.suffix.lower() != ".md":
            continue
        in_adr_dir = any(part.lower() in ADR_DIR_NAMES for part in p.relative_to(root).parts[:-1])
        if in_adr_dir or ADR_FILE.match(p.name):
            adr_files.append(p)
            if in_adr_dir:
                adr_dirs.add(p.parent)
    if not adr_files:
        s.facts.append("没有找到 ADR（找过 adr/ decisions/ 等目录和 ADR-NNN*.md 文件）")
        return s

    docs = [p for p in adr_files if not re.match(r"(?i)(readme|index|template)", p.name)]
    meta = [p for p in adr_files if p not in docs]
    s.facts.append(
        f"ADR 目录：{', '.join(sorted(rel(root, d) for d in adr_dirs)) or '无（散落文件）'}；ADR {len(docs)} 篇，说明/模板 {len(meta)} 个"
    )

    statuses: dict[str, list[str]] = {}
    no_status = []
    for p in docs:
        lines = read(p).splitlines()
        found = None
        for i, line in enumerate(lines[:60]):
            m = STATUS_LINE.match(line)
            if m:
                found = m.group(2).strip()
                break
            if STATUS_HEAD.match(line):
                nxt = next((ln.strip() for ln in lines[i + 1 : i + 5] if ln.strip()), "")
                found = nxt
                break
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


def installed_hooks(hd: Path | None) -> list[Path]:
    if not hd or not hd.is_dir():
        return []
    return sorted(
        p
        for p in hd.iterdir()
        if p.is_file() and not p.name.endswith(".sample") and os.access(p, os.X_OK)
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
    s.facts.append(f"已安装（可执行、非 .sample）的 hook：{', '.join(names) if names else '无'}")

    install_refs = []
    for name in [
        "README.md",
        "CONTRIBUTING.md",
        "Makefile",
        "justfile",
        "package.json",
        "CLAUDE.md",
        "AGENTS.md",
    ]:
        p = root / name
        if p.exists():
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


FORMATTERS = re.compile(
    r"(?i)\b(black|ruff-format|ruff format|isort|autopep8|yapf|prettier|biome[- ]format|"
    r"gofmt|goimports|gofumpt|rustfmt|cargo fmt|clang-format|shfmt|stylua|ktlint|swiftformat|"
    r"terraform fmt|taplo|end-of-file-fixer|trailing-whitespace)\b"
)
LINTERS = re.compile(
    r"(?i)\b(ruff(?![- ]format)|flake8|pylint|mypy|pyright|basedpyright|eslint|biome(?![- ]format)|"
    r"golangci-lint|go vet|staticcheck|clippy|shellcheck|hadolint|markdownlint|tsc|stylelint|"
    r"rubocop|ktlint|swiftlint|tflint|yamllint|actionlint)\b"
)
# Evidence only: the model reads the snippet and decides. shellcheck's `--severity` is not
# listed: `style` is its default and every other level reports less.
STRICT = re.compile(
    r"(?i)--strict|strict\s*=\s*true|typeCheckingMode\s*[:=]\s*\"?strict|"
    r"^\s*(extend-)?select\s*=\s*\[|--enable[= ]all|(^|\s)-o\s*all\b|"
    r"--max-warnings[= ]0|-D\s*warnings|--deny[= ]warnings|"
    r"disallow_untyped_defs|warn_unused_ignores|--exit-non-zero-on-fix|\"strict\"\s*:\s*true|"
    r"--fail-on|--error|"
    r"enable-all:\s*true|default:\s*all|require-explanation:\s*true|require-specific:\s*true|"
    r"\"recommended\"\s*:\s*true|plugin:@typescript-eslint/strict|strictTypeChecked"
)
# `|| true` only counts when it follows a check command on the same line
WEAK = re.compile(
    r"(?i)--exit-zero|(pre-commit|lint|ruff|eslint|flake8|mypy|pyright|pylint|clippy|golangci|"
    r"shellcheck|pytest|test)[^|\n]*\|\|\s*true|continue-on-error:\s*true|--max-warnings[= ]-1|"
    r"--disable-error-code|ignore_errors\s*=\s*true|SKIP="
)
LINT_CONFIGS = [
    "pyproject.toml",
    "ruff.toml",
    ".ruff.toml",
    "setup.cfg",
    "mypy.ini",
    ".mypy.ini",
    "pyrightconfig.json",
    ".flake8",
    ".pylintrc",
    "tsconfig.json",
    ".eslintrc",
    ".eslintrc.js",
    ".eslintrc.json",
    ".eslintrc.cjs",
    ".eslintrc.yml",
    "eslint.config.js",
    "eslint.config.mjs",
    "eslint.config.cjs",
    "biome.json",
    ".golangci.yml",
    ".golangci.yaml",
    "clippy.toml",
    ".clippy.toml",
    "Cargo.toml",
    ".shellcheckrc",
]


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


def check_fmt_lint(root: Path, hooks: list[Path]) -> Section:
    s = Section(3, "pre-commit 做 formatter + 严格 linter")
    files = hook_texts(root, hooks)
    text = "\n".join(read(f) for f in files)
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

    strict, weak = [], []
    for f in files + [root / c for c in LINT_CONFIGS if (root / c).is_file()]:
        strict += grep(root, f, STRICT, limit=4)
        weak += grep(root, f, WEAK, limit=4)
    s.evidence += [f"严格：{e}" for e in strict[:6]] + [f"放宽：{e}" for e in weak[:6]]

    if not fmts or not lints:
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
    r"(?i)不.{0,6}(切换|checkout|switch|影响).{0,12}(分支|branch)|"
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
CI_LINT = re.compile(
    r"(?i)pre-commit run|pre-commit/action|\blint\b|ruff|eslint|flake8|mypy|pyright|clippy|golangci|shellcheck|biome|fmt --check|--check\b"
)
CI_TEST = re.compile(
    r"(?i)pytest|unittest|\btest_\w+\.py\b|\w+_test\.py\b|\b(npm|pnpm|yarn|bun) (run )?test|cargo test|go test|\bmake test|ctest|jest|vitest|tox\b|nox\b"
)
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

CODE_SUFFIXES = {
    ".py", ".js", ".mjs", ".cjs", ".ts", ".go", ".rs", ".sh", ".bash", ".rb",
    ".java", ".kt", ".swift", ".c", ".cc", ".cpp",
}  # fmt: skip
MAX_CODE_FILES = 400
# (file, pattern): a declared command-line entry point
CLI_DECL = [
    ("pyproject.toml", r"(?m)^\[(project|tool\.poetry)\.scripts\]"),
    ("setup.py", r"console_scripts"),
    ("setup.cfg", r"console_scripts"),
    ("package.json", r'"bin"\s*:'),
    ("Cargo.toml", r"(?m)^\[\[bin\]\]"),
]
# (file, pattern): a package meant to be imported by other programs
LIB_DECL = [
    ("pyproject.toml", r"(?m)^\[project\]|^\[tool\.poetry\]"),
    ("setup.py", r"setup\("),
    ("package.json", r'"(main|exports|module)"\s*:'),
    ("Cargo.toml", r"(?m)^\[lib\]"),
    ("go.mod", r"(?m)^module\s"),
]
# (file, pattern): a user-facing app or long-running service
APP_DECL = [
    (
        "package.json",
        r'"(react|react-dom|vue|next|nuxt|svelte|@sveltejs/kit|@angular/core|'
        r'express|koa|fastify|@nestjs/core|electron|react-native|expo)"\s*:',
    ),
    ("pyproject.toml", r"(?i)\b(django|flask|fastapi|streamlit|gradio)\b"),
    ("requirements.txt", r"(?im)^(django|flask|fastapi|streamlit|gradio)\b"),
    ("Gemfile", r"\brails\b"),
    ("Dockerfile", r"(?m)^\s*EXPOSE\s"),
    ("Procfile", r"(?m)^web:"),
]
APP_PATHS = ["android", "ios", "public/index.html", "src/components", "src/pages", "app/views"]
CLI_CODE = re.compile(
    r"\bargparse\b|\bimport click\b|\bfrom click\b|\btyper\b|\bclap::|cobra\.Command|"
    r"\bflag\.Parse\(|\bcommander\b|\byargs\b|\bOptionParser\b|\bgetopts\b"
)
APP_CODE = re.compile(
    r"\bFlask\(|\bFastAPI\(|\bapp\.listen\(|http\.ListenAndServe|createRoot\(|ReactDOM\.render"
)
UNIX_SIGNALS = [
    ("读 stdin", re.compile(r"sys\.stdin|process\.stdin|os\.Stdin|io::stdin|\bfileinput\b|\bread -r\b")),
    ("写 stderr", re.compile(r"sys\.stderr|console\.error|os\.Stderr|eprintln!|>&2")),
    ("显式退出码", re.compile(r"sys\.exit\(|SystemExit|process\.exit\(|os\.Exit\(|process::exit|\bexit [0-9]")),
    ("机器可读输出", re.compile(r"--json|json\.dumps?\(|JSON\.stringify|serde_json|--porcelain|--format\b")),
    ("判断是否终端", re.compile(r"isatty|isTTY|NO_COLOR|IsTerminal|\batty\b|\[ -t [012] \]")),
    ("交互提问", re.compile(
        r"\binput\(|inquirer|readline\.question|click\.(prompt|confirm)|Confirm\.ask|\bread -p\b"
    )),
]  # fmt: skip


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
    s.verdict = "JUDGE"
    s.judge += [
        "先定类型：主要给终端或其他程序用的是工具类；面向终端用户的应用或服务是 app 类，写“不适用”",
        "工具类逐条核对 SKILL.md 第 8 项：单一职责、stdin/stdout/stderr 分工、机器可读输出、"
        "非终端不带颜色、退出码、安静、不强制交互",
        "“交互提问”命中的地方，确认有参数或 stdin 能跳过提问",
    ]
    return s


# ---------- main ----------


def main() -> None:
    argv = sys.argv[1:]
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return
    unknown = [a for a in argv if a.startswith("-") and a != "--no-remote"]
    args = [a for a in argv if not a.startswith("-")]
    if unknown or len(args) > 1:
        bad = " ".join(unknown or args[1:])
        print(f"check.py: unexpected argument: {bad}", file=sys.stderr)
        print("Usage: check.py [repo_path] [--no-remote]", file=sys.stderr)
        sys.exit(2)
    root = Path(args[0] if args else ".").resolve()
    remote = "--no-remote" not in argv
    top = git(root, "rev-parse", "--show-toplevel")
    if top:
        root = Path(top)
    hooks = installed_hooks(hooks_dir(root)) if top else []
    sections = [
        check_adr(root),
        check_git_precommit(root, hooks),
        check_fmt_lint(root, hooks),
        check_agent_gate(root, hooks),
        check_worktree_doc(root),
        check_enforcement(root, hooks),
        check_ci(root, remote),
        check_unix(root),
    ]
    print(f"# 流程约束检查：{root}\n")
    print("| # | 检查项 | 初判 |\n|---|---|---|")
    for s in sections:
        print(f"| {s.num} | {s.title} | {s.verdict} |")
    print()
    for s in sections:
        print(s.render())


if __name__ == "__main__":
    main()
