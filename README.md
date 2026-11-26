# skills

本仓库用自己的 skills 维护自己

| Skill | 用途 |
|---|---|
| [`jy-repo-review`](skills/jy-repo-review) | 只读代码审查，输出 `REVIEW.md` |
| [`jy-repo-quiz`](skills/jy-repo-quiz) | 出题考你对 repo 的理解 |
| [`jy-repo-constrain-check`](skills/jy-repo-constrain-check) | 检查 repo 的开发流程约束；工具类 repo 还查是否符合 Unix 设计哲学 |
| [`jy-repo-codify`](skills/jy-repo-codify) | 收集仓库里犯过的错和纠正，固化成 lint、测试、check 脚本等可强制执行的约束 |
| [`jy-skills-update`](skills/jy-skills-update) | 给引用本仓库的项目用：检查更新、回顾使用记录、把问题整理成 issue 提回来 |
| [`jy-wake-up`](skills/jy-wake-up) | 被 IM 唤醒时：派给自己的活同一轮开工，不开工就写明原因；附带 Stop hook 检查 |

## 安装

```bash
Install https://github.com/Junyi-Agent/skills to this project.
```

**更新:** 在 Claude Code 里运行 `/jy-skills-update`

## For Agent

```bash
在项目根目录用 git submodule 引用本仓库，再把各 skill 软链接进 `.claude/skills/`：

```bash
git submodule add https://github.com/Junyi-Agent/skills.git .claude/jy-skills
git -C .claude/jy-skills checkout v2.0.0          # 锁定版本，版本列表见 CHANGELOG.md
mkdir -p .claude/skills
for d in .claude/jy-skills/skills/jy-*/; do n="$(basename "$d")"; ln -sfn "../jy-skills/skills/$n" ".claude/skills/$n"; done
git add .gitmodules .claude && git commit -m "Add jy skills v2.0.0"
```

同事 clone 时加 `--recurse-submodules`，已经 clone 的执行 `git submodule update --init`。不想用 submodule 的，也可以把 `skills/` 下的各 skill 软链接到本机的一个 clone，或者链进 `~/.claude/skills` 全局使用。

- Windows 上软链接需要开启开发者模式，并设置 `git config core.symlinks true`。
- claude.ai 上用：把 skill 目录打成 zip，在 Settings → Capabilities → Skills 上传。
```
