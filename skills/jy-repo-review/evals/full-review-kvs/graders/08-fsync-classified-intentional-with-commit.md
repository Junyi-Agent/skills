---
type: llm
focus: trace
weight: 1
---

flush 不调用 fsync 一事出现在“判定为故意设计”部分，依据引用了提交 "store: drop fsync from flush" 的说明；它没有出现在确认的问题里。
