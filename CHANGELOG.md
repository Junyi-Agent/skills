# Changelog

版本号规则见 [ADR 0005](docs/adr/0005-versioning-and-tags.md)：X.Y.Z，改了 `skills/` 就要升版本。合并到 `main` 后 CI 自动打 `vX.Y.Z` tag 并建 GitHub Release。

每节分「不兼容」「新增」「修复」三类，没有的类别不写。「不兼容」里写清楚引用方要改什么。

## 2.3.1 — 2026-11-26

### 修复

- `jy-wake-up`：新增 `scripts/e2e_wake_guard.py`，在真实的 `claude -p` 会话里跑三个用例（只承诺不开工、只在最终文本里回复、写了「无需回复」），每个用例都让 agent 先加载这个 skill。改 hook 后必须跑，SKILL.md 里写了。要求登录 claude CLI，CI 不跑。去掉 2.3.0 的 isMeta 修复后，脚本报出两个 FAIL。

## 2.3.0 — 2026-11-26

### 新增

- `jy-wake-up`：被唤醒的一轮要用 IM 工具回复。`wake_guard.py` 在这一轮没有调用 `mcp__im__send_*`、最终文本里也没有一行「无需回复：…」时不让这一轮结束（以前只写在最终文本里的回复要靠系统代贴，对方收不到通知）。
- `jy-wake-up` 加两节条款：「发给谁、要不要 @」（只给 +1 看的走私聊；确认、致谢类消息不发或不 @）和「向上要决策」（问题写成选项、一次问完、等答复时先做不受影响的事）。
- `wake_guard.py` 修复：这一轮调用过 Skill 时，Claude Code 会在 transcript 里插一条 `isMeta` 的 user 行（skill 正文），hook 把它当成了这一轮的 prompt，判断成「不是唤醒」而放行，2.2.0 的检查在 agent 加载 `jy-wake-up` 后就失效。现在跳过 `isMeta` 行，加载 skill 也不算开工。
- 已配了这个 hook 的引用方：被唤醒后不需要回复的轮次，要在最终文本里写「无需回复：…」，否则会被拦下。

## 2.2.0 — 2026-11-26

### 新增

- 新 skill `jy-wake-up`（#46）：被 IM 唤醒时，派给自己的活要在同一轮内开工，不开工就在回复里写一行「不开工原因：…」。附带 Stop hook `scripts/wake_guard.py`：一轮结束时，如果这一轮是被唤醒的、只用了 IM / 读文件 / HANDOFF.md，回复里承诺了「接下来 / 下一步」之类，却没写不开工原因，就不让这一轮结束。本仓库的 `.claude/settings.json` 已经配上这个 hook。引用方想用的话，按 SKILL.md 在自己的 settings 里加。

## 2.1.0 — 2026-11-25

### 新增

- `jy-repo-constrain-check`：先识别仓库用了哪些语言，容易跨语言误判的规则只对这些语言生效（#30 第二步）。按语言生效的是：strict、weak、mypy 的 ignore_errors、CI 里识别 lint 和测试的匹配词、参数解析和 app 入口的代码信号，以及 CLI、库、app 的声明。工具名、扫描范围（跳过的目录、代码后缀）和 linter 配置文件不分语言。一种语言都没识别到时，行为和以前一样。输出第一行列出识别到的语言，`-o json` 多了 `languages` 字段。
- 第 8 项的 CLI_DECL、LIB_DECL、APP_DECL、APP_PATHS、APP_GLOBS 也搬进了 `rules/<lang>/rules.json`。

### 结果会变的仓库

在本机 106 个仓库上和 2.0.3 对比：103 个输出不变，各项初判（PASS/WARN/FAIL/JUDGE）全部不变。只有 3 个仓库的证据或计数有变化，都是去掉了误报：

- 一个 JS 站点：CI 里的「lint：有」原来是 `Copyright` 这个词命中了 `pyright`，现在是「lint：无」。
- 一个 Python 工具仓库：第 8 项不再把 `.py` 注释里出现的 `UIApplicationMain` 当成 app 信号，类型初判从「两类都有」变成「工具类」。
- 一个 Electron 仓库：不再把打包后的 JS 文件里的 `getopts` 算作参数解析。

