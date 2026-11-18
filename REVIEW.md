---
type: review
commit: 0be9018fbf3e1ed8f078a351d1e56c07d65131a9
base: b9e9b616df98665494a709d1d13c75821d89d08b
date: 2026-11-18
scope: incremental
tags: [#review]
---

# REVIEW

**增量审查**：范围是 `b9e9b61..0be9018` 的 3 个提交（#8 修上次的审查问题、#9 把 skill 移进 `skills/`、#10 给 constrain-check 加第 8 项）。改动的代码文件是 `git_guard.py`、`review_gate.py`、`check.py` 和它们的测试，另有 `ablation_report.py`、`build_good_repo.sh`、ADR 0001/0002、CLAUDE.md、README 和两份 SKILL.md。改动的代码不到 1000 行，按 skill 规则四组由我依次审，核实由 2 个新启动的 agent 完成。上次报告的每条确认问题都逐条检查了。

## 1. 总体评价

上次确认的 16 条里 15 条已修完，修复本身大多可靠：新的单元测试覆盖了修复点，pre-commit 全过。剩下的问题集中在两处：

- `git_guard.py` 新加的包装解析和 fail-closed 都有漏洞。heredoc 正文里一个撇号就让整条命令跳过检查；`SKIP=` 放在 `bash -c` 前面就能跳过审查门禁。
- `check.py` 的 CI 触发判断改成 PR 和 push 分开判断，但仍在整份 workflow 文本上匹配，常见写法会产生误报。

建议修复顺序：

1. `git_guard.py` 解析失败时 fail open（heredoc 撇号）
2. `SKIP=` 不传进 `bash -c`
3. `check.py` 触发判断限定在 `on:` 块
4. `git_guard.py` 其余解析缺口和 `NO_COMMIT_OPTS`
5. 补截断测试，改 `build_good_repo.sh` 注释

## 2. 确认的问题

### 中

**`.claude/hooks/git_guard.py:290-293` heredoc 正文含撇号时，整条命令跳过检查**
- 问题：`strip_redirections` 只去掉 `<<EOF` 和定界符，正文留在命令里；正文有落单撇号（如 `Don't`）时 `shlex` 抛 `ValueError`，`check` 返回 None，同一条命令里所有 git 操作放行。
- 核实：在 main 仓库，`cat > notes.txt <<EOF\nDon't\nEOF\ngit commit -m x` 和 `git commit -F - <<EOF\nDon't do this\nEOF` 都放行，用 stdin JSON 跑完整 hook 也不 deny；在 feature 仓库接 `git switch main` 也放行。`git commit -m "$(cat <<'EOF' ... EOF)"` 仍会被拒，因为正文在双引号里。第 292 行注释的假设是“引号不配对时 shell 自己会报错”，但 heredoc 里的撇号是合法 shell，命令会照常执行。这与 ADR 0001 和模块 docstring 写的“检查本身出错时拒绝（fail closed）”矛盾，也没有测试覆盖 heredoc。main 上的本地提交和当前目录切分支，服务端都管不到。

**`.claude/hooks/git_guard.py:302-310` `SKIP=` 不传进 `bash -c`（上次 `env SKIP=` 那条的修复不完整）**
- 问题：`unwrap` 算出的 `skip_env` 在走 `shell_script` 分支时被丢掉，递归的 `check(script, cwd)` 看不到它。
- 核实：`SKIP=review-gate git push` 被拒；`SKIP=review-gate bash -c 'git push'`、`env SKIP=review-gate sh -c 'git push -u origin feature'`、`SKIP=review-gate bash -lc 'git commit -m x'` 都放行。实测 `SKIP=review-gate bash -c 'echo $SKIP'` 输出 `review-gate`，说明变量确实传进了子 shell。ADR 0001 已接受的缺口只有 `export SKIP=` 后单独 push。服务端不跑审查门禁，这条没有兜底。

**`skills/jy-repo-constrain-check/scripts/check.py:678-680, 704-715` CI 触发判断匹配整份 workflow，不限于 `on:` 块**
- 问题：`CI_PR` 和 `GHA_PUSH` 的 `^\s*push\s*:` 在所有 CI 文件拼起来的全文上搜索。
- 核实（fixture 加对照组）：
  - 只有 `pull_request` 触发、某个 step 用 `docker/build-push-action` 并写了 `with: push: true` 时，报“push 触发：有”，初判 JUDGE；去掉这一行后是 WARN。
  - 只有 push 触发、job 上写 `if: github.event_name != 'pull_request'` 时，报“PR 触发：有”，初判 JUDGE。
  - 只有 `on: push: tags: ["v*"]` 的发布 workflow 也算作有 push 触发，但第 7 项要求的是主分支 push。
- 第 7 项的 judge 提示没有要求模型核对触发条件，证据片段里也没有触发行；SKILL.md 又要求只看证据片段，所以模型很可能照搬这条事实。测试只覆盖了写在 `on:` 下的触发。

### 低

**`.claude/hooks/git_guard.py:144-159, 328-331` 用 `GIT_CONFIG_*` 环境变量设置 `core.hooksPath` 能跳过 hook**
- 核实：`GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null git push` 和 `GIT_CONFIG_PARAMETERS="'core.hookspath'='/dev/null'" git push` 都放行；对照 `git -c core.hooksPath=... push` 被拒。用真 git 验证过：加了这些变量后，一个 `exit 1` 的 hook 不再运行。`unwrap` 对环境变量只检查 `SKIP=`。

**`.claude/hooks/git_guard.py:151-154` `env -C` / `env --chdir=` / `pushd` 不跟随目录**
- 核实：在 feature 仓库里，`env -C <main> git commit -m x`、`env --chdir=<main> git commit -m x`、`pushd <main> && git commit -m x` 都放行，对照 `cd <main> && ...` 被拒。`ENV_VALUE_OPTS` 列了 `-C`，但只跳过值，没有拿它更新 cwd。macOS 自带的 `env` 支持 `-C`。

**`.claude/hooks/git_guard.py:46, 340-345` `NO_COMMIT_OPTS` 与“只拦产生提交的命令”不符**
- 核实：
  - 误拦：main 上 `git merge --no-commit`、`git merge --squash`、`git cherry-pick -n`、`git revert --no-commit` 被拒，用真 git 确认它们不产生提交。
  - 漏拦：`git cherry-pick --skip`、`git am --skip`、`git revert --skip` 放行，用真 git 确认 `cherry-pick --skip` 会继续提交后面的序列。
- ADR 0001 和 4530950 的提交说明都写“`--ff-only`、`--abort` 等不产生提交的放行”。`--skip` 只有在 main 上已经有进行中的序列时才会被利用。

**`.claude/hooks/git_guard.py:155-156, 195-209` 包装命令自身的选项、git 别名不解析**
- 核实：`time -p git push origin main`、`nohup -- git push origin main`、`command -p git push origin main` 放行，对照 `time git push origin main` 被拒。docstring 声称会透过 `time` 等包装。`git -c alias.ci=commit ci -m x` 在 main 上放行，用户配置里的任何别名同理。push main 有服务端兜底，main 上的本地提交没有。

**`scripts/test_review_gate.py` 截断路径仍没有测试（上次“没测 main()”那条修了一部分）**
- 核实：新的 `MainTest` 覆盖了 `PRE_COMMIT_TO_REF`、`ZERO_SHA`、merge-base 范围、空 diff、返回码、缺 `origin/main`。`MAX_DIFF_CHARS` 截断（`review_gate.py:176-177`）没有用例，搜 `MAX_DIFF`、`truncat` 都 0 处命中。

**`skills/jy-repo-constrain-check/evals/build_good_repo.sh:2` 注释还写 “all seven checks”**
- 核实：0be9018 在第 5 行加了 `8 = JUDGE ...`，第 2 行没跟着改。SKILL.md:16 写的是“八项”。

## 3. 需要你确认

- （上次遗留，仍未回答）`check.py:356` 的 `WEAK` 正则把 pre-commit 的 `verbose: true` 算作放宽信号，命中后第 3 / 7 项直接降为 WARN。`verbose` 只控制输出，不影响 hook 通过与否。这是有意用来抓“只打印警告不失败”的 hook 吗？如果是，要不要改成只在证据里提示、不直接降级？

## 4. 判定为故意设计

- `git_guard.py:247` 对已删除文件的 `git checkout <file>` 仍按切分支拒绝：ADR 0001 明写“只有一个参数、且它是已存在的路径而不是 commit 时放行”，拒绝信息里也给了 `git checkout -- <path>` 的替代写法。审查时注意到这一点，因为有明文依据，没有作为问题上报。

## 5. 上次问题的处理情况

| 上次的问题 | 状态 |
|---|---|
| `env SKIP=` 绕过 | 直接写法已修；`SKIP=` 加 `bash -c` 仍可绕过，见第 2 节 |
| `bash -c`、`(cd ...)` 子 shell 绕过 | 已修，有测试 |
| 审查 prompt 未隔离 diff | 已修：随机标签的 `<untrusted-…>` 块，有测试 |
| shellcheck `--severity` 被当成严格信号 | 已修 |
| CI 触发：只有 push 也报 PR/push 都有 | 已拆成 PR 和 push 分开判断；但仍在全文上匹配，产生新的误报，见第 2 节 |
| main 上 `git push origin HEAD` 放行 | 已修，有测试 |
| 只拦 `commit`，不拦 merge/cherry-pick/revert/am | 已修；`NO_COMMIT_OPTS` 的边界有误，见第 2 节 |
| `git checkout <file>` 被误拦 | 已修，有测试 |
| stdin 解析失败时 fail open | 已修，有测试；但命令解析失败（heredoc）仍 fail open，见第 2 节 |
| `review_gate` 缺 `origin/main` 时 traceback | 已修，有提示和测试 |
| `test_review_gate` 没测 `main()` | 大部分已修；截断仍未测，见第 2 节 |
| `blocking_issues` 为字符串时拆成单字符 | 已修，有测试 |
| 不认 ruff 的显式 `select` 列表 | 已修，有测试 |
| `check.py:674` 后缀过滤失效 | 已修，有测试 |
| 分支保护的仓库名、分支名解析 | 已修，有测试 |
| `ablation_report` 缺数据仍给结论 | 已修：结论改为“缺数据”（mypy 在第 84/86 行报 `float | None` 比较，是 `None in (...)` 不能收窄类型导致的误报） |
| `.gitignore` 冗余行 | 已修 |

## 6. 统计

| 组 | 本次上报 | 确认 | 故意设计 | 证伪 | 无法判断 | 上次上报 / 确认 / 证伪 |
|---|---|---|---|---|---|---|
| 正确性 | 8 | 8 | 0 | 0 | 0 | 12 / 11 / 0 |
| 清理 | 0 | 0 | 0 | 0 | 0 | 2 / 2 / 0 |
| 一致性 | 1 | 1 | 0 | 0 | 0 | 0 / 0 / 0 |
| 设计与工程 | 0 | 0 | 0 | 0 | 0 | 3 / 3 / 0 |

- 两次都没有证伪的条目，暂时不需要收紧哪一组的要求。
- 所有条目都逐条核实，没有抽查。6 条 git_guard 发现和 CI 触发那条都在临时仓库里实际复现了。

## 7. 基线

- commit：`0be9018fbf3e1ed8f078a351d1e56c07d65131a9`（2026-11-18 15:47 +0800，“jy-repo-constrain-check: add item 8, Unix philosophy for tool repos (#10)”）。上次基线是 `b9e9b61`。
- 审查期间 HEAD 和 `origin/main` 都没有新提交。下次增量审查以 `0be9018` 为起点。
- 跑过的工具（都在固定 commit 的 worktree 拷贝里跑）：
  - `pre-commit run --all-files`：全部通过，含三套单元测试。
  - `ruff 0.8.6 --select ALL`：只有项目没启用的风格类规则，加上测试替身里的未用参数，不计入。
  - `mypy`：ablation_report 那 2 处误报。
  - `vulture`：2 条误报，`whitespace_split` 属性赋值和 `__exit__(*exc)`。
  - `shellcheck --severity=style`：3 条 SC2016，是有意的写法。
  - 两个 eval fixture 的 hash 与 README 写的一致，都复现了（`d8a3a01` / `abe905f`）。
  - `build_good_repo.sh` 加 `check.py`：初判与头注释第 5 行一致。
- 没跑的：
  - README 的安装步骤：会往 `~/.claude/skills` 写软链接。
  - `review_gate.py` 的真实 `claude` 调用：每次要起 3 个 Claude。
  - ablation 的完整 eval 流程：需要 18 次完整审查。

## 8. 后续处理（2026-11-18）

- 第 2 节的 9 条全部在 PR #11（`66b2708`）修复，每条都有对应测试；审查时的复现命令重放后都被拒绝。
- “判定为故意设计”的条目，以及 ADR 0001/0003 已接受的缺口，写进了 CLAUDE.md 的“已知的故意设计”一节。
- 第 3 节 `verbose: true` 的问题没有答复，`check.py` 行为未改，下次审查继续列出。
- 同一轮还处理了 `/jy-repo-constrain-check` 给出的三处补法：shellcheck 去掉 `--severity=warning`（3 处有意的 SC2016 单独豁免）；`check.py` 遇到不认识的参数时报用法并退出 2；`review_gate.py` 的进度行改写到 stderr。
- 下次增量审查仍以 `0be9018` 为基线，范围包括 #11 和这次的改动。
