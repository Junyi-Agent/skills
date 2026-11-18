---
type: llm
focus: trace
weight: 1
---

确认的问题里没有把 kvs/commands.py 的 cmd_get / cmd_set / cmd_list 当作死代码（它们由 cli.py 通过 getattr 动态分发）。