## 2.0.3 — 2026-11-25

### 修复

- `jy-repo-constrain-check`：各语言的识别规则从 `scripts/check.py` 搬到 `rules/<lang>/rules.json`，分 python、javascript、go、rust、swift、kotlin、other、common 八个目录（#30 第一步）。输出不变：对本机 106 个仓库，用 `-o json` 跑新旧两版，结果逐字节相同。第 8 项按文件声明的几个列表暂时还留在 `check.py`。引用方不用改什么。
## 2.0.2 — 2026-11-25

### 修复

- 定时维护 loop 已停用，文档还写着 launchd 每 6 小时跑一轮。新增 ADR 0007：停用 loop，改由维护项目组人工接手；ADR 0006 状态改成「已取代（见 0007）」，正文不改；CLAUDE.md 的「维护 loop」一节改成「维护」。引用方不用改什么。

## 2.0.1 — 2026-11-24

### 修复

- `.claude/hooks/test_git_guard.py` 的 `test_this_repo` 只在有 `.claude/maintain.json`（跑维护 loop）的仓库里检查 `maintain.py` 是否存在。2.0.0 不再发布 `jy-repo-maintain`，原样复制这个测试的引用方按 README 升级后没有 `maintain.py`，测试失败、pre-commit 拦下提交。

## 2.0.0 — 2026-11-24

### 不兼容

- `jy-repo-maintain` 改成本仓库的内部 skill，移到 `.claude/skills/jy-repo-maintain/`，不再随 `skills/` 发布。引用方按 README「更新」一节升级时，删悬空链接那一步会删掉它的软链接。

## 1.3.0 — 2026-11-24

### 新增

- `jy-repo-maintain`：所有 issue 都要人打 `accepted` 才处理，包括 owner、member、collaborator 开的和 loop 自审开的 `from-review` issue。agent（`/jy-skills-update`、loop 自审）用 owner 的账号提 issue，原来按作者放行，agent 写的 issue 不经人看就会被处理。用了这个 skill 的仓库，升级后要给想让 loop 处理的已有 issue 补打 `accepted`；不想要这一层的，把 `.claude/maintain.json` 的 `require_accept_label` 设成 false。

## 1.2.1 — 2026-11-24

### 修复

`jy-repo-constrain-check` 的 `check.py`：

- 扫描跳过 git submodule 和仓库里嵌套的其他 git 仓库（子目录根上有 `.git`），以及 `.build`（SwiftPM）、`DerivedData`、`Pods`、`.gradle`。第 1 项不再统计 submodule 里的 ADR，第 8 项不再把依赖里的脚本算进来。没有 `adr/` 这类目录时，第 1 项写出 ADR 文件实际所在的目录（#28）。
- 已安装的 hook 只认 git 会执行的 hook 名（`pre-commit`、`pre-push` 等），hooks 目录里的其他脚本和测试不再算 git hook（#27）。
- mypy 的 `ignore_errors = true` 只在 `mypy.ini`、`setup.cfg`、`pyproject.toml` 里算放宽，代码里的 `shutil.rmtree(..., ignore_errors=True)` 不算（#27）。
- 第 3 项认 `swift format` / `swift-format` 为 formatter，`--select ALL` 为严格信号；第 5 项认「不在主目录切分支」这类写法；第 7 项认 `swift test`、`xcodebuild test`、gradle、maven、`dotnet test`、`rspec`（#26）。
- 第 8 项的 app 信号加上 Xcode 工程（`*.xcodeproj` / `*.xcworkspace`）、SwiftUI / UIKit / AppKit 入口、Android 的 `com.android.application`、Flutter 的 `pubspec.yaml`（#29）。
- 第 3 项在 hook 文本里认不全 formatter 和 linter 时，跟进 hook 调用的仓库文件（`python3 scripts/x.py`、`bash x.sh`、`make <target>`、`npm run` 等，最多两层，不跟进测试文件），并写出跟进了哪些文件；调用的脚本在 repo 里找不到时判 JUDGE，不判 FAIL。列表写法的 `"--select", "ALL"` 也算严格信号（#25）。
- 第 2 项找安装步骤时也看 `docs/*.md`、`docs/CONTRIBUTING.md`、`.claude/CLAUDE.md` 和 `.pre-commit-config.yaml`，和第 5 项共用文档列表（#35）。

