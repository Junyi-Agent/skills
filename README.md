# skills

一组 Agent Skills（`SKILL.md` 格式），用于检查和理解代码仓库。适用于 Claude Code、claude.ai，以及其他支持 `SKILL.md` 格式的 coding agent。

| Skill | 用途 | 触发说法 |
|---|---|---|
| [`jy-repo-review`](jy-repo-review/SKILL.md) | 只读的多维度代码审查，由新起的 agent 逐条核实发现，输出 `REVIEW.md` | review repo、审查代码库、代码体检 |
| [`jy-repo-quiz`](jy-repo-quiz/SKILL.md) | 读取 repo 的关键设计后出题，考察你对代码库的理解，最后给出薄弱点和补读清单 | repo quiz、考考我这个 repo |
| [`jy-repo-constrain-check`](jy-repo-constrain-check/SKILL.md) | 检查 repo 对开发流程的约束：ADR、pre-commit、agent 审查门禁、worktree 规则、强制脚本、CI/CD | 检查流程约束、constrain check |

## 自举

这个仓库用自己的 skills 维护自己：

- **自己加载自己：** `.claude/skills/` 下是指向顶层各 skill 目录的相对软链接，在本仓库里打开 Claude Code，这些 skills 会作为项目级 skill 加载。改 `SKILL.md` 立即生效，不需要重新安装。
- **自己检查自己：** 仓库现有的流程约束，都是在本仓库上跑 `/jy-repo-constrain-check`，再按报告补上的：
  - `CLAUDE.md`
  - `.claude/hooks/git-guard.sh`
  - `.pre-commit-config.yaml`
  - `.github/workflows/ci.yml`

  改完流程后再跑一次，可以确认哪些项已经补上。
- **自己审查自己：** 改动 skill 后，可以在本仓库上跑 `/jy-repo-review` 做审查，跑 `/jy-repo-quiz` 检验自己对改动的理解。

所以，修改一个 skill 的效果，会直接体现在这个仓库自身的检查结果上。

## 安装

先 clone：

```bash
git clone https://github.com/Junyi-Agent/skills.git ~/code/skills
```

下面的命令都用软链接，以后 `git pull` 就能更新 skills。

### Claude Code：所有项目可用

```bash
mkdir -p ~/.claude/skills
for d in ~/code/skills/jy-*/; do ln -sfn "${d%/}" ~/.claude/skills/"$(basename "$d")"; done
```

如果设置了 `CLAUDE_CONFIG_DIR`，把 `~/.claude` 换成那个目录。

### Claude Code：只在某个项目里可用

```bash
cd <你的项目>
mkdir -p .claude/skills
ln -sfn ~/code/skills/jy-repo-review .claude/skills/jy-repo-review
```

这里的软链接是绝对路径，只在你自己的机器上有效。要和团队共享，就把 skill 目录复制进去（`cp -r`）再提交。

装好后重开会话，或运行 `/reload-skills`。之后用 `/jy-repo-review` 等命令调用，也可以直接说出触发说法。

### claude.ai / Claude 桌面版

每个 skill 打一个 zip，在 Settings → Capabilities → Skills 上传：

```bash
cd ~/code/skills
zip -r jy-repo-review.zip jy-repo-review -x '*/__pycache__/*' '*/evals/*'
```

`evals/` 是测试用例，运行 skill 时用不到，打包时排除。

### 其他 coding agent

支持 `SKILL.md` 的 agent，用同样的方式把目录链接到它的 skills 目录（例如 Codex CLI 是 `~/.codex/skills/`）。不支持 `SKILL.md` 的 agent，可以把 `SKILL.md` 的正文当作自定义指令使用。

### Windows

Git 在 Windows 上默认不建软链接，`.claude/skills/` 下会变成只含路径的文本文件。clone 前先开启开发者模式，然后：

```bash
git clone -c core.symlinks=true https://github.com/Junyi-Agent/skills.git
```

## 参与修改

流程见 [`CLAUDE.md`](CLAUDE.md)，主要是这几条：

- 每个改动在仓库目录之外的 worktree 里做：`git worktree add ../skills-repo-<feature> -b <feature> origin/main`
- 不在 `main` 上提交，push feature 分支后开 PR
- 第一次 clone 后运行 `pre-commit install`。CI 跑的是同一套检查

各 skill 的 evals 放在 `<skill>/evals/`，运行方法见 [`jy-repo-review/evals/README.md`](jy-repo-review/evals/README.md)。
