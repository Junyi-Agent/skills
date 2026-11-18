# 0001 在仓库外的 worktree 开发，用 PreToolUse hook 强制

状态：采纳

## 背景

agent 在主目录直接切分支或在 `main` 上提交，会打乱用户正在用的工作区，也会绕过 PR 和 CI。只写在 CLAUDE.md 里，agent 可能不遵守。

## 决策

- 每个新功能在仓库目录之外建 worktree：`git worktree add ../skills-repo-<feature> -b <feature> origin/main`。
- `.claude/hooks/git_guard.py`（PreToolUse，Bash）拒绝：在当前目录 `git switch` / `git checkout <branch>`、在 `main` 上 commit、push 到 `main`、用 `--no-verify` / `SKIP=` / `core.hooksPath` 跳过 git hook。
- 分支按命令实际运行的目录判断，跟随同一条命令里的 `cd` 和 `git -C`。

## 后果

- 拦截只对 Claude Code 的 Bash 工具生效；人在终端里操作不受限。
- 用 `export SKIP=...` 再单独 push 的写法拦不住，靠 CLAUDE.md 约束。

## 验证
