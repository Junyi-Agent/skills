# skills-repo

Agent Skills（SKILL.md 格式）。每个顶层 `jy-*` 目录是一个 skill，`.claude/skills/` 下是指向它们的软链接，供本仓库内使用。

## 开发流程

- 每个新功能或修改在仓库目录之外建 worktree 开发：
  `git worktree add ../skills-repo-<feature> -b <feature> origin/main`
- 不在当前目录切换分支（`git checkout <branch>` / `git switch`）。
- 不在 `main` 上提交，不 push 到 `main`。push feature 分支后开 PR，CI 通过后合并。
- 不用 `--no-verify`、`SKIP=`、`core.hooksPath` 跳过 git hook。
- 以上规则由 `.claude/hooks/git_guard.py`（PreToolUse hook）强制执行；`gh pr merge` 会先跑 `gh pr checks`，检查没全过就拒绝（[[docs/adr/0003-merge-requires-green-ci]]）。

## push 审查门禁

push 时 pre-push hook 跑 `scripts/review_gate.py`：3 个 Claude 分别从正确性、skill 质量、流程与安全审查 `origin/main` 之后的改动，≥2 个同意才放行。被拒时按输出里的 blocking issues 改完再 push。需要本机装好并登录 `claude` CLI。见 [[docs/adr/0002-pre-push-review-gate]]。

## 本地检查

新机器或新 worktree 先装 hook（同时装 pre-commit 和 pre-push 两个阶段）：

```bash
pre-commit install
```

提交前会跑 ruff format、ruff check、shellcheck、YAML/JSON 校验。手动全量跑：`pre-commit run --all-files`。CI（`.github/workflows/ci.yml`）跑同一套配置。

## 约定

- evals 产物（`*-workspace/`、`evals/results/`）不提交。
- 改 SKILL.md 的 description 会影响触发，改完跑一下对应 skill 的 evals。
- 流程或架构决策写 ADR（`docs/adr/`）。状态分三种：提议 = 只写了文档；采纳 = 允许实现，可能已实现；已验证 = 人类测试了代码并确认。agent 最多改到「采纳」，「已验证」只由人类改。
