# Changelog

版本号规则见 [ADR 0005](docs/adr/0005-versioning-and-tags.md)：X.Y.Z，改了 `skills/` 就要升版本。合并到 `main` 后 CI 自动打 `vX.Y.Z` tag 并建 GitHub Release。

每节分「不兼容」「新增」「修复」三类，没有的类别不写。「不兼容」里写清楚引用方要改什么。

## 1.0.0 — 2026-11-21

第一个带版本号的发布。包含 `jy-repo-review`、`jy-repo-quiz`、`jy-repo-constrain-check`、`jy-repo-codify` 四个 skill。

### 不兼容

相对之前没有 tag 的提交：

- `jy-repo-constrain-check`：`check.py` 的调用方式改为 `check.py get checks [NUM...] [--repo PATH] [-o text|json] [--no-remote]`，原来的 `check.py <path> [--json]` 会以退出码 2 报错（#19）。SKILL.md 已同步，整个 skill 目录一起更新即可。
- `jy-repo-review` evals：`ablation_report.py` 改为 `get verdicts --iteration DIR [-o text|json]`，去掉 `--out`；fixture 脚本改为 `create repo` 子命令（#19）。

### 新增

- `jy-repo-constrain-check` 第 8 项：命令行入口必须是 kubectl 式结构，豁免要人类批准并写 ADR（#19）。
- `jy-repo-review`：设计与工程组检查命令结构（#19）；一致性组检查功能块之间有没有空行（#20）。
