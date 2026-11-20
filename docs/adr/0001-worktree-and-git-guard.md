# 0001 在仓库外的 worktree 开发，用 PreToolUse hook 强制

状态：采纳

## 背景

agent 在主目录直接切分支或在 `main` 上提交，会打乱用户正在用的工作区，也会绕过 PR 和 CI。只写在 CLAUDE.md 里，agent 可能不遵守。

## 决策

- 每个新功能在仓库目录之外建 worktree：`git worktree add ../skills-repo-<feature> -b <feature> origin/main`。
- `.claude/hooks/git_guard.py`（PreToolUse，Bash）拒绝：在当前目录 `git switch` / `git checkout <branch>`、在 `main` 上产生提交（commit、merge、cherry-pick、revert、am；`--ff-only`、`--squash`、`--no-commit`、`--abort` 等不产生提交的放行，`--skip` 会继续提交后面的序列，不放行）、push 到 `main`（含在 `main` 上 `git push origin HEAD`）、用 `--no-verify` / `SKIP=` / `core.hooksPath`（含 `GIT_CONFIG_*` 环境变量）跳过 git hook。
- `git checkout <path>` 恢复文件不算切分支：只有一个参数、且它是已存在的路径而不是 commit 时放行；`--`、`-p` 和 `<tree-ish> <path>` 形式都放行。
- 分支按命令实际运行的目录判断，跟随同一条命令里的 `cd`、`pushd`/`popd`、`( ... )` 子 shell、`env -C` 和 `git -C`。
- 透过常见包装解析：`env VAR=x`、`command`、`exec`、`nohup`、`time`、`env -S`（含它们自己的选项）、`bash -c '...'`（及 `sh`、`zsh`；前面的 `SKIP=` 等变量会带进去）。展开 git 别名，`!` 开头的 shell 别名按 shell 命令检查。
- heredoc 正文从命令里切出来，正文里的撇号不影响解析。喂给 shell 的正文（`bash <<EOF`、`cat <<EOF | bash`）按脚本检查，用的是这个 shell 所在的目录和它前面的变量；喂给其他命令的正文（`cat > file`、`gh pr create --body-file -`、`git commit -F -`）是数据，不检查。
- 跳过 shell 注释：`#` 只在词首时算注释，`a#b`、URL 里的 `#` 不算。
- hook 读不了输入或检查本身出错时拒绝（fail closed），拒绝理由里带上错误。命令或喂给 shell 的 heredoc 正文解析不了（引号不配对）且提到 `git` / `gh` 时也拒绝；不提到的交给 shell 自己报错。

## 后果

- 拦截只对 Claude Code 的 Bash 工具生效；人在终端里操作不受限。
- 用 `export SKIP=...` 再单独 push、`eval`、脚本文件里的 git 命令拦不住，靠 CLAUDE.md 约束。
- hook 出错会拦下所有 Bash 命令，修 hook 时要先跑 `.claude/hooks/test_git_guard.py`。

## 验证
