# skills

检查和理解代码仓库的 Agent Skills。

| Skill | 用途 |
|---|---|
| [`jy-repo-review`](skills/jy-repo-review) | 只读代码审查，输出 `REVIEW.md` |
| [`jy-repo-quiz`](skills/jy-repo-quiz) | 出题考你对 repo 的理解 |
| [`jy-repo-constrain-check`](skills/jy-repo-constrain-check) | 检查 repo 的开发流程约束；工具类 repo 还查是否符合 Unix 设计哲学 |
| [`jy-repo-codify`](skills/jy-repo-codify) | 收集仓库里犯过的错和纠正，固化成 lint、测试、check 脚本等可强制执行的约束 |

## 自举

本仓库用自己的 skills 维护自己：`.claude/skills/` 软链接到 `skills/` 下的各 skill 目录，在仓库里打开 Claude Code 就能用；仓库的 hook、pre-commit、CI 都是跑 `/jy-repo-constrain-check` 后按报告补上的。

## 安装到项目

用 git submodule 引用本仓库，再把各 skill 软链接进项目的 `.claude/skills/`。软链接用相对路径，和 submodule 一起提交，同事 clone 后直接能用。在项目根目录执行：

```bash
git submodule add https://github.com/Junyi-Agent/skills.git .claude/jy-skills
git -C .claude/jy-skills checkout v1.0.0          # 锁定到某个版本，版本列表见 CHANGELOG.md
mkdir -p .claude/skills
for d in .claude/jy-skills/skills/jy-*/; do n="$(basename "$d")"; ln -sfn "../jy-skills/skills/$n" ".claude/skills/$n"; done
git add .gitmodules .claude
git commit -m "Add jy skills v1.0.0"
```

同事 clone 时加 `--recurse-submodules`；已经 clone 的，执行 `git submodule update --init`。

## 更新

先读 [`CHANGELOG.md`](CHANGELOG.md) 里新版本的「不兼容」一节，再执行：

```bash
git -C .claude/jy-skills fetch --tags
git -C .claude/jy-skills checkout v1.1.0          # 目标版本
# 链接新增的 skill，删掉指向已删除 skill 的链接（只删指向 jy-skills 的）
for d in .claude/jy-skills/skills/jy-*/; do n="$(basename "$d")"; ln -sfn "../jy-skills/skills/$n" ".claude/skills/$n"; done
find .claude/skills -maxdepth 1 -type l -lname '../jy-skills/*' ! -exec test -e {} \; -print -delete
git add -A .claude
git commit -m "Update jy skills to v1.1.0"
```

同事拉到这个提交后执行 `git submodule update` 才会切到新版本。

注意：

- 整个 skill 目录一起更新，不要只同步 SKILL.md。skill 里的脚本和 SKILL.md 是配套改的。
- Windows 上软链接需要开启开发者模式，并设置 `git config core.symlinks true`。
- claude.ai 上用：把 skill 目录打成 zip，在 Settings → Capabilities → Skills 上传，更新时重新上传。

## 版本

版本号在 [`VERSION`](VERSION)，格式 X.Y.Z：X 变表示引用方要跟着改，Y 是新增 skill 或检查，Z 是修复。每个版本合并到 `main` 后自动打 `vX.Y.Z` tag 并建 GitHub Release。规则见 [ADR 0005](docs/adr/0005-versioning-and-tags.md)。

修改本仓库的流程见 [`CLAUDE.md`](CLAUDE.md)。
