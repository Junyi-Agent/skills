# Changelog

版本号规则见 [ADR 0005](docs/adr/0005-versioning-and-tags.md)：X.Y.Z，改了 `skills/` 就要升版本。合并到 `main` 后 CI 自动打 `vX.Y.Z` tag 并建 GitHub Release。

每节分「不兼容」「新增」「修复」三类，没有的类别不写。「不兼容」里写清楚引用方要改什么。

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
