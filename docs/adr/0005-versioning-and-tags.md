# 0005 版本号和 tag

状态：采纳

## 背景

别的项目用 git submodule 引用本仓库，把 skill 软链接进自己的 `.claude/skills/`。之前没有版本号和 tag，引用方只能锁到某个 commit，看不出两次更新之间有没有不兼容的改动（比如 #19 改了 `check.py` 的参数）。

## 决策

- 整个仓库用一个版本号，写在根目录的 `VERSION`，格式 X.Y.Z。submodule 只能整体锁定，按 skill 分别打版本没有意义。
- 升级规则（只看对引用方的影响）：
  - X：引用方要跟着改才能用。删除或改名 skill；skill 里脚本的命令行参数、`-o json` 输出字段不兼容；skill 的调用方式变了
  - Y：新增 skill、新增检查项或规则、新增参数。会让原来通过的检查出现新的 ⚠️，也算 Y，在 CHANGELOG 里写明
  - Z：修 bug、改措辞，行为不变
- 改了 `skills/` 下任何文件就要升版本，并在 `CHANGELOG.md` 顶部加一节。只改 `scripts/`、`.claude/`、`docs/`、CI 等不影响引用方的文件，不用升。
- 强制方式：`scripts/release.py check version --base origin/main` 检查 VERSION 格式、CHANGELOG 第一节与 VERSION 一致、改了 `skills/` 时版本比 `origin/main` 高。pre-commit 的 pre-push 阶段和 CI 都跑。
- 打 tag：`.github/workflows/release.yml` 在 `main` 上 VERSION 变化时，若 `vX.Y.Z` 不存在，就在该提交上建 tag 和 GitHub Release，说明取 CHANGELOG 对应的那一节。不手动打 tag。
- 从 1.0.0 开始：已经有别的项目在用，用 1.x 让 X 的变化明确表示不兼容。

## 后果

- 每个改 `skills/` 的 PR 都是一次发布，PR 多时版本号涨得快，但引用方能精确锁定。
- 两个 PR 同时升到同一个版本号时，后合并的那个在 rebase 后会被检查拦下（版本不高于 `origin/main`），要再升一次。
- X / Y / Z 选哪个靠人和 agent 判断，脚本只检查“有没有升”。

## 验证

（只由人类填写。状态改成「已验证」时填：验证人、日期、测了什么、结果。）
