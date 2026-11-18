---
type: llm
focus: trace
weight: 1
---

确认的问题里有 Store.list 的 off-by-one：切片 offset + limit - 1 导致少返回一个 key。
