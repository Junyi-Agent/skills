# 0001 在仓库外的 worktree 开发，用 PreToolUse hook 强制

状态：采纳

## 背景

agent 在主目录直接切分支或在 `main` 上提交，会打乱用户正在用的工作区，也会绕过 PR 和 CI。只写在 CLAUDE.md 里，agent 可能不遵守。

## 决策

- 每个新功能在仓库目录之外建 worktree：`git worktree add ../skills-repo-<feature> -b <feature> origin/main`。
- `.claude/hooks/git_guard.py`（PreToolUse，Bash）拒绝：在当前目录 `git switch` / `git checkout <branch>`、在 `main` 上产生提交（commit、merge、cherry-pick、revert、am；`--ff-only`、`--abort` 等不产生提交的放行）、push 到 `main`（含在 `main` 上 `git push origin HEAD`）、用 `--no-verify` / `SKIP=` / `core.hooksPath` 跳过 git hook。
- `git checkout <path>` 恢复文件不算切分支：只有一个参数、且它是已存在的路径而不是 commit 时放行；`--`、`-p` 和 `<tree-ish> <path>` 形式都放行。
- 分支按命令实际运行的目录判断，跟随同一条命令里的 `cd`、`( ... )` 子 shell 和 `git -C`。
- 透过常见包装解析：`env VAR=x`、`command`、`exec`、`nohup`、`time`、`bash -c '...'`（及 `sh`、`zsh`）。
- hook 读不了输入或检查本身出错时拒绝（fail closed），拒绝理由里带上错误。

## 后果

- 拦截只对 Claude Code 的 Bash 工具生效；人在终端里操作不受限。
- 用 `export SKIP=...` 再单独 push、`eval`、脚本文件里的 git 命令拦不住，靠 CLAUDE.md 约束。
- hook 出错会拦下所有 Bash 命令，修 hook 时要先跑 `.claude/hooks/test_git_guard.py`。

## 验证
