#!/usr/bin/env bash
# Builds the feeds fixture repo used by the jy-repo-codify evals, in the current directory.
# Commit dates and authors are fixed, so the hashes are reproducible.
#
# Planted lessons (the graders refer to these ids; see ../rule-map.json):
#   C1  Direct urllib.request.urlopen (no timeout) instead of feeds.http.fetch(): written in
#       CLAUDE.md, fixed twice (weather, rates), and still present in feeds/sources/news.py.
#       Nothing checks it mechanically. The strongest candidate.
#   C2  Naive datetime.now() without tz: reported in two successive REVIEW.md versions
#       (cache.py, then report.py), fixed both times, no check. Second candidate.
#   E1  "No print for debugging, use logging": in CLAUDE.md, already enforced by ruff T20.
#       Not a candidate (already covered).
#   K1  feeds/cli.py prints its output: listed in CLAUDE.md as known-intentional, with a
#       per-file ignore. Not a candidate.
#   J1  "Write docs/sources.md before adding a source": a judgment rule that cannot be
#       checked mechanically. At most a doc-level candidate.
set -euo pipefail

export GIT_AUTHOR_NAME="feeds dev" GIT_AUTHOR_EMAIL="dev@example.invalid"
export GIT_COMMITTER_NAME="feeds dev" GIT_COMMITTER_EMAIL="dev@example.invalid"
commit() { # commit <date> <message>
  git add -A
  GIT_AUTHOR_DATE="$1" GIT_COMMITTER_DATE="$1" git commit -q -m "$2"
}

git init -q -b main .
mkdir -p feeds/sources tests docs

cat > README.md <<'EOF'
# feeds

Pulls a few public JSON feeds and prints a daily summary.

```
python -m feeds.cli
make check   # ruff + unit tests
```
EOF

cat > CLAUDE.md <<'EOF'
# feeds

## 约定
- 网络请求统一走 `feeds.http.fetch()`，不要直接调用 `urllib.request.urlopen`：它默认没有超时，cron 任务会卡死。
- 不要用 print 调试，用 `logging`。
- 新增数据源前，先在 `docs/sources.md` 写明来源和许可。
- 提交前跑 `make check`。

## 已知的故意设计
- `feeds/cli.py` 用 print 输出结果：这是命令行的正式输出，不是调试（pyproject 里对它单独放开了 T201）。
EOF

cat > pyproject.toml <<'EOF'
[project]
name = "feeds"
version = "0.1.0"
requires-python = ">=3.10"

[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "T20"]

[tool.ruff.lint.per-file-ignores]
"feeds/cli.py" = ["T201"]
EOF

cat > Makefile <<'EOF'
.PHONY: check
check:
	ruff check .
	python3 -m unittest discover -s tests
EOF

cat > .pre-commit-config.yaml <<'EOF'
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.8.6
    hooks:
      - id: ruff
EOF

printf '# Sources\n\n| source | url | license |\n|---|---|---|\n| weather | https://example.invalid/weather | CC-BY |\n' > docs/sources.md

cat > feeds/__init__.py <<'EOF'
EOF

cat > feeds/http.py <<'EOF'
"""The one place that talks to the network."""

import json
import time
import urllib.request

DEFAULT_TIMEOUT = 10


def fetch(url: str, *, timeout: float = DEFAULT_TIMEOUT, retries: int = 3) -> dict:
    """GET a JSON document with a timeout and simple retries."""
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return json.load(resp)
        except OSError:
            if attempt == retries - 1:
                raise
            time.sleep(2**attempt)
    raise AssertionError("unreachable")
EOF

cat > feeds/sources/__init__.py <<'EOF'
EOF

cat > feeds/sources/weather.py <<'EOF'
import json
import urllib.request

URL = "https://example.invalid/weather.json"


def today() -> dict:
    with urllib.request.urlopen(URL) as resp:
        return json.load(resp)
EOF

cat > feeds/cache.py <<'EOF'
import datetime


def stamp() -> str:
    return datetime.datetime.now().isoformat()
EOF

cat > feeds/cli.py <<'EOF'
from feeds.sources import weather