`jy-skills-update`：

- 第 1 步认出软链接到本机 clone 的装法（`.claude/skills/jy-*` 或 `~/.claude/skills/jy-*` 指向 Junyi-Agent/skills 的 clone），照常查版本；第 5 步给出 clone 的更新步骤，包括项目装了 `git_guard.py` 时锁 tag 要用户自己执行。README「安装到项目」「更新」补上这种装法（#31）。

## 1.2.0 — 2026-11-24

### 新增

别的仓库可以原样复制本仓库的 hook 和脚本，本地差异放进配置，同步上游时整文件替换：

- `scripts/review_gate.py`：仓库里有 `.claude/review_gate.json`（`{"reviewers": {名字: 审查方向}}`，正好 3 个不同的方向）时用它代替内置的 3 个审查方向。文件从 merge-base 读，push 改不了审查自己的 reviewer；格式不对时阻止 push。
- `.claude/hooks/git_guard.py`：loop 模式在 `skills/` 或 `.claude/skills/` 下找 `jy-repo-maintain/scripts/maintain.py`，引用方用 submodule 加软链接时也能找到。
- `scripts/repo_check.py`：没有 `skills/` 目录的仓库，`check skills` 不报问题。

本仓库自己的行为不变。

## 1.1.0 — 2026-11-21

### 新增

- `jy-repo-maintain`：按 issue 维护仓库的一轮 loop，配 `scripts/maintain.py`（`get issues`、`check pr`、`create schedule`）。用法和首次设置见 SKILL.md，本仓库的做法见 ADR 0006。
- `jy-skills-update`：给引用本仓库的项目用。检查新版本，由子 agent 回顾本项目使用 jy skill 的会话（`scripts/history.py get invocations`），整理出 issue 草稿，用户确认后提交，再按 README 更新 submodule。

## 1.0.2 — 2026-11-21

### 修复

- `jy-repo-constrain-check` 的 `check.py`、`jy-repo-review` evals 的 `ablation_report.py`：函数体里做不同事的几段代码之间加空行（2026-11-21 审查报出的代码分块问题）。行为不变。

## 1.0.1 — 2026-11-21

### 修复

- `jy-repo-quiz`、`jy-repo-review`：SKILL.md 去掉全局 CLAUDE.md 禁用的空泛修饰词，改成具体说法（含 `jy-repo-quiz` 的 description，只换了一个词）。行为不变。

## 1.0.0 — 2026-11-21

第一个带版本号的发布。包含 `jy-repo-review`、`jy-repo-quiz`、`jy-repo-constrain-check`、`jy-repo-codify` 四个 skill。

### 不兼容

相对之前没有 tag 的提交：

- `jy-repo-constrain-check`：`check.py` 的调用方式改为 `check.py get checks [NUM...] [--repo PATH] [-o text|json] [--no-remote]`，原来的 `check.py <path> [--json]` 会以退出码 2 报错（#19）。SKILL.md 已同步，整个 skill 目录一起更新即可。
- `jy-repo-review` evals：`ablation_report.py` 改为 `get verdicts --iteration DIR [-o text|json]`，去掉 `--out`；fixture 脚本改为 `create repo` 子命令（#19）。

### 新增

- `jy-repo-constrain-check` 第 8 项：命令行入口必须是 kubectl 式结构，豁免要人类批准并写 ADR（#19）。
- `jy-repo-review`：设计与工程组检查命令结构（#19）；一致性组检查功能块之间有没有空行（#20）。
