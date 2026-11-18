---
type: llm
focus: trace
weight: 1
---

Store.delete 仍在确认的问题里，且写明已修部分（补了 _dirty）和未修部分（仍不持 _lock，与 flush 并发可能出错）。
