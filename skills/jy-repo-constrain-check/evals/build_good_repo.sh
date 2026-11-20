#!/usr/bin/env bash
# Builds, in the current (empty) directory, a repo that should pass all eight checks except
# one planted gap: the agent review gate is declared on the pre-push stage, but only the
# pre-commit hook is installed. Expected script verdicts: 2 = WARN (missing pre-push hook),
# 4 = JUDGE with an "未安装" warning, 8 = JUDGE with type "工具类" (shell scripts only) and
# docs/adr/0004-cli-exempt.md listed as an exemption ADR (状态：采纳),
# everything else JUDGE or PASS.
#
#   mkdir /tmp/good && cd /tmp/good && bash build_good_repo.sh && python3 ../scripts/check.py . --no-remote
set -euo pipefail
git init -q -b main .
mkdir -p docs/adr scripts .github/workflows .claude/hooks
echo '状态定义：提议 = 只写了 ADR 文档；采纳 = 允许去实现，可能已经实现；已验证 = 人类测试了代码并确认了实现的内容。' > docs/adr/README.md
printf '# ADR-001 用 ruff\n\n状态：已验证\n' > docs/adr/0001-ruff.md
printf '# ADR-002 换存储\n\n状态：提议\n' > docs/adr/0002-store.md
printf '# ADR-003 加缓存\n\n状态：采纳\n' > docs/adr/0003-cache.md
printf '# ADR-004 豁免 scripts/new-worktree.sh 的 kubectl 式命令结构\n\n状态：采纳\n\n理由：只建一个 worktree，没有资源可分。批准人：维护者（人类），2026-11-20。\n' > docs/adr/0004-cli-exempt.md
cat > .pre-commit-config.yaml <<'Y'
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.0
    hooks:
      - id: ruff
        args: [--exit-non-zero-on-fix]
      - id: ruff-format
  - repo: local
    hooks:
      - id: agent-review
        name: agent review gate
        entry: scripts/review_gate.sh
        language: script
        stages: [pre-push]
Y
printf '[tool.ruff.lint]\nselect = ["ALL"]\n' > pyproject.toml
cat > scripts/review_gate.sh <<'S'
#!/usr/bin/env bash
# squash merge 的 worktree 分支在 push 前由 3 个 agent 审查
ok=0
for role in correctness design tests; do
  claude -p "review as $role" | grep -q APPROVE && ok=$((ok+1))
done
[ "$ok" -ge 2 ] || { echo "only $ok approvals"; exit 1; }
S
# shellcheck disable=SC2016  # written literally into the fixture
printf '#!/usr/bin/env bash\ngit worktree add "../$(basename $PWD)-$1" -b "$1" || exit 1\n' > scripts/new-worktree.sh
printf '{"hooks":{"PreToolUse":[{"matcher":"Bash","hooks":[{"type":"command","command":".claude/hooks/no-switch.sh"}]}]}}' > .claude/settings.json
printf '#!/bin/sh\ngrep -q "git checkout\\|git switch" && { echo "{\\"decision\\": \\"block\\"}"; exit 2; }\n' > .claude/hooks/no-switch.sh
# shellcheck disable=SC2016  # written literally into the fixture
echo '每个新功能都在当前目录之外的 worktree 里做（`scripts/new-worktree.sh` 建到 ../ 下），不要在当前目录切换分支。' > CLAUDE.md
cp CLAUDE.md AGENTS.md
# shellcheck disable=SC2016  # written literally into the fixture
printf '# good\n\n安装 hook：`pre-commit install --hook-type pre-commit --hook-type pre-push`\n' > README.md
cat > .github/workflows/ci.yml <<'Y'
on:
  pull_request:
  push:
    branches: [main]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pipx run pre-commit run --all-files
      - run: python -m pytest
Y
printf '#!/bin/sh\npre-commit run\n' > .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit scripts/*.sh .claude/hooks/*.sh
git add -A
git -c core.hooksPath=/dev/null -c user.name=t -c user.email=t@example.invalid commit -qm init
