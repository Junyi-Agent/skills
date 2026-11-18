# 0003 合并 PR 前要求 CI 检查全部通过

状态：采纳

## 背景

仓库是私有的，GitHub 免费版开不了分支保护，`main` 无法设置必需状态检查。

## 决策

`.claude/hooks/git_guard.py` 拦截 `gh pr merge`：先跑 `gh pr checks <PR>`，退出码为 0（全部通过）才放行；检查失败、进行中或没有检查都拒绝。通过 `gh api` 调 `/pulls/<n>/merge` 直接拒绝。

## 后果

- 只约束 agent 通过 Bash 合并；在 GitHub 网页上点合并不受限。

## 更新（2026-11-18）

服务端分支保护已开启：仓库已改为 public，`main` 开启分支保护，要求 `pre-commit` 检查通过、必须走 PR、禁止 force push 和删除，对管理员同样生效。网页合并也受必需检查约束。`git_guard.py` 的拦截保留，让 agent 在本地就被拒绝，不用等到服务端报错。

## 验证
