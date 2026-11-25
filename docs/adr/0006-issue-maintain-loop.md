# 0006 用 issue 和定时 loop 维护仓库

状态：已取代（见 0007）

## 背景

回看 2026-11-18 到 11-21 的会话，用户在本仓库打的消息约 68 条：约 40% 是“去修吧”“merge”这类放行，约 20% 是问进度，约 33% 是方向和判断（新检查项、目录结构、版本方案、某个行为是否有意）。修 bug 的活全部来自手动跑的 `/jy-repo-review` 和 `/jy-repo-constrain-check`（#8、#11、#17、#23），仓库还没有外部 issue。

放行和问进度可以交给 loop。方向和判断仍要人做，ADR 的「提议 → 采纳」本来就规定由用户决定。

## 决策

- 新增内部 skill `jy-repo-maintain`，每次调用跑一轮：收尾上一轮的 PR → 分诊 issue → 修 bug → 给提议写 ADR → 没活时每 7 天自审一次，把发现开成 issue → 在 `maintain-log` issue 里汇报、列出等人决定的事。
- `jy-repo-maintain` 只给本仓库用，放在 `.claude/skills/jy-repo-maintain/`（实体目录），不放进 `skills/`，README 和安装说明不提它。安装命令按 `skills/jy-*` 链接，引用方不会装到它（2026-11-24 改，原来和其他 skill 一起在 `skills/` 里发布）。
- 本机 launchd 每 6 小时跑一轮（`maintain.py create schedule`）。放在本机：push 门禁要用本机已登录的 `claude` CLI 和 gh 凭据。launchd 保证同一时间只有一个实例，每轮跑完就退出。主 checkout 有未提交改动时不开始。
- issue 要人打 `accepted` 才处理，不论作者是谁，loop 自审开的 `from-review` issue 也一样。agent（`/jy-skills-update`、loop 自审）用 owner 的账号提 issue，按作者放行等于 agent 写的 issue 不经人看就会被处理（2026-11-24 改，原来 owner、member、collaborator 开的 issue 直接处理）。`.claude/maintain.json` 的 `require_accept_label` 改成 false 即全自动（这个文件是护栏，改它要人合并）。没打标签的 issue，loop 不读正文。
- 提议：loop 写「提议」状态的 ADR 开 PR，人打 `approved` 后，loop 改成「采纳」并实现。「已验证」仍只由人改。
- 合并：CI 全绿、没改护栏文件、没升 X 版本的 PR，loop 自己 squash merge。其余打 `needs-human-merge` 等人。护栏文件列在 `.claude/maintain.json` 的 `guardrails` 里：`.claude/`、`.github/`、`scripts/`、`.pre-commit-config.yaml`、`ruff.toml`、`CLAUDE.md`。`jy-repo-maintain` 在 `.claude/` 下，也在护栏内。
- 强制方式：launchd 给 loop 设 `JY_MAINTAIN_LOOP=1`，`git_guard.py` 从自己的环境变量读到后进入 loop 模式：
  - `gh pr merge` 要写明 PR 编号，并先过 `maintain.py check pr`，用的是主 checkout 的脚本和配置，PR 改不了检查自己的规则。
  - gh 只允许 `LOOP_GH` 里列出的子命令；`gh api` 只能 GET。
  - 不能加 `accepted` / `approved` 标签。
- 限流：每轮最多新开 2 个 PR。最近 3 个 loop PR 都改过同一个文件时，不再修这个文件，开 issue 问人要不要换方案。历史上 `git_guard.py` 的 shell 解析每轮审查都在上一轮的修复里发现新漏洞。
- 使用者那边：新增 skill `jy-skills-update`。检查更新时交给子 agent 读 CHANGELOG 和本项目使用 jy skill 的会话，整理出 issue 草稿，用户确认后再提。另外加 issue 模板 `bug.yml`、`proposal.yml`。

## 后果

- 人只需要做：给 issue 打 `accepted`，给提议打 `approved`，合并 `needs-human-merge` 的 PR，看 `maintain-log`。
- 每轮都会调用 Claude（loop 本身 + 每次 push 的 3 个审查），空转时也要读一次 issue 列表。
- loop 和人用同一个 GitHub 账号（Junyi-Agent），无法从作者区分。loop 的评论靠第一行的 `<!-- jy-repo-maintain -->` 标记区分；标签靠 git_guard 拦 loop。人在网页上打的标签无法和 loop 绕过 git_guard 打的区分。
- loop 用 `bypassPermissions` 运行，靠 hook、push 门禁、CI 和服务端分支保护约束。已知缺口：
  - loop 在一轮之内可以用 Bash 直接改主 checkout 里的 hook 或配置，git_guard 不检查文件写入。下一轮开始前的未提交改动检查只能防止改动延续到后面的轮次。
  - 0001 列出的 git_guard 缺口在 loop 模式下同样存在。
- 被注入的 issue 最坏能让 loop 合并一个过了门禁和 CI 的非护栏改动并发版。引用方锁定 tag，要主动更新才会拿到。
- Mac 睡眠或关机时不跑。

## 验证

（只由人类填写。状态改成「已验证」时填：验证人、日期、测了什么、结果。）
