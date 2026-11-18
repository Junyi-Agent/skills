---
type: llm
focus: trace
weight: 1
---

确认的问题里有 kvs/store.py 的 Store.delete：不设置 _dirty（只做删除时 flush 直接返回，删除丢失），严重度为高或中。
