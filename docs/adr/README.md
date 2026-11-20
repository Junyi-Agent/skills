# ADR（架构决策记录）

每个影响流程或架构的决策写一篇 `NNNN-<slug>.md`，从 [`template.md`](template.md) 复制。

## 状态

| 状态 | 含义 | 谁来改 |
|---|---|---|
| 提议 | 只写了 ADR 文档 | 写 ADR 的人或 agent |
| 采纳 | 允许去实现，可能已经实现 | 用户决定后，人或 agent 改 |
| 已验证 | 人类测试了代码，确认了实现的内容 | 只能由人类改，并填写「验证」一节 |

- agent 不能把状态改成「已验证」，也不能代填「验证」一节。实现完成后停在「采纳」，请用户测试。
- 被取代的 ADR 状态写「已取代（见 NNNN）」，不删除。

## 列表

| # | 决策 | 状态 |
|---|---|---|
| [0001](0001-worktree-and-git-guard.md) | 在仓库外的 worktree 开发，用 PreToolUse hook 强制 | 采纳 |
| [0002](0002-pre-push-review-gate.md) | push 前 3 个 Claude 审查，≥2 同意才放行 | 采纳 |
| [0003](0003-merge-requires-green-ci.md) | 合并 PR 前要求 CI 检查全部通过 | 采纳 |
| [0004](0004-kubectl-style-cli.md) | 命令行入口用 kubectl 式结构，豁免要人类批准并写 ADR | 采纳 |
