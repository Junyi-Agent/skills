# 按语言分的检查规则

`scripts/check.py` 先识别仓库用了哪些语言，再加载 `<lang>/rules.json`，把同名字段按 `LANGS` 的顺序拼接起来；`common` 总是生效，排在最后。识别方法：根目录有 `detect.files` 里的文件、能匹配 `detect.globs`，或者 6 层目录以内有 `detect.suffixes` 里的后缀，满足任一条就算用了这种语言。一种语言都没识别到时，所有语言的规则都生效。输出的第一行列出识别到的语言，`-o json` 输出里是 `languages` 字段。

有些字段不分语言，始终按全部语言合并：只关掉它们只会让真实存在的东西被漏掉，比如去扫 node_modules，或者看不到 hook 里的 clang-format。

| 字段 | 类型 | 用在 | 按语言生效 |
|---|---|---|---|
| `detect` | `{files, globs, suffixes}` | 识别语言 | — |
| `skip_dirs` | 目录名 | 扫描时跳过 | 否 |
| `code_suffixes` | 后缀 | 第 8 项：哪些文件算代码 | 否 |
| `formatters` / `linters` | 正则片段，合并成 `(?i)\b(a\|b)\b` | 第 3 项：识别 hook 里的工具 | 否 |
| `lint_configs` | 文件名 | 第 3 项：额外读取的 linter 配置 | 否 |
| `strict` / `weak` | 正则片段 | 第 3、7 项：严格模式和放宽的证据 | 是 |
| `weak_in_config` | `{files, pattern, why}` | 第 3 项：只在这些根目录配置文件里才算放宽 | 是 |
| `ci_lint` / `ci_test` | 正则片段 | 第 7 项：CI 里有没有 lint、测试 | 是 |
| `cli_code` / `app_code` | 正则片段，区分大小写 | 第 8 项：参数解析、app 入口的信号 | 是 |
| `cli_decl` / `lib_decl` / `app_decl` | `[文件, 正则]` | 第 8 项：CLI、库、app 的声明 | 是 |
| `app_paths` / `app_globs` | 路径 / glob | 第 8 项：app 目录 | 是 |

列表字段的顺序决定证据的输出顺序。正则在加载时编译，写错了 `check.py` 会直接报错退出。`scripts/test_check.py` 的 `RulesTest` 检查字段名和正则，`LanguageTest` 检查每种语言能否被识别、别的语言的规则是否关掉。

补一种语言：建 `<lang>/rules.json`，把目录名加进 `check.py` 的 `LANGS`（放在 `common` 之前），在 `LanguageTest.test_detect_each_language` 里加一个例子。
