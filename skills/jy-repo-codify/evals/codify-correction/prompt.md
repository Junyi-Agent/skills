---
tags: [skill-creator, jy-repo-codify]
max_turns: 120
timeout_seconds: 2700
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash, Agent]
---

你刚才在 feeds/sources/news.py 里又直接调了 urllib.request.urlopen，没有超时。CLAUDE.md 早就写了要走 feeds.http.fetch()，前面也修过两次了。把这个固化下来，别再犯。
