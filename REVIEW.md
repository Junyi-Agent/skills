---
type: review
commit: 490459125291547db6f9a6cc69c03eb5a4bb78b7
base: 0be9018fbf3e1ed8f078a351d1e56c07d65131a9
date: 2026-11-20
scope: incremental
tags: [#review]
---

# REVIEW

**增量审查**：范围是 `0be9018..4904591` 的 2 个提交：#11（`66b2708`）修上次审查的问题，#12（`4904591`）提交 REVIEW.md、记录已知的故意设计、应用 constrain-check 的补法。改动的代码文件是 `git_guard.py`、`check.py`、`review_gate.py` 和它们的测试，另有 `build_good_repo.sh`、`.pre-commit-config.yaml`、ADR 0001 和 CLAUDE.md。改动约 590 行（不含 REVIEW.md）。按 skill 规则，四组维度由我依次审，核实交给一个新启动的 agent。上次的 9 条确认问题都逐条检查了修复。

## 1. 总体评价

上次的 9 条都有对应测试，重放审查时的复现命令后全部被拒，pre-commit 全过。这次最重要的问题早在 #4 就已存在，前两次审查都漏了：`git_guard.py` 把换行替换成 `;` 后交给 `shlex`，而 `shlex` 默认把 `#` 当注释开头，所以多行命令里一个注释就让后面所有行不再检查。其余问题集中在 #11 新加的 heredoc 处理（正文脱离所属命令单独检查），以及 `check.py` 新写的 `on:` 解析把一种合法 YAML 写法漏掉了（相对上次是回退）。

建议修复顺序：

1. `git_guard.py` 的 `#` 注释问题（关掉 shlex 的 `commenters`，或按行处理注释）
2. heredoc 正文按所属命令处理：cwd、前缀变量、只把喂给 shell 的正文当脚本（一并解决误拒文档的问题）
3. `check.py` 支持零缩进的 `on:` 列表，`WEAK` 去掉 `verbose: true`
4. `env -S`

## 2. 确认的问题

### 中

**`.claude/hooks/git_guard.py:177` 未加引号的 `#` 吞掉命令剩余部分，包括后面的行**
- 问题：`segments` 先把 `\n` 换成 ` ; `，再交给 `shlex`。`shlex` 默认 `commenters='#'`，遇到 `#` 就读到行尾，而换行已经不存在，于是整条命令的剩余部分都被丢掉。词中间的 `#`（如 `a#b`、URL 里的 `#frag`）也会触发，bash 对这种 `#` 按字面处理。
- 核实（在临时仓库里）：
  - 在 main 上，`echo hi # note\ngit commit -m x`、`echo a#b\ngit commit ...`、`echo a#b; git commit ...`、`curl -s http://x/#frag\ngit commit ...` 都放行；同样的命令去掉 `#` 后被拒。通过 hook 的 stdin JSON 跑也放行。
  - 在 feature 上，`# step 1\ngit switch main` 放行。
  - 用真 bash 执行前两条，main 上确实产生了提交。
  - heredoc 也受影响：`bash <<'EOF'` 正文第一行是 `# don't` 时，整段正文都被丢掉。
  - 单行的 `echo hi # x; git commit` 在 bash 里同样是注释，放行是对的，不算问题。
- 没有注释、ADR 或测试提到注释。这个行为在 `b30f3f7`（#4）引入，早于本次范围，但 `segments` 在 #11 改过，所以一并上报。agent 常写带 `# step` 注释的多行命令，而 main 上的本地提交和原地切分支都没有服务端兜底。

### 低

**`.claude/hooks/git_guard.py:449-452` 喂给 shell 的 heredoc 正文脱离所属命令检查（上次 heredoc、`SKIP=`、`env -C` 三条的修复都不完整）**
- 问题：正文在遍历各段之前统一检查，用的是整条命令开头的 cwd 和外层继承的变量，并且 `strict=False`。
- 核实：
  - cwd：从 feature 目录执行 `cd <main> && bash <<'EOF'\ngit commit ...\nEOF` 放行，同样的写法换成 `bash -c` 被拒。反方向也错：从 main 目录执行 `cd <feat> && bash <<EOF ...` 被误拒。
  - 前缀变量：`SKIP=review-gate bash <<'EOF'\ngit push origin feat\nEOF` 放行，`SKIP=review-gate bash -c 'git push origin feat'` 被拒。ADR 0001 写的是 `bash -c` 前面的 `SKIP=` 会带进去，没有把 heredoc 排除在外。
  - 解析失败：`check()` docstring 明确说正文“通常是文本”，所以不用 strict，这部分是有意的。正文里撇号在 git 之后时 bash 会先执行 git：`bash <<'EOF'\ngit commit ...\necho it's\nEOF` 被 hook 放行，main 上产生了提交。这与 ADR 0001 的“喂给 bash 的正文就是脚本”“解析不了且提到 git 时拒绝”不符。
- 都是 #11（`66b2708`）引入的。

**`.claude/hooks/git_guard.py:215-226` `env -S '<命令>'` 不展开**
- 问题：`env` 分支把 `-S` 当普通选项跳过，剩下的 `git push origin main` 是一个词，不等于 `git`。
- 核实：在 feature 上，`env -S 'git push origin main'` 和 `env '-Sgit push origin main'` 都放行，对照 `env git push origin main` 被拒。在 main 上，`env -S 'git commit ...'` 放行，用 macOS 自带的 `env` 实际执行确实产生了提交。docstring 写的是“透过包装（含它们自己的选项）”。push main 有服务端兜底，main 上的本地提交没有。

**`skills/jy-repo-constrain-check/scripts/check.py:709` `on:` 下零缩进的列表读不到任何触发（相对上次是回退）**
- 问题：`gha_triggers` 遇到缩进为 0 的行就结束 `on:` 块，`on:\n- push\n- pull_request` 这种写法的第一个列表项就被当成块结束。
- 核实：`gha_triggers` 对这种写法返回 `({}, 2)`，`check_ci` 报“PR 触发：无；push 触发：无”，初判 WARN，证据显示“（空）”。PyYAML 和 Ruby 的 YAML 解析器都把它读成 `['push', 'pull_request']`，是合法写法。用 `0be9018` 的 `check.py` 跑同一个仓库，报的是“有/有”，因为旧正则 `^\s*-\s*push\s*$` 能匹配零缩进的列表项。测试只覆盖了缩进写法。judge 提示会让模型看触发证据，证据显示“（空）”，所以误判有机会被纠正。

**`.claude/hooks/git_guard.py:449-452` heredoc 正文不管喂给谁都按脚本检查，误拒写文档和 PR 正文（2026-11-20 用户确认不是有意的）**
- 问题：只有喂给 shell 的正文才是脚本，`cat`、`gh pr create --body-file -`、`git commit -F -` 的正文是数据。
- 核实：在 feature 分支上，`cat > notes.md <<'EOF'\ngit switch main\nEOF` 被拒，`gh pr create --body-file - <<'EOF'` 而正文里有一行 `git checkout -b foo` 也被拒。在 main 上往文件里写一行 `git commit -m x` 同样被拒。bash 只是把这些行写进文件。模块 docstring 和 `66b2708` 的提交说明写了“正文按脚本检查”，ADR 0001 只说“喂给 bash 的正文就是脚本”，没有任何地方提到这类误拒。`test_heredoc` 只测了正文外的 git 命令。修法和上面“正文脱离所属命令”那条一起做：正文跟着它所属的命令段检查，只有该段是 shell 时才当脚本。

**`skills/jy-repo-constrain-check/scripts/check.py:357` `verbose: true` 被当成放宽信号，第 3 项和第 7 项直接降为 WARN（2026-11-20 用户确认不是有意的，上两次列在“需要你确认”）**
- 问题：`WEAK` 正则含 `verbose:\s*true\s*$`。pre-commit 的 `verbose` 只控制是否打印输出，不影响 hook 是否通过。
- 修法：从 `WEAK` 里去掉这一项，并加一个“只有 `verbose: true` 时不降级”的测试。

## 3. 需要你确认

无。

## 4. 判定为故意设计

- `git_guard.py:441-442` heredoc 正文解析失败时放行（`strict=False`）：`check()` 的 docstring 写明了理由，正文通常是文本。只是这个理由不适用于喂给 shell 的正文，那部分作为问题列在第 2 节。

## 5. 上次问题的处理情况

| 上次的问题 | 状态 |
|---|---|
| heredoc 正文有撇号时整条命令跳过检查 | 数据型 heredoc 已修，有测试；喂给 bash 的正文仍有缺口，见第 2 节 |
| `SKIP=` 不传进 `bash -c` | `bash -c` 已修，有测试；`SKIP=… bash <<EOF` 没覆盖，见第 2 节 |
| CI 触发判断匹配整份 workflow | 已修，改为只读 `on:` 块，有测试；但漏掉零缩进列表，是回退，见第 2 节 |
| `GIT_CONFIG_*` 设置 `core.hooksPath` | 已修，有测试 |
| `env -C` / `--chdir=` / `pushd` 不跟随目录 | 已修，有测试；heredoc 正文仍用开头的 cwd，见第 2 节 |
| `NO_COMMIT_OPTS` 误拦 / 漏拦 | 已修，有测试（含 `merge -n` 是 `--no-stat`） |
| 包装命令的选项、git 别名 | `time -p`、`nohup --`、`command -p` 和别名（含 `!` 别名、`-c alias.*`、仓库配置里的别名）已修，有测试；`env -S` 没覆盖，见第 2 节 |
| `MAX_DIFF_CHARS` 截断没有测试 | 已修 |
| `build_good_repo.sh` 注释写 “seven” | 已修 |

## 6. 统计

| 组 | 本次上报 | 确认 | 故意设计 | 证伪 | 无法判断 | 上次上报 / 确认 / 证伪 | 上上次 |
|---|---|---|---|---|---|---|---|
| 正确性 | 5 | 5（1 条经用户确认） | 0（1 条中的一个子项） | 0 | 0 | 8 / 8 / 0 | 12 / 11 / 0 |
| 清理 | 0 | 0 | 0 | 0 | 0 | 0 / 0 / 0 | 2 / 2 / 0 |
| 一致性 | 0 | 0 | 0 | 0 | 0 | 1 / 1 / 0 | 0 / 0 / 0 |
| 设计与工程 | 0 | 0 | 0 | 0 | 0 | 0 / 0 / 0 | 3 / 3 / 0 |

- 连续三次都没有证伪的条目，不需要收紧哪一组的要求。
- 反过来要注意漏报：`#` 注释问题从 #4 起就存在，前两次审查都在审 `git_guard.py` 的解析，却都没发现。两次的复现用例都是逐个构造的绕过写法，没有拿 agent 平时真正会写的多行命令（带注释、带 URL）来测。
- 所有条目都逐条核实，没有抽查，5 条都在临时仓库里实际复现，其中 4 条用真 shell 确认了后果。

## 7. 基线

- commit：`490459125291547db6f9a6cc69c03eb5a4bb78b7`（2026-11-18 16:30 +0800，“Commit REVIEW.md, record known intentional behavior, apply constrain-check fixes (#12)”）。上次基线是 `0be9018`。
- 审查期间 HEAD 和 `origin/main` 都没有新提交。工作目录里有一个未跟踪的 `skills/jy-repo-codify/`，不属于本次审查对象。下次增量审查以 `4904591` 为起点。
- 跑过的工具（都在固定 commit 的 worktree 拷贝里跑）：
  - `pre-commit run --all-files`：全部通过，含三套单元测试和去掉 `--severity` 后的 shellcheck。
  - `ruff 0.8.6 --select ALL`（排除项目未启用的风格类规则）：剩下的是 RUF001/RUF003 全角标点（中文字符串）、PLW1510、FURB167 等风格项。`git_guard.py` 的 BLE001 是 fail closed 有意捕获所有异常。
  - `mypy`：无问题。
  - `vulture`：1 条误报，`whitespace_split` 是属性赋值。
- 没跑的：
  - README 的安装步骤：会往 `~/.claude/skills` 写软链接。
  - `review_gate.py` 的真实 `claude` 调用：每次要起 3 个 Claude。
  - ablation 的完整 eval 流程：需要 18 次完整审查，这次范围也没改到它。

## 8. 后续处理（2026-11-20）

- 第 2 节的 6 条全部在 `fix-review-3` 分支修复，每条都有测试；新测试在修复前的代码上都会失败。
  - `#` 注释：`strip_redirections` 只在词首把 `#` 当注释，跳到行尾；`shlex` 的 `commenters` 关掉。
  - heredoc：正文在命令里留一个标记词，跟着所属命令走。只有喂给 shell 的正文（`bash <<EOF`、`cat <<EOF | bash`）才按脚本检查，用这个 shell 的 cwd 和前缀变量，解析失败且提到 git 时拒绝；其他正文是数据，不检查。`strict` 参数去掉了。
  - `env -S` / `--split-string` 展开后继续解析。
  - `check.py`：`on:` 下零缩进的列表能读到；`WEAK` 去掉 `verbose: true`。
- 新增 `test_agent_style_commands`：按 agent 平时写的多行命令（注释、URL、heredoc 写的提交说明和 PR 正文）构造的回归语料，对应第 6 节说的漏报原因。
- 另修了 push 时发现的问题：`review_gate.py` 在 `claude` 失败时只打印 stderr，而 `--output-format json` 把错误（如 “Not logged in”）放在 stdout 的 `result` 里，所以拒绝信息是空的。现在两处都会打印。
- ADR 0001 同步了 heredoc、注释、`env -S` 的写法。
- 下次增量审查仍以 `4904591` 为基线，范围包括 #13、#14 和这次的修复。
