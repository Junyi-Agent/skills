---
name: "jy-repo-constrain-check"
description: "快速只读检查一个 repo 有没有对开发流程的约束：ADR 状态是否区分提议/采纳/已验证、git + pre-commit 是否安装、pre-commit 是否有 formatter 和严格 linter、合并/push 前是否有 3 个 agent 审查且 ≥2 同意的门禁、文档是否规定新功能在目录外的 worktree 里做、是否用脚本强制流程、CI/CD 是否完善。用户说检查流程约束、constrain check、看看这个 repo 的流程规范、repo 有没有护栏时使用。"
---

# Repo 流程约束检查

只检查、只报告，几分钟内完成。不修改任何文件，不跑测试、不触发 hook，不启动 subagent。

## 第 1 步：跑脚本

```bash
python3 <本 skill 目录>/scripts/check.py <repo 路径>
```

不给路径时用当前目录。脚本只读，一次扫完七项，每项给出初判和带 `文件:行号` 的证据片段：

- PASS / WARN / FAIL：脚本根据事实已能判定
- JUDGE：找到了相关内容，但是否合格要读懂语义，由你判断

默认会用 `gh api` 查一次 GitHub 分支保护（6 秒超时）；离线或不想访问网络时加 `--no-remote`。

## 第 2 步：判定 JUDGE 项

只看脚本给出的证据片段，片段不够时再读对应文件的那几行，不要通读 repo。各项的合格标准：

1. **ADR**：状态字段有这三种，且 repo 里写明了各自含义：
   - **提议**（Proposed）：只写了 ADR 文档
   - **采纳**（Accepted）：允许去实现，可能已经实现
   - **已验证**（Verified）：人类测试了代码，确认了实现的内容

   只有提议/采纳、没有“已验证”这一档时算 WARN（标准 ADR 的 Accepted 只表示同意实现，不代表实测过）。有“已验证”但没写明它要求人类实测时，也算 WARN
2. **pre-commit 安装**：本机已安装，且 repo 里写了安装步骤（新机器、重建的 worktree 能装上）
3. **formatter + 严格 linter**：两类都在 pre-commit 里，linter 开了严格模式，且没有 `--exit-zero`、`|| true` 之类的放宽
4. **agent 审查门禁**：由已安装的 hook 触发、覆盖 worktree squash merge 后的 push；3 个 agent 的审查方向不同；≥2 同意才放行，否则非零退出阻止 push。缺任一条就不是 PASS，写明缺哪条
5. **worktree 规则**：README / CLAUDE.md / AGENTS.md 至少一处明确规定：每个新功能在当前目录之外建 worktree，且不在当前目录切换分支。只说“可以用 worktree”算 WARN；worktree 出现在产品功能介绍里（比如工具本身管理 worktree）不算流程规定
6. **脚本强制**：有会阻断的约束（PreToolUse 返回 block/deny、git hook 非零退出），覆盖第 4、5 项的流程。只有辅助脚本、靠 agent 自觉调用的，算 WARN
7. **CI/CD**：PR 和主分支 push 都触发；跑 lint 和测试，且与 pre-commit 一致；没有放宽；主分支要求检查通过才能合并。需要发布的项目还应有 CD

脚本初判为 PASS/WARN/FAIL 的，一般直接采用；证据明显与初判矛盾时以证据为准，并说明原因。

## 第 3 步：输出

在对话里输出，不写文件（用户要求时才写）：

```
| # | 检查项 | 结论 | 依据 |
|---|---|---|---|
| 1 | ADR 提议/采纳/已验证 | ⚠️ | docs/adr/ 有 6 篇，状态只有 Proposed / Accepted，没有“已验证”这一档 |
...
```

结论用 ✅ / ⚠️ / ❌。表后列出所有 ⚠️ 和 ❌ 的补法，每条一句，具体到文件和要加的内容，按“补上后对防止 agent 偏移最有用”的顺序排。全部 ✅ 时只输出表格。
