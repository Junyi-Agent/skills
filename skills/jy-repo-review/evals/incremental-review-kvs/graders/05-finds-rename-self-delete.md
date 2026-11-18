---
type: llm
focus: trace
weight: 1
---

新发现里有 Store.rename：old == new 时先赋值再 del，导致 key 被删除。
