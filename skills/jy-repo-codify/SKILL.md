---
name: "jy-repo-codify"
description: "在当前仓库收集 agent 犯过的错和用户的纠正（对话、REVIEW.md、fix 提交、只写在文档里的规则），把它们固化成可强制执行的检查（API 约束、类型、lint、测试、check 脚本），往 AGENTS.md 加一条只作最后退路。用户说 /jy-repo-codify、codify、固化这个纠正、别再犯这个错、把经验变成约束时使用。"
---

# jy-repo-codify：把经验变成系统能强制执行的约束

原则：文档是建议，检查才是约束。一次纠正只有在“下次犯同样的错会自动失败”时才算真正固化。AGENTS.md 只是最后的退路。

## 1. 收集经验，选出要固化的不变式

先看入口：

- **刚纠正过**：用户刚在对话里纠正了 agent，或在命令后附了说明，就以这次纠正为准：agent 做错了什么，正确的做法是什么。直接进入下面的改写。
- **没指定**：在仓库里收集候选。每个来源快速扫一遍，不深挖：
  - 最近对话里的纠正。
  - `REVIEW.md` 及其历史（`git log -p --follow REVIEW.md`）：反复上报的同类问题、修了又回退的、修复不完整又出现的。
  - fix 类提交（例如 `git log --since="3 months ago" -i -E --grep='fix|revert|回退' --format='%h %s'`）：修的是哪一类错误，同类是否修过不止一次。
  - CLAUDE.md、AGENTS.md、ADR 里只靠文字约束的规则：没有对应 hook、lint、测试、CI 检查的，都是候选。之前已经写进文档却还在犯的，优先。
  - 被 hook 或检查拒绝过的命令、agent 执行失败的命令（环境里有 atuin 时用它查）。

  每条候选写成一行：错误是什么、出处（`文件:行号` 或提交 hash）、出现几次、目前有没有机械检查。已经有检查覆盖的、列在“已知的故意设计”里的，不列。按出现次数和后果排序，把前几条列给用户，由用户选固化哪几条，选完再往下做。选了多条时，每条单独走第 2 到第 5 步。

对选定的每条：

- 把它改写成一句**不变式**，例如“产品代码里不得直接调用 `jax.jit`，必须通过 `kernels.compile()`”。
- 推广到**一类错误**，不要只针对这一处。问自己：同样的错误还会以哪些形态出现？
- 纠正本身含糊，或者可以有多种不变式解读时，先用一句话向用户确认，再动手。

## 2. 摸清项目现有的强制执行设施

快速检查，不要深挖：
- 语言、包管理（`pyproject.toml`、`package.json`、`Cargo.toml` 等）
- 已有的 linter 和配置（ruff、eslint、clippy、ast-grep、semgrep、import-linter）
- 类型检查（pyright、mypy、tsc）及其严格程度
- 测试框架和测试目录结构
- 统一入口：`make check`、`just check`、`npm run check`、pre-commit、CI workflow
- AGENTS.md / CLAUDE.md 里已有的相关条目。如果这次的纠正之前已经写在文档里，说明文档没起作用，这正是固化的理由。

## 3. 按“强度阶梯”选机制

从上往下找第一个可行的。越靠上，越是从根上消除错误：

1. **让错误写不出来（API / 结构）**：删掉或私有化容易误用的入口，提供唯一正确的包装函数，挪进内部模块。最好的规则是不需要规则。
2. **类型约束**：NewType、Literal、Protocol、更窄的签名、pyright strict 中相关的 report 选项。
3. **lint 规则**，优先级从高到低：
   - 打开现有工具里已有的规则
   - 禁用 API / import：ruff `TID251` + `[tool.ruff.lint.flake8-tidy-imports.banned-api]`；eslint `no-restricted-imports` / `no-restricted-syntax`；clippy `disallowed-methods`
   - 模块边界：Python 用 import-linter，JS/TS 用 dependency-cruiser 或 eslint boundaries
   - 结构模式：ast-grep 或 semgrep 规则（YAML，放在 `rules/` 下）
   - 自定义 lint 插件：最后才考虑
4. **测试**：回归测试（复现这次的错误）、属性测试（hypothesis）。数值 kernel 要和参考实现对比（`assert_allclose`），覆盖边界 shape 和 dtype。
5. **check 脚本**：上面都不合适时，写一个小脚本（grep 或 AST 检查），接入 `make check`。
6. **文档**：只有确实无法机械检查时才用（比如涉及设计判断）。在 AGENTS.md 里写**一行**，并在报告里说明为什么无法强制执行。

规则的报错信息本身就是给 agent 的指令：必须说清楚**该怎么做**，而不只是“禁止 X”。例如 `"jax.jit".msg = "用 kernels.compile()，它会处理 donate_argnums 和缓存"`。

## 4. 实现，并双向验证

- 范围尽量小，只为这个不变式服务。
- **证明规则会触发**：写一个违规的临时片段，或者临时还原到出错的写法，确认检查失败、报错信息清楚。验证完删掉临时内容。
- **证明没有误报**：在整个代码库上跑一遍。现有代码有违规的：
  - 属于真问题的，顺手修掉；
  - 属于合理例外的，用行内 ignore 加注释写明原因，或者加进 allowlist；
  - 例外太多的，说明规则写得太宽，收窄后重来。不要为了通过而削弱规则。
- 不得关闭或放宽任何已有检查。

## 5. 确保它真的会被执行

- 新规则必须能从统一入口触发：`make check`（或项目的等价命令）、pre-commit、CI，至少覆盖一个，最好全部覆盖。
- 项目还没有 `check` 入口的，创建一个最小的 `check` 目标，把 lint、类型检查、测试串起来。这是项目进入“值得固化”阶段的标志。
- 最后完整跑一次 `check`，确认是绿的。

## 6. 报告

简短回复用户，固化了几条就写几段：
- **不变式**：一句话，附第 1 步找到的出处
- **机制**：用了阶梯上的哪一级，为什么不用更高的那一级
- **位置**：改了哪些文件
- **验证**：违规时如何失败（附报错信息），当前代码是否通过，处理了哪些例外
- **跨项目建议**：这条纠正如果不只适用于本项目（比如通用的 Python/JAX 习惯），提示用户把它加进自己的项目模板或全局配置。不要擅自修改仓库以外的文件。

第 1 步收集了候选、这次没选的，在最后列成一张短表（错误、出处、建议用阶梯上的哪一级），方便下次接着做。

不要自动 commit，除非用户要求。可以附一条建议的 commit message，格式如 `codify: <不变式>`。

## 常用配置速查

ruff 禁用 API（需要在 `select` 里包含 `TID251`）：

```toml
[tool.ruff.lint.flake8-tidy-imports.banned-api]
"jax.jit".msg = "用 kernels.compile()，它统一处理缓存和 donate"
```

ast-grep 结构规则（`ast-grep scan --rule rules/<id>.yml`）：

```yaml
id: no-jit-in-loop
language: python
rule:
  pattern: jax.jit($F)
  inside:
    kind: for_statement
    stopBy: end
message: 不要在循环里 jit，把编译移到循环外并复用
severity: error
```

import-linter 模块边界（`.importlinter`）：

```ini
[importlinter]
root_package = mypkg

[importlinter:contract:kernels-internal]
name = 只有 kernels 包能 import kernels._impl
type = forbidden
source_modules = mypkg.app
forbidden_modules = mypkg.kernels._impl
```
