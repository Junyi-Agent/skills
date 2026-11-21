---
type: review
commit: 73d2b4c40f321210485ea15a6566a4961e3e30f7
base: 490459125291547db6f9a6cc69c03eb5a4bb78b7
date: 2026-11-21
scope: incremental
tags: [#review]
---

# REVIEW

**增量审查**：范围是 `4904591..73d2b4c` 的 9 个提交（#13 至 #21）。改动的代码文件有 `git_guard.py`、`review_gate.py`、`check.py`、`ablation_report.py` 和它们的测试，以及新加的 `scripts/release.py`（含测试）、三个 eval fixture 脚本、`ci.yml` / `release.yml`、`.pre-commit-config.yaml`。改动的文档有 ADR 0001、0004、0005，CLAUDE.md、README、CHANGELOG，以及 `jy-repo-codify` / `jy-repo-constrain-check` / `jy-repo-review` 的 SKILL.md 和 evals。改动的代码约 1300 行，按 skill 规则四组维度由我依次审，核实交给 3 个新启动的 agent。上次的 6 条确认问题都逐条检查了修复。代码分块规则是 #20 新加的，这次对改动过的非测试代码文件整文件检查，测试文件没查。

## 1. 总体评价

上次的 6 条在 #17 里都有对应测试，pre-commit 全过，新加的 `release.py`、kubectl 式 CLI 改造和 `-o json` 都工作正常，fixture 的 hash 与 README 一致。这次最重要的问题和上次的 `#` 注释一样，早在 #7 就存在：`git_guard.py` 把反斜杠续行拆成两条命令，续行后面的 `--no-verify`、子命令、目标分支都不再检查。其次是上次 heredoc 和 `env -S` 两处修复各漏了一种写法，以及 `release.py` 的 pre-push 检查对象不对。

建议修复顺序：

1. `git_guard.py` 的反斜杠续行（在 `strip_redirections` 里把 `\` + 换行整个删掉，和 shell 一样）
2. `git_guard.py` 的 shell 读 stdin：`reads_script_from_stdin` 识别带取值的选项，here-string 喂给 shell 时按脚本检查
3. `git_guard.py` 的 `env` 选项按 getopt 规则解析（合写的短选项、`-P` 等带取值的选项）
4. `release.py` 在 pre-push 时检查被 push 的 ref，并且只在分支自己改了 VERSION 时才和 base 末端比较
5. 代码分块、CLAUDE.md

## 2. 确认的问题

### 中

**`.claude/hooks/git_guard.py:190`（以及 123-127）反斜杠续行把一条命令拆成两条**
- 问题：`strip_redirections` 把 `\` 和换行原样保留，第 190 行把换行替换成 ` ; ` 后变成 `\ ; `。shlex 把 `\ ` 读成一个只含空格的词，`;` 把命令切开，续行之后的词变成一条独立的“命令”。
- 核实：`segments('git commit --allow-empty \\\n  --no-verify -m x')` 返回 `[['git','commit','--allow-empty',' '], ['--no-verify','-m','x']]`。下面三种写法写成一行都被拒，用 `\` 续行都放行，用真 bash 执行后果都发生了：
  - main 上 `git \`↵`commit --allow-empty -m x`：main 上产生了提交。
  - feature 上 `git commit --allow-empty \`↵`--no-verify -m x`：pre-commit hook 没有运行。
  - feature 上 `git push origin \`↵`feat:main`：远端 main 被更新（本地裸仓库，没有分支保护）。
  - 反方向也错：`git checkout \`↵`-- file` 被误拒，空格词被当成分支名。
- 续行之前那段仍完整检查，`git commit \`↵`-m x` 在 main 上照样被拒，所以只有决定性的词落在续行之后才会漏。push 到 main 有服务端分支保护兜底，main 上的本地提交和 `--no-verify` 没有。docstring、ADR 0001、已知故意设计清单和测试（含 `test_agent_style_commands`）都没提到续行。反斜杠处理来自 `b9e9b61`（#7），审查者原定为高，核实者按上次 `#` 注释问题的标准调为中，我同意。

### 低

**`.claude/hooks/git_guard.py:268-275` 带取值选项的 shell 读 stdin 时不检查 heredoc 正文（上次 heredoc 修复不完整）**
- 问题：`reads_script_from_stdin` 要求参数全部以 `-`/`+` 开头或含 `-s`。`bash -o pipefail` 里的 `pipefail` 不满足，正文被当成数据跳过。
- 核实：在 main 上，`bash -o pipefail <<'X'`、`bash -euo pipefail <<'X'`、`cat <<'X' | bash -o pipefail` 都放行，对照 `bash -e <<'X'` 被拒。真 bash 执行后 main 上产生了提交；在 feature 上正文里的 `--no-verify` 生效。`bash -O extglob` 同样会漏。#17 的提交说明和 ADR 0001 都写了喂给 shell 的正文按脚本检查。

**`.claude/hooks/git_guard.py:89`、`137-162` here-string 喂给 shell 时内容不检查**
- 问题：`<<<` 的目标被当成普通重定向目标丢掉，`bash <<< '<命令>'` 只剩下 `bash`。
- 核实：`bash <<< 'git commit ...'` 用单引号、双引号都放行，真 bash 执行后 main 上产生了提交；feature 上 `--no-verify` 生效。CLAUDE.md 和 ADR 0001 列出拦不住的只有 `export SKIP=...` 后单独 push、`eval`、脚本文件，不包括 here-string；它和 ADR 承诺检查的 `bash <<EOF` 一样是把命令行里的字面文本喂给 shell。同类的 `echo '<命令>' | bash` 也放行，文档同样没提，修的时候一起定。

**`.claude/hooks/git_guard.py:241-248` `env` 的选项没有按 getopt 规则解析（上次 `env -S` 修复不完整）**
- 问题：`-S` 和别的短选项合写时（`-vS`、`-iS'...'`）落到 `else` 只跳过一个词；`-P <altpath>` 带取值但不在 `ENV_VALUE_OPTS` 里，取值被当成命令名。
- 核实：在 main 上，`env -iS'git commit ...'`、`env -vS 'git commit ...'`、`env -P /usr/bin git commit ...` 都放行，对照 `env -S '...'`、`env -i -S '...'` 被拒。用 macOS 的 `/usr/bin/env` 执行，三种都在 main 上产生了提交；feature 上 `env -vS` 带 `--no-verify` 时 hook 没运行。#17 的测试只覆盖了单独的 `-S`、`--split-string=` 和 `-S<命令>`。`-P` 一项是核实者发现的，原因相同，并入这条。

**`scripts/release.py:69-76` 落后于 origin/main 的分支，没改 skills/ 也会被拦**
- 问题：版本比较用 `--base` 末端的 VERSION，改动判断用 merge base。main 期间升过版本时，一个只改 README 的分支 VERSION 低于 origin/main，pre-push 报“VERSION 1.0.0 低于 origin/main 上的 1.1.0”。
- 核实：在临时仓库里装上 pre-push hook，真实 `git push origin feat` 被拒；rebase 后通过。docstring 字面上写了“不低于 REF 上的版本”，但 docstring、ADR 0005、#21 的提交说明都没提到这个副作用，ADR 0005 写的是只改 `scripts/` 等不用升版本。`test_lower_version_fails` 测的是分支主动把版本降低。CI 的 `pull_request` 检出的是合并提交，不会误报（推断，没在 Actions 上跑）。报错让人以为要升版本，实际要 rebase。

**`.pre-commit-config.yaml:50-56`（`release-version`）pre-push 检查的是当前 HEAD，不是被 push 的 ref**
- 问题：`release.py` 读工作区的 VERSION、用 `{base}...HEAD` 算改动，不看 `PRE_COMMIT_TO_REF`。同仓库的 `review_gate.py:172` 用的是 `PRE_COMMIT_TO_REF`。
- 核实：分支 A 合规、分支 B 改了 skills/ 没升版本。在 A 上 `git push origin B` 放行，B 被推上远端；在 B 上 `git push origin A` 被拒。两个方向都错。漏放的情况有 CI 兜底。

**`CLAUDE.md:26` 本地检查一节漏了 release 脚本测试**
- 问题：列出的单元测试只有 git guard、审查门禁、constrain-check，`.pre-commit-config.yaml` 里 #21 加的 `release-tests` 没写。#21 只在“约定”一节加了一行，没改这一句。

**代码分块：下列函数体里做不同事的几段代码之间没有空行**（19 处，每处都单独核实）
- `.claude/hooks/git_guard.py`
  - 106-180 `strip_redirections`：初始化扫描状态、引号/转义/注释、重定向识别、截取 heredoc 正文
  - 189-205 `segments`：准备 lexer、按标点分组 token
  - 413-432 `check_pr_merge`：解析参数、运行 `gh pr checks`、生成拒绝信息
  - 436-464 `check_git`：解析选项、hooksPath 检查、展开别名、跳过 hook 检查、切分支检查、main 上提交和 push 检查
  - 472-535 `check`：解析命令、初始化状态栈、管道/heredoc 记账、递归检查嵌套 shell、按命令种类分派
- `skills/jy-repo-constrain-check/scripts/check.py`
  - 485-525 `check_agent_gate`：收集候选文件、grep 信号、hook 阶段、提前返回、写结论
  - 553-587 `check_worktree_doc`：文档存在性、grep 信号、写 facts、定 verdict
  - 604-627 `check_enforcement`：`.claude/settings*.json` 与 `.claude/hooks/` 两个来源
  - 638-663 `check_enforcement`：流程脚本、Makefile 类目标、输出 facts
  - 716-747 `gha_triggers`：定位 `on:`、行内写法、块状写法
  - 761-811 `check_ci`：收集 CI 文件、并入 pre-commit 配置、检测 lint/测试/部署信号、触发分析（只用注释隔开）、汇总、放宽证据
  - 830-857 `branch_protection`：解析 remote、定分支、调用 `gh api`、解析响应
  - 1040-1067 `parse_args`：构建 parser、解析、校验
  - 1072-1099 `main`：定 root 和 hooks、分派执行、JSON 输出、文本输出
- `scripts/review_gate.py`
  - 171-197 `main` 前半段：定 head、求 merge-base、取 diff 和截断、组装 ctx（后半段已分块）
- `skills/jy-repo-review/evals/ablation_report.py`
  - 50-68 `parse_args`：构建 parser、解析、校验
  - 84-115 `case_result`：准备计数表、遍历 grading.json 计数、算通过率组 rows
  - 136-148 `main`：加载输入、选 case、计算、输出

## 3. 需要你确认

无。

## 4. 判定为故意设计

无。

## 5. 上次问题的处理情况

| 上次的问题 | 状态 |
|---|---|
| 未加引号的 `#` 吞掉后面的行 | 已修，有测试（含 `a#b`、URL、heredoc 首行注释）；修复本身没引入新问题。同类的续行问题是另一处缺陷，见第 2 节 |
| 喂给 shell 的 heredoc 正文脱离所属命令检查 | 已修，正文跟着所属命令走，用该 shell 的 cwd 和前缀变量，有测试；带取值选项的 shell 没覆盖，见第 2 节 |
| `env -S` 不展开 | 单独写的 `-S`、`--split-string`、`-S<命令>` 已修，有测试；合写的短选项没覆盖，见第 2 节 |
| `on:` 下零缩进的列表读不到 | 已修，有测试 |
| heredoc 正文不管喂给谁都按脚本检查，误拒文档和 PR 正文 | 已修，有测试 |
| `verbose: true` 被当成放宽信号 | 已修，有测试 |

## 6. 统计

| 组 | 本次上报 | 确认 | 故意设计 | 证伪 | 无法判断 | 上次上报 / 确认 / 证伪 | 上上次 | 再上次 |
|---|---|---|---|---|---|---|---|---|
| 正确性 | 6 | 6 | 0 | 0 | 0 | 5 / 5 / 0 | 8 / 8 / 0 | 12 / 11 / 0 |
| 清理 | 0 | 0 | 0 | 0 | 0 | 0 / 0 / 0 | 0 / 0 / 0 | 2 / 2 / 0 |
| 一致性 | 5（1 条文档 + 4 条代码分块，共 19 处） | 5 | 0 | 0 | 0 | 0 / 0 / 0 | 1 / 1 / 0 | 0 / 0 / 0 |
| 设计与工程 | 0 | 0 | 0 | 0 | 0 | 0 / 0 / 0 | 0 / 0 / 0 | 3 / 3 / 0 |

- 连续四次没有证伪的条目，不需要收紧哪一组的要求。
- 漏报仍要注意：续行问题从 #7 起就存在，和上次的 `#` 注释一样，是 agent 常写的多行命令形态。上次新增的 `test_agent_style_commands` 语料没有带续行的命令，建议补进去。
- 所有条目都逐条核实，没有抽查。6 条正确性发现都在临时仓库里复现，4 条 `git_guard` 发现用真 shell 确认了后果，2 条 `release.py` 发现用真实的 `git push` 加 pre-push hook 确认。
- 命令结构检查（#19 新加）：本仓库的入口 `check.py get checks`、`ablation_report.py get verdicts`、`release.py check version`、三个 fixture 脚本的 `create repo` 都满足 kubectl 式结构；`git_guard.py`、`review_gate.py`、`place-files.sh` 由调用方定死参数，不在范围内。没有上报。

## 7. 基线

- commit：`73d2b4c40f321210485ea15a6566a4961e3e30f7`（2026-11-21 15:46 +0800，“Add VERSION, CHANGELOG and automatic release tags (1.0.0) (#21)”）。上次基线是 `4904591`。
- 审查期间 HEAD 和 `origin/main` 都没有新提交。下次增量审查以 `73d2b4c` 为起点。
- 跑过的工具（都在固定 commit 的 worktree 拷贝里跑）：
  - `pre-commit run --all-files`：全部通过，含四套单元测试。
  - `ruff 0.8.6 --select ALL`（排除项目未启用的风格类规则）：剩下的是 C901/PLR091x 复杂度、测试替身的 ARG001、PLW1510、FURB167 等，没有单独上报。`git_guard.py` 的 BLE001 是 fail closed 有意捕获所有异常。
  - `mypy`：`release.py:43`、`:74` 报 4 条类型错误（`parse` 的返回类型写成 `tuple[int, int, int]`，实际推断是 `tuple[int, ...]`；`:74` 的比较前已经排除了 None），运行时不会出错，没有上报。
  - `vulture`：4 条都是误报（shlex 的属性赋值、`__exit__(*exc)`）。
  - `shellcheck`：无问题。
  - 三个 fixture 脚本：`build_kvs.sh create repo full|incremental` 的 HEAD 是 `d8a3a01` / `abe905f`，`build_feeds.sh create repo` 的 HEAD 是 `e8e9bb3`，与 README 一致；`build_good_repo.sh create repo` 后 `check.py get checks 8` 列出了豁免 ADR，与脚本注释一致。
- 没跑的：
  - README 的安装步骤：会往别的项目写 submodule 和软链接。
  - `review_gate.py` 的真实 `claude` 调用：每次要起 3 个 Claude。
  - `release.yml`：要在 GitHub Actions 上跑。
  - 各 skill 的完整 eval 流程。

## 8. 后续处理（2026-11-21）

第 2 节的问题全部在 `fix-review-4` 分支处理，正确性类每条都先写了测试，修复前的代码上这些测试都失败。

- 反斜杠续行：`strip_redirections` 在单引号外遇到 `\` 加换行时把两个字符都删掉，和 shell 一样把两行接起来。`test_agent_style_commands` 补了带续行的提交和 push 命令。
- shell 从 stdin 读脚本：`reads_script_from_stdin` 按 shell 的选项规则解析，`-o` / `-O` / `+o` 和 `--rcfile`、`--init-file` 会带走下一个词，`-` / `--` 结束选项；选项后面跟了脚本文件且没有 `-s` 时才算不读 stdin。
- here-string：`<<<` 的词按正文切出来，跟着所属命令走，喂给 shell 时按脚本检查。按上次报告里留的问题一并处理了 `echo '...' | bash` 和 `printf ... | sh`，管道中间隔着 `tee` 或不带文件的 `cat` 也会传下去。别的命令的输出喂给 shell（`cat x.sh | bash`）和脚本文件同类，写进了 ADR 0001 的“后果”和 CLAUDE.md 的已知故意设计清单。
- `env` 选项：新函数 `unwrap_env` 按 getopt 规则解析，短选项可以合写，`-u` / `-C` / `-S` / `-P` / `-a` 和对应的长选项带取值，`--` 结束选项，`-S` 展开后的词再按选项解析一遍。
- `release.py`：
  - 新增 `--ref`，从这个提交读 VERSION 和 CHANGELOG.md；pre-push 阶段传 `PRE_COMMIT_TO_REF`。40 个 0（删除分支）直接通过。
  - 分支自 merge base 以来没改 `skills/` 也没改 VERSION 时，不和 `--base` 比较。落后的分支改了 `skills/` 时，报错提示先 rebase 再升版本。
  - 用核实时的脚本在临时仓库里重放：在 A 上 `git push origin B` 现在被拒，在 B 上 `git push origin A` 放行；落后且只改 README 的分支能 push。
- `CLAUDE.md:26`：#22（`cc2aae0`）改写这一句时已经改成“各脚本的单元测试”，不用再改。
- 代码分块：第 2 节列的 19 处都加了空行；`git_guard.py`、`release.py` 这次新写和改写的函数也按同样的规则分块。
- ADR 0001、ADR 0005 同步了上面的行为。`check.py`、`ablation_report.py` 在 `skills/` 下，VERSION 升到 1.0.2。
- 下次增量审查以 `73d2b4c` 为基线，范围包括 #22 和这次的修复。
