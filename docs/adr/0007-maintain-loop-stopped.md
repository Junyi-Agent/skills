# 0007 停用定时维护 loop，改由维护项目组接手

状态：采纳

## 背景

[0006](0006-issue-maintain-loop.md) 决定用本机 launchd 每 6 小时跑一轮 `/jy-repo-maintain`。这个定时 loop 已经停用：2026-11-25 在本机执行 `launchctl list | grep -i maintain` 和 `ls ~/Library/LaunchAgents | grep -i maintain`，都没有输出。仓库维护改由 jy-dashboard 上的「skills-repo 维护」项目组负责（维护者「自驱」，维护工程师「严证」）。

0006 和 CLAUDE.md 仍写着 launchd 每 6 小时跑一轮，后来的 agent 读了会以为有 loop 在处理 issue。

## 决策

- 不再用 launchd 定时跑 `/jy-repo-maintain`。0006 状态改成「已取代（见 0007）」，正文保留，便于追溯。
- 由维护项目组的成员处理 issue。他们在仓库外的 worktree 里开发，开 PR，CI 全绿后合并，遵守 CLAUDE.md 的开发流程（0001、0002、0003、0005 不变）。
- issue 仍然要打 `accepted` 才处理。大改动（例如改 skill 的目录结构）先在 issue 里写方案，项目维护者确认后再开工。
- 保留 `jy-repo-maintain` skill、`.claude/maintain.json` 和 `git_guard.py` 的 loop 模式（`JY_MAINTAIN_LOOP=1`），需要时可以手动跑一轮。

## 后果

- 文档和实际做法一致，agent 不会再等一个不存在的 loop。
- issue 处理的及时程度取决于项目组什么时候被唤醒，没有固定周期。
- 项目组成员不带 `JY_MAINTAIN_LOOP=1`，git_guard 按普通模式检查；loop 模式的额外限制（gh 白名单、不能加标签、合并前 `maintain.py check pr`）对他们不生效。靠 PR + CI + 服务端分支保护约束。

## 验证

（只由人类填写。状态改成「已验证」时填：验证人、日期、测了什么、结果。）
