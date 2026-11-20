---
type: llm
focus: outputs
weight: 1
---

报告里的不变式覆盖整个代码库（feeds/http.py 之外都不得直接调用 urlopen），不是只针对 news.py。
