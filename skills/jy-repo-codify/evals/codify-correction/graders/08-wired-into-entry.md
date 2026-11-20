---
type: llm
focus: outputs
weight: 1
---

规则写在 pyproject.toml 的 ruff 配置里（或其他被 make check / pre-commit 实际执行的位置），并且对应规则码已启用（例如 select 里加了 TID251）。
