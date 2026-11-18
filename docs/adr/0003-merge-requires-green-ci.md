# 0003 合并 PR 前要求 CI 检查全部通过

状态：采纳

## 背景

仓库是私有的，GitHub 免费版开不了分支保护，`main` 无法设置必需状态检查。

## 决策

`.claude/hooks/git_guard.py` 拦截 `gh pr merge`：先跑 `gh pr checks <PR>`，退出码为 0（全部通过）才放行；检查失败、进行中或没有检查都拒绝。通过 `gh api` 调 `/pulls/<n>/merge` 直接拒绝。

## 后果

- 只约束 agent 通过 Bash 合并；在 GitHub 网页上点合并不受限。
- 仓库改为 public 或升级 GitHub Pro 后，应改用分支保护的必需检查，本条改为「已取代」。

## 验证