def main() -> None:
    print(weather.today())


if __name__ == "__main__":
    main()
EOF

cat > tests/test_http.py <<'EOF'
import io
import json
import unittest
from unittest import mock

from feeds import http


class FetchTest(unittest.TestCase):
    def test_passes_timeout(self):
        body = io.BytesIO(json.dumps({"ok": True}).encode())
        with mock.patch("urllib.request.urlopen", return_value=body) as urlopen:
            self.assertEqual(http.fetch("https://example.invalid/x"), {"ok": True})
        self.assertEqual(urlopen.call_args.kwargs["timeout"], http.DEFAULT_TIMEOUT)


if __name__ == "__main__":
    unittest.main()
EOF
commit "2026-09-01T10:00:00+08:00" "Initial feeds: http.fetch, weather source, cli"

# C1, first fix
cat > feeds/sources/weather.py <<'EOF'
from feeds.http import fetch

URL = "https://example.invalid/weather.json"


def today() -> dict:
    return fetch(URL)
EOF
commit "2026-09-08T09:30:00+08:00" "weather: use http.fetch

urlopen was called without a timeout and the 06:00 cron job hung for 9 hours."

cat > feeds/sources/rates.py <<'EOF'
import json
import urllib.request

URL = "https://example.invalid/rates.json"


def latest() -> dict:
    with urllib.request.urlopen(URL) as resp:
        return json.load(resp)
EOF
printf '| rates | https://example.invalid/rates | CC0 |\n' >> docs/sources.md
commit "2026-09-15T14:00:00+08:00" "Add rates source"

# C2, first report
cat > REVIEW.md <<'EOF'
---
type: review
date: 2026-09-20
---

# REVIEW

## 确认的问题

### 中

**`feeds/cache.py:5` 用不带时区的 `datetime.now()` 生成时间戳**
- 服务器在 UTC，开发机在 +08:00，同一份缓存在两边比较新旧时差 8 小时，导致缓存一直被判为过期。
EOF
commit "2026-09-20T11:00:00+08:00" "Add REVIEW.md from the first repo review"

cat > feeds/cache.py <<'EOF'
import datetime


def stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()
EOF
commit "2026-09-21T10:00:00+08:00" "cache: use an aware UTC timestamp (REVIEW.md)"

# C1, second fix
cat > feeds/sources/rates.py <<'EOF'
from feeds.http import fetch

URL = "https://example.invalid/rates.json"


def latest() -> dict:
    return fetch(URL)
EOF
commit "2026-10-02T08:45:00+08:00" "rates: use http.fetch

Same bug as weather: urlopen without a timeout hung the cron job again."

cat > feeds/report.py <<'EOF'
import datetime


def header() -> str:
    return f"Report generated {datetime.datetime.now():%Y-%m-%d %H:%M}"
EOF
commit "2026-10-10T16:00:00+08:00" "Add report header"

# C2, second report
cat > REVIEW.md <<'EOF'
---
type: review
date: 2026-10-15
---

# REVIEW

## 确认的问题

### 中

**`feeds/report.py:5` 又用了不带时区的 `datetime.now()`**
- 和上次 `cache.py` 的问题相同：报告头的时间在服务器上比本地晚 8 小时。上次只修了 `cache.py`，没有加检查。
EOF
commit "2026-10-15T11:00:00+08:00" "Update REVIEW.md: second repo review"

cat > feeds/report.py <<'EOF'
import datetime


def header() -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    return f"Report generated {now:%Y-%m-%d %H:%M} UTC"
EOF
commit "2026-10-16T09:00:00+08:00" "report: use an aware UTC timestamp (REVIEW.md)"

# C1, still present
cat > feeds/sources/news.py <<'EOF'
import json
import urllib.request

URL = "https://example.invalid/news.json"


def headlines() -> list:
    with urllib.request.urlopen(URL) as resp:
        return json.load(resp)["items"]
EOF
printf '| news | https://example.invalid/news | CC-BY-SA |\n' >> docs/sources.md
commit "2026-11-02T15:20:00+08:00" "Add news source"
