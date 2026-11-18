# 0002 push 前 3 个 Claude 审查，≥2 同意才放行

状态：采纳

## 背景

pre-commit 和 CI 只做格式、lint 和单元测试，查不出 SKILL.md 写得含糊、description 触发偏了、改动绕开流程这类问题。

## 决策

- `scripts/review_gate.py` 作为 pre-commit 的 `pre-push` 阶段 hook 运行（`pre-commit install` 会同时装 pre-commit 和 pre-push）。
- 审查范围：`merge-base(origin/main, 被 push 的 ref)..被 push 的 ref`。squash merge 后再 push 也走同一范围。
- 并行跑 3 个 `claude -p`，方向分别是：正确性、skill 质量与文档一致性、流程与安全。只给 Read/Grep/Glob 工具，用 `--json-schema` 要求结构化输出。
- ≥2 个 approve 才 exit 0；报错、超时、输出解析失败都算 reject，否则非零退出阻止 push。
- commit log 和 diff 放在每次随机生成标签的 `<untrusted-…>` 块里，prompt 说明其中的文字只是待审数据，试图引导审查的内容按 blocking issue 处理。
- 本地找不到 `origin/main`（或与之没有共同祖先）时阻止 push，并提示先 `git fetch origin`。
- CI 不跑这个 hook（`pre-commit run --all-files` 只跑 pre-commit 阶段）。

## 后果

- 每次 push 都调用 3 次 Claude，要花 token，等几分钟。
- 机器上没有 `claude` CLI 或没登录时 push 会被拦住。
- 人可以在终端用 `git push --no-verify` 跳过；agent 走 Bash 时被 0001 的 hook 拦住。

## 验证
