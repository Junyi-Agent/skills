# jy-repo-review evals

用途：每次模型升级后跑一遍，看 SKILL.md 里哪些规则模型已经不需要提示就会做，可以删或压缩。

## 组成

- `fixture/build_kvs.sh`：在当前目录生成一个小型 Python repo（kvs），埋了真 bug、文档漂移、死代码、动态分发的“假死代码”、有提交说明的故意设计、CLAUDE.md 清单里的故意设计、无说明的可疑处、一个未提交文件。每项的编号和说明见脚本头部注释。commit 时间和作者固定，hash 可复现（full 的 HEAD 是 `d8a3a01`，incremental 的 HEAD 是 `abe905f`）
- `full-review-kvs/`、`incremental-review-kvs/`：两个 case，`prompt.md` 是用户原话，`graders/` 每个文件一条断言
- `rule-map.json`：每条断言对应 SKILL.md 的哪条规则，以及类型（`preference` 你的偏好 / `practice` 通用做法）
- `variants/description-only/SKILL.md`：只保留 description 的版本
- `ablation_report.py`：汇总三组的通过率，逐条给结论

## 三组配置

| 目录名 | skill |
|---|---|
| `with_skill` | 完整的 `../SKILL.md` |
| `description_only` | `variants/description-only/SKILL.md` |
| `without_skill` | 无。运行时要明确禁止调用 Skill 工具和读取任何 SKILL.md，因为本机已安装 jy-repo-review，否则会污染基线 |

每组每个 case 跑 3 次（`run-1/` … `run-3/`），共 2 × 3 × 3 = 18 次。每次都是一整轮 repo 审查，会再起若干 subagent，成本不低。

## 运行步骤（skill-creator 流程）

1. 工作区放在 `jy-repo-review-workspace/iteration-N/<case>/<config>/run-K/`（已在 .gitignore）
2. 每个 run：新建空目录，在里面执行 case 的 `place-files.sh`，以该目录为 repo 交给执行 agent
3. 执行 agent 要把以下内容存到 `outputs/`：最终的 REVIEW.md、最后一条回复、`transcript.md`（每一步做了什么，含所有 subagent 的完整 prompt），以及跑完后 repo 的 `git status --porcelain` 和 `git log --oneline`
4. 按 `graders/` 打分，写 `grading.json`（`expectations[].text` 必须与 grader 正文逐字一致）
5. `python -m scripts.aggregate_benchmark <iteration>`（在 skill-creator 目录下）得到总体通过率
6. `python3 evals/ablation_report.py <iteration> --out <iteration>/ablation.md` 得到逐条结论

## 读结论

- **可删**：不给 skill，模型也稳定做到。从 SKILL.md 删掉这条规则
- **可压缩**：只给 description 就能做到，正文里这条可以缩成一句或删掉
- **保留**：只有完整 skill 能做到
- **skill 失效**：完整 skill 也做不到，先改规则再谈删

`preference` 类规则即使暂时“可删”，也先看一下是不是 fixture 里的线索太明显（比如 prompt 已经说了文件名），再决定。

另外看 benchmark 的 tokens 和耗时：去掉 skill 后质量不降、成本明显下降，也是删的理由。

## 运行记录

| 日期 | 模型 | full | desc | none | 删/压缩的规则 |
|---|---|---|---|---|---|
