# skills

检查和理解代码仓库的 Agent Skills。

| Skill | 用途 |
|---|---|
| [`jy-repo-review`](skills/jy-repo-review) | 只读代码审查，输出 `REVIEW.md` |
| [`jy-repo-quiz`](skills/jy-repo-quiz) | 出题考你对 repo 的理解 |
| [`jy-repo-constrain-check`](skills/jy-repo-constrain-check) | 检查 repo 的开发流程约束；工具类 repo 还查是否符合 Unix 设计哲学 |

## 自举

本仓库用自己的 skills 维护自己：`.claude/skills/` 软链接到 `skills/` 下的各 skill 目录，在仓库里打开 Claude Code 就能用；仓库的 hook、pre-commit、CI 都是跑 `/jy-repo-constrain-check` 后按报告补上的。

## 安装

```bash
git clone https://github.com/Junyi-Agent/skills.git ~/code/skills
for d in ~/code/skills/skills/jy-*/; do ln -sfn "${d%/}" ~/.claude/skills/"$(basename "$d")"; done
```

之后 `git pull` 即可更新。只想在某个项目里用，就链接到该项目的 `.claude/skills/`。claude.ai 上把 skill 目录打成 zip，在 Settings → Capabilities → Skills 上传。

修改本仓库的流程见 [`CLAUDE.md`](CLAUDE.md)。
