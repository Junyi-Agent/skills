# 按语言分的检查规则

`scripts/check.py` 启动时按 `LANGS` 的顺序读取每个 `<lang>/rules.json`，把同名字段依次拼接起来；`common` 排在最后。列表字段的顺序决定证据的输出顺序，所以不要随意调整。目前所有语言的规则对所有仓库都生效，改成按识别到的语言生效见 #30 的第二个 PR。

| 字段 | 类型 | 用在 |
|---|---|---|
| `skip_dirs` | 目录名 | 扫描时跳过 |
| `formatters` / `linters` | 正则片段，合并成 `(?i)\b(a\|b)\b` | 第 3 项：识别 hook 里的工具 |
| `strict` / `weak` | 正则片段 | 第 3、7 项：严格模式和放宽的证据 |
| `weak_in_config` | `{files, pattern, why}` | 第 3 项：只在这些根目录配置文件里才算放宽 |
| `lint_configs` | 文件名 | 第 3 项：额外读取的 linter 配置 |
| `ci_lint` / `ci_test` | 正则片段 | 第 7 项：CI 里有没有 lint、测试 |
| `code_suffixes` | 后缀 | 第 8 项：哪些文件算代码 |
| `cli_code` / `app_code` | 正则片段，区分大小写 | 第 8 项：参数解析、app 入口的信号 |

正则在 import 时编译，写错了 `check.py` 会直接报错退出。`scripts/test_check.py` 的 `RulesTest` 检查字段名和正则能否编译。第 8 项的 `CLI_DECL`、`LIB_DECL`、`APP_DECL`、`APP_PATHS`、`APP_GLOBS` 还留在 `check.py` 里：它们的顺序跨语言交错，拆开会改变证据顺序，放到第二个 PR 一起改。
