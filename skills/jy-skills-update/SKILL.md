---
name: "jy-skills-update"
description: "检查并更新当前项目引用的 jy skills（Junyi-Agent/skills，通常是 .claude/jy-skills 这个 submodule），同时回顾本项目里用 jy-* skill 的会话，把 skill 自身的问题整理成 issue 草稿，用户确认后提到 Junyi-Agent/skills。用户说更新 jy skills、检查 skill 更新、jy skills 有新版本吗、给 skills 仓库提 issue、反馈 jy skill 的问题时使用。"
---

# jy-skills-update：更新 jy skills，顺带反馈问题

读历史、查 CHANGELOG 的活交给一个子 agent 做，主会话只拿摘要，用户当前的上下文不被大段日志占满。提 issue 和改用户仓库这两件事都要用户确认。

用户只想反馈一个已经说清楚的问题时，跳过第 1、2 步：按第 2 步模板第 4 条的格式在 scratchpad 里写草稿，从第 3 步开始。

## 1. 找到安装位置和当前版本

- 在项目根目录执行 `git config -f .gitmodules --get-regexp 'submodule\..*\.url'`，找 url 含 `Junyi-Agent/skills` 的那一项，取它的 path（README 推荐的是 `.claude/jy-skills`）。
- 当前版本：`git -C <path> describe --tags`；没有 tag 时读 `<path>/VERSION`，并说明锁的是某个提交。
- 找不到 submodule（skill 是复制进来的，或装在 `~/.claude/skills`）：告诉用户这种装法没法自动更新，只做下面的回顾和反馈；要更新可以改用 README「安装到项目」一节的 submodule 方式。

## 2. 交给子 agent

Claude Code 里用 Agent 工具起一个全新的 general-purpose agent（不要 fork，它不需要当前对话），prompt 用下面的模板，把尖括号里的值填好。草稿目录用会话的 scratchpad 目录，没有就 `mktemp -d`。

没有子 agent 能力的 agent（如 pi）：让用户新开一个会话，把这段 prompt 贴进去跑，跑完把摘要贴回来。

```text
你在帮用户检查项目 <项目根目录> 引用的 jy skills（GitHub 仓库 Junyi-Agent/skills）。
安装位置：<submodule path，没有就写“无，只做反馈”>；当前版本：<版本>。
草稿目录：<scratch 目录>。只能在草稿目录里写文件；不要提 issue，不要改项目里的任何文件，
不要 checkout 或更新 submodule。

1. 新版本：git -C <path> fetch --tags -q，列出比当前版本新的 vX.Y.Z tag。用
   git -C <path> show <最新 tag>:CHANGELOG.md 读 CHANGELOG，摘出当前版本之后到最新版本
   之间各版本的段落。「不兼容」一节单独列出，写清引用方要改什么。
2. 回顾使用历史：运行
   python3 <skill 目录>/scripts/history.py get invocations --project <项目根目录> -o json
   它列出本项目（含 worktree）会话里每次 jy-* skill 调用、之后用户说的话和报错的工具调用。
   只在需要时用 Read 打开对应会话文件看上下文，不要整份读。找 skill 自身的问题：
   - SKILL.md 写错、过时，或和 skill 里的脚本对不上；
   - skill 自带的脚本报错、参数不对；
   - 用户在同一类事情上反复纠正 agent，而原因在 skill 的写法；
   - 该触发没触发，或不该触发时触发了。
   用户项目自己的 bug、用户的个人偏好、和 jy skill 无关的报错，不算。
3. 查重：每个候选问题用
   gh issue list --repo Junyi-Agent/skills --state all --search "<搜索词>" 查已有 issue。
   已有的不写草稿，只在摘要里给出 issue 链接；gh 用不了就跳过查重并在摘要里说明。
4. 写草稿：每个问题一个 Markdown 文件，第一行是标题（`# [skill 名] 一句话问题`），正文按
   这几节写：Skill、版本、发生了什么、期望、复现。复现写成不依赖用户项目的最小步骤。
   去掉用户项目的私有内容：代码片段、文件路径、仓库名、人名、内部系统名、token，一律换成
   中性的说法。拿不准能不能公开的内容，不写进去，在摘要里提一句。
5. 只返回不超过 15 行的摘要：当前版本、最新版本、不兼容项（每项一行）、每个草稿的标题和
   文件路径、查重时找到的已有 issue。
```

## 3. 和用户确认

把摘要给用户，问两件事：

- 要不要升级，升到哪个版本。有「不兼容」项时，把要改的地方说清楚。
- 哪些草稿要提成 issue。

用户选好草稿后，把每个要提的草稿全文贴给用户看，用户确认后才提。issue 是公开的，提了可能被缓存和索引，删掉也收不回来。用户要改措辞就先改草稿文件。

## 4. 提 issue

```bash
gh issue create --repo Junyi-Agent/skills --title "<草稿第一行去掉 # >" --body-file <去掉标题行后的正文文件>
```

- 不加 `--label`：不是仓库协作者设的 label 会被 GitHub 丢掉，维护者那边的 loop 会分诊打标签。
- 没装 gh 或没登录：给用户链接 https://github.com/Junyi-Agent/skills/issues/new?template=bug.yml （功能建议用 `template=proposal.yml`）和可以直接粘贴的标题、正文。
- 告诉用户：外部提的 issue 要维护者打上 `accepted` 标签，维护 loop 才会处理；进展在 issue 下的评论里看。

## 5. 更新

用户同意升级后，照 Junyi-Agent/skills 的 README「更新」一节做：

```bash
git -C <path> fetch --tags
git -C <path> checkout vX.Y.Z
for d in <path>/skills/jy-*/; do n="$(basename "$d")"; ln -sfn "../<path 去掉 .claude/ 的部分>/skills/$n" ".claude/skills/$n"; done
find .claude/skills -maxdepth 1 -type l -lname '../<同上>/*' ! -exec test -e {} \; -print -delete
```

- 软链接的相对路径以 README 为准（submodule 在 `.claude/jy-skills` 时是 `../jy-skills/skills/<name>`）。只删指向 jy skills 的悬空链接，别的不动。
- 有「不兼容」项的，按 CHANGELOG 说的改完用户项目里受影响的地方。
- `git add` 和 commit 之前先问用户。遵守用户项目自己的规则：要求在 worktree 或 feature 分支上改、要求开 PR 的，照做。commit message 可以用 `Update jy skills to vX.Y.Z`。
- 提醒用户：同事拉到这个提交后要执行 `git submodule update` 才会切到新版本。

## 6. 报告

简短告诉用户：

- 版本从几到几（没升就写“保持 vX.Y.Z”）。
- 不兼容项改了哪些地方，还有哪些要用户自己处理。
- 提了哪些 issue（链接），哪些草稿没提、在哪个文件里。
