---
type: llm
focus: outputs
weight: 1
---

feeds/sources/news.py 改成通过 feeds.http.fetch() 取数据，不再直接调用 urlopen。
