# jy-repo-codify evals

用途：改 SKILL.md（尤其是 description）后确认触发和行为没退化；模型升级后看哪些规则已经不用写。

## 组成

- `fixture/build_feeds.sh`：在当前目录生成一个小型 Python repo（feeds），埋了几类经验线索：反复修的错（C1）、REVIEW.md 里报过两次的问题（C2）、已被 lint 覆盖的规则（E1）、已知的故意设计（K1）、无法机械检查的规则（J1）。每项说明见脚本头部注释。commit 时间和作者固定，HEAD 是 `e8e9bb3`。
- `collect-candidates/`：没有指定纠正，走“收集候选”入口。期望列出带出处的候选、排序、排除已覆盖和故意设计的条目，然后停下来让用户选，不改仓库。
- `codify-correction/`：用户给了具体纠正（又直接调了 `urlopen`），走“刚纠正过”入口，期望把它固化成 lint 规则并完成双向验证。
- 每个 case 下 `prompt.md` 是用户原话，`graders/` 每个文件一条断言。
- `rule-map.json`：每条断言对应 SKILL.md 的哪条规则，以及类型（`preference` / `practice`），含义同 `jy-repo-review/evals`。
- `trigger-eval.json`：20 条触发测试 query，10 条应触发、10 条不应触发。不应触发的多是相邻任务：审查 repo、检查流程约束、记偏好、直接改 lint 配置、修单个 bug。

## 跑触发测试

改了 description 就跑。用 skill-creator 的 `run_eval.py`，在 repo 之外新建一个目录，在里面执行 `fixture/build_feeds.sh` 生成项目。query 说的“这个仓库”要真实存在，否则模型会先去查看目录，第一个工具调用就不是 skill 了，而 `run_eval.py` 只看第一个工具调用。

- 这个目录的 `.claude/skills/` 要软链接本仓库其他的 `jy-*` skill，这样不应触发的 query 才有竞争对手。不要链接 jy-repo-codify 本身，`run_eval.py` 会自己注册它。
- `claude` 要在载入 zsh 环境后调用（`zsh -lic`），否则会报未登录。
- 必须加 `--num-workers 1`。`run_eval.py` 给每次运行注册一个名字带随机后缀的命令文件，放在同一个 `.claude/commands/` 下，并行时多个同名不同后缀的副本同时存在。模型常调用别的 worker 的副本，被记成没触发。2026-11-20 用 6 个 worker 跑时，应触发的 query 全部判为失败。

```bash
SC=<skill-creator 目录>   # 含 scripts/run_eval.py
cd <fixture 目录> && zsh -lic "PYTHONPATH=$SC python3 -m scripts.run_eval \
  --eval-set <本目录>/trigger-eval.json --skill-path <本仓库>/skills/jy-repo-codify \
  --runs-per-query 3 --timeout 90 --num-workers 1 --model <当前模型 ID>"
```

## 跑行为测试

流程同 `jy-repo-review/evals/README.md` 的“运行步骤”：

1. 工作区放在 `jy-repo-codify-workspace/iteration-N/<case>/<config>/run-K/`（已在 .gitignore）。
2. 每个 run 新建空目录，执行 case 的 `place-files.sh`，把这个目录当作 repo 交给执行 agent。
3. 执行 agent 把以下内容存到 `outputs/`：
   - 最后一条回复
   - `transcript.md`，记录每一步做了什么
   - 跑完后 repo 的 `git status --porcelain`、`git log --oneline` 和 `git diff`
4. 按 `graders/` 打分，写 `grading.json`。`expectations[].text` 必须与 grader 正文逐字一致。

配置分两组：`with_skill`（完整的 `../SKILL.md`）和 `without_skill`（不给 skill）。`without_skill` 运行时要明确禁止调用 Skill 工具和读取任何 SKILL.md。

`collect-candidates` 是单轮测试：执行 agent 列完候选、问用户之后就结束，不替用户回答。

## 运行记录

| 日期 | 模型 | 触发（应触发 / 不应触发） | collect | codify | 备注 |
|---|---|---|---|---|---|
| 2026-11-20 | claude-opus-5-5 | 30/30 / 0/30（每条 3 次） | 8/8（with_skill，1 次） | 11/11（with_skill，1 次） | 首次建立；without_skill 组未跑 |
