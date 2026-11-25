# skills-repo

Agent Skills（SKILL.md 格式）。每个 `skills/jy-*` 目录是一个 skill，`.claude/skills/` 下是指向它们的软链接，供本仓库内使用。新增 skill 放进 `skills/`，并在 `.claude/skills/` 加一条 `../../skills/<name>` 软链接、在 README 的 skill 表加一行（`scripts/repo_check.py check skills` 检查）。只给本仓库用的内部 skill（目前是 `jy-repo-maintain`）直接放在 `.claude/skills/<name>/`，不是软链接，README 和安装说明里不提。

## 开发流程

- 每个新功能或修改在仓库目录之外建 worktree 开发：
  `git worktree add ../skills-repo-<feature> -b <feature> origin/main`
- 不在当前目录切换分支（`git checkout <branch>` / `git switch`）。
- 不在 `main` 上提交，不 push 到 `main`。push feature 分支后开 PR，CI 通过后合并。
- 不用 `--no-verify`、`SKIP=`、`core.hooksPath` 跳过 git hook。
- 以上规则由 `.claude/hooks/git_guard.py`（PreToolUse hook）强制执行；`gh pr merge` 会先跑 `gh pr checks`，检查没全过就拒绝（[[docs/adr/0003-merge-requires-green-ci]]）。

## push 审查门禁

push 时 pre-push hook 跑 `scripts/review_gate.py`：3 个 Claude 分别从正确性、skill 质量、流程与安全审查 `origin/main` 之后的改动，≥2 个同意才放行。被拒时按输出里的 blocking issues 改完再 push。需要本机装好并登录 `claude` CLI。见 [[docs/adr/0002-pre-push-review-gate]]。

## 维护

定时维护 loop 已停用，由 jy-dashboard 的「skills-repo 维护」项目组人工接手（[[docs/adr/0007-maintain-loop-stopped]]，取代 [[docs/adr/0006-issue-maintain-loop]]）。issue 打了 `accepted` 才处理；大改动先在 issue 里写方案，确认后再开工。`/jy-repo-maintain` 仍可手动跑一轮：带 `JY_MAINTAIN_LOOP=1` 时 `git_guard.py` 只放行部分 gh 命令，不让加 `accepted` / `approved` 标签，合并前先跑 `maintain.py check pr`。

## 本地检查

新机器或新 worktree 先装 hook（同时装 pre-commit 和 pre-push 两个阶段）：

```bash
pre-commit install
```

提交前会跑 ruff format、ruff check、shellcheck、YAML/JSON 校验、skill 软链接和 ADR 列表检查（`scripts/repo_check.py`）、Markdown 禁用修饰词检查，改到对应文件时还会跑 git guard、ADR guard、审查门禁和各脚本的单元测试。手动全量跑：`pre-commit run --all-files`。CI（`.github/workflows/ci.yml`）跑同一套配置。

## 约定

- evals 产物（`*-workspace/`、`evals/results/`）不提交。
- 改 SKILL.md 的 description 会影响触发，改完跑一下对应 skill 的 evals。
- 改了 `skills/` 下的文件就升 `VERSION` 并在 `CHANGELOG.md` 顶部加一节，X/Y/Z 怎么选见 [[docs/adr/0005-versioning-and-tags]]。push 和 CI 会检查；合并后 CI 自动打 tag，不手动打。
- 流程或架构决策写 ADR（`docs/adr/`）。状态分三种：提议 = 只写了文档；采纳 = 允许实现，可能已实现；已验证 = 人类测试了代码并确认。agent 最多改到「采纳」，「已验证」只由人类改。

## 已知的故意设计

审查（`/jy-repo-review`）不把下列行为当问题上报。

- `git_guard.py` 对已删除文件的 `git checkout <file>` 按切分支拒绝：只有参数是已存在的路径、且不是 commit 时才当作恢复文件放行，拒绝信息给出 `git checkout -- <path>` 的写法（[[docs/adr/0001-worktree-and-git-guard]]）。
- `git_guard.py` 拦不住 `export SKIP=...` 后单独 push、`eval`、脚本文件里的 git 命令，以及别的命令的输出喂给 shell（`cat x.sh | bash`）；`git push --all` 带上 main 由服务端分支保护拦（ADR 0001、0003）。
