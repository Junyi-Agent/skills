# skills-repo

Agent Skills（SKILL.md 格式）。每个顶层 `jy-*` 目录是一个 skill，`.claude/skills/` 下是指向它们的软链接，供本仓库内使用。

## 开发流程

- 每个新功能或修改在仓库目录之外建 worktree 开发：
  `git worktree add ../skills-repo-<feature> -b <feature> origin/main`
- 不在当前目录切换分支（`git checkout <branch>` / `git switch`）。
- 不在 `main` 上提交，不 push 到 `main`。push feature 分支后开 PR，CI 通过后合并。
- 以上规则由 `.claude/hooks/git-guard.sh`（PreToolUse hook）强制执行。

## 本地检查

新机器或新 worktree 先装 hook：

```bash
pre-commit install
```

提交前会跑 ruff format、ruff check、shellcheck、YAML/JSON 校验。手动全量跑：`pre-commit run --all-files`。CI（`.github/workflows/ci.yml`）跑同一套配置。

## 约定

- evals 产物（`*-workspace/`、`evals/results/`）不提交。
- 改 SKILL.md 的 description 会影响触发，改完跑一下对应 skill 的 evals。
