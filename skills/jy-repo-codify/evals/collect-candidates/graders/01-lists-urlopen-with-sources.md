---
type: llm
focus: trace
weight: 1
---

候选里有“直接调用 urllib.request.urlopen / 不走 feeds.http.fetch()”这一条，出处至少写到 CLAUDE.md 的规则和一个 fix 提交（weather 或 rates 那次），并指出 feeds/sources/news.py 现在仍有违规。
