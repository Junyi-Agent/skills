#!/usr/bin/env bash
# Builds the kvs fixture repo used by the jy-repo-review evals, in the current directory.
#
#   build_kvs.sh create repo full         history up to the "baseline" commit, plus one
#                                         uncommitted file
#   build_kvs.sh create repo incremental  same history, a committed REVIEW.md from an earlier
#                                         review, then three later commits (a partial fix,
#                                         a full fix, a new bug)
# Exits 2 on any other arguments.
#
# Planted items (the graders refer to these ids; see ../rule-map.json):
#   B1  Store.delete neither marks the store dirty nor takes the lock -> deletes are lost on flush,
#       and a concurrent flush can hit "dictionary changed size during iteration"
#   B2  Store.list returns limit-1 keys (off by one)
#   T1  test_list only checks the return type, so it cannot catch B2
#   D1  README says the default flush interval is 30s, code says 10
#   DC1 kvs/util.py:human_size is never used
#   FP1 cmd_* functions in kvs/commands.py look unused but are dispatched via getattr (not dead)
#   I1  flush() has no fsync: intentional, explained in the commit message of "store: drop fsync"
#   I2  cli.main swallows every exception and exits 0: listed in CLAUDE.md as known-intentional
#   A1  load() returns {} on corrupt JSON, with no comment or commit explaining it (ambiguous)
#   K1  kvs/cli.py is `kvs <verb> <key>` (get/set/list, no resource noun, no -o) and no ADR
#       exempts it -> not kubectl-style, should be reported
#   U1  kvs/export.py is uncommitted and has a shell injection; it is not part of the reviewed commit
#   (incremental only)
#   N1  Store.rename deletes the key when old == new
set -euo pipefail
usage() {
  echo "usage: build_kvs.sh create repo full|incremental" >&2
  exit 2
}
if [ $# -ne 3 ] || [ "$1" != create ] || [ "$2" != repo ]; then usage; fi
MODE="$3"
case "$MODE" in full | incremental) ;; *) usage ;; esac

export GIT_AUTHOR_NAME="kvs dev" GIT_AUTHOR_EMAIL="dev@example.invalid"
export GIT_COMMITTER_NAME="kvs dev" GIT_COMMITTER_EMAIL="dev@example.invalid"
commit() { # commit <date> <message>
  git add -A
  GIT_AUTHOR_DATE="$1" GIT_COMMITTER_DATE="$1" git commit -q -m "$2"
}

git init -q -b main .
mkdir -p kvs tests

cat > README.md <<'EOF'
# kvs

A small local key-value store with a CLI.

```
python -m kvs set <key> <value>
python -m kvs get <key>
python -m kvs list [prefix]
```

Data lives in `kvs.json` in the current directory. Writes are buffered and flushed
every 30 seconds by default (`Store(path, flush_interval=...)`).

## Tests

```
python -m unittest discover -s tests
```
EOF

cat > CLAUDE.md <<'EOF'
# kvs

## 已知的故意设计
- `kvs/cli.py` 的 `main` 捕获所有异常后以退出码 0 退出：kvs 由 cron 每分钟调用，非零退出会触发告警风暴；错误写到 stderr，由日志采集处理。
EOF

cat > kvs/__init__.py <<'EOF'
EOF

cat > kvs/__main__.py <<'EOF'
from kvs.cli import main

main()
EOF

cat > kvs/store.py <<'EOF'
import json
import os
import threading


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}


class Store:
    def __init__(self, path, flush_interval=10):
        self.path = path
        self.flush_interval = flush_interval
        self._data = {}
        self._lock = threading.Lock()
        self._dirty = False
        if os.path.exists(path):
            self._data = load(path)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        with self._lock:
            self._data[key] = value
            self._dirty = True

    def flush(self):
        with self._lock:
            if not self._dirty:
                return
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self._data, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            self._dirty = False
EOF

cat > kvs/commands.py <<'EOF'
def cmd_get(store, args):
    value = store.get(args[0])
    if value is not None:
        print(value)


def cmd_set(store, args):
    store.set(args[0], args[1])


def cmd_list(store, args):
    prefix = args[0] if args else ""
    for key in store.list(prefix):
        print(key)
EOF

cat > kvs/cli.py <<'EOF'
import sys

from kvs import commands
from kvs.store import Store


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        store = Store("kvs.json")
        handler = getattr(commands, "cmd_" + argv[0])
        handler(store, argv[1:])
        store.flush()
    except Exception as e:
        print(f"kvs: {e}", file=sys.stderr)
    sys.exit(0)
EOF

cat > kvs/util.py <<'EOF'
def human_size(n):
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.0f}TB"
EOF

cat > tests/test_store.py <<'EOF'
import os
import tempfile
import unittest

from kvs.store import Store


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "kvs.json")

    def test_set_get_roundtrip(self):
        s = Store(self.path)
        s.set("a", "1")
        s.flush()
        self.assertEqual(Store(self.path).get("a"), "1")
EOF
commit "2026-03-02T10:00:00+0800" "initial kvs store and CLI"

# I1: fsync removed on purpose, rationale only in the commit message
python3 - <<'EOF'
p = "kvs/store.py"
s = open(p).read()
s = s.replace("                f.flush()\n                os.fsync(f.fileno())\n", "")
open(p, "w").write(s)
EOF
commit "2026-03-09T15:20:00+0800" "store: drop fsync from flush

fsync on every flush pushed set() latency to ~200ms on the NFS home
directories this runs on. kvs is a cache that is rebuilt from upstream,
so losing the last flush interval on power loss is acceptable."

# B1, B2, T1
python3 - <<'EOF'
p = "kvs/store.py"
s = open(p).read()
s = s.replace("    def flush(self):", '''    def delete(self, key):
        self._data.pop(key, None)

    def list(self, prefix="", limit=100, offset=0):
        keys = sorted(k for k in self._data if k.startswith(prefix))
        return keys[offset:offset + limit - 1]

    def flush(self):''')
open(p, "w").write(s)

p = "tests/test_store.py"
s = open(p).read()
s += '''
    def test_list(self):
        s = Store(self.path)
        for i in range(5):
            s.set(f"k{i}", i)
        self.assertIsInstance(s.list(limit=3), list)
'''
open(p, "w").write(s)
EOF
commit "2026-04-14T11:05:00+0800" "store: add delete and paginated list"

if [ "$MODE" = "incremental" ]; then
  BASE=$(git rev-parse HEAD)
  cat > REVIEW.md <<EOF
# kvs 审查报告

## 总体评价
代码量小，主要问题集中在 \`Store\` 的新接口上。建议顺序：删死代码 → 修 bug → 更新文档。

## 确认的问题
1. **高** \`kvs/store.py\` \`Store.delete\`：既不设置 \`_dirty\`，也不持 \`_lock\`。只做删除时 flush 直接返回，删除丢失；与 flush 并发时 \`json.dump\` 可能遇到 "dictionary changed size during iteration"。核实：最小复现脚本 set→flush→delete→flush 后重新加载，key 仍在。
2. **中** \`kvs/store.py\` \`Store.list\`：切片 \`offset + limit - 1\` 少返回一个 key。核实：limit=3 返回 2 个。
3. **低** \`tests/test_store.py\` \`test_list\`：只断言返回类型，覆盖不到问题 2。
4. **低** \`README.md\`：写默认 flush 间隔 30 秒，\`Store.__init__\` 默认是 10。
5. **低** \`kvs/util.py\` \`human_size\`：全仓库无引用。

## 需要你确认
- \`kvs/store.py\` \`load\`：JSON 损坏时返回空字典，下次 flush 会覆盖原文件。这是有意的吗？

## 判定为故意设计
- \`Store.flush\` 不 fsync：见提交 "store: drop fsync from flush" 的说明。建议在代码里补一句注释。

## 统计
| 组 | 上报 | 确认 | 故意设计 | 证伪 |
|---|---|---|---|---|
| 正确性 | 4 | 3 | 1 | 0 |
| 清理 | 2 | 1 | 0 | 1 |
| 一致性 | 1 | 1 | 0 | 0 |
| 设计与工程 | 0 | 0 | 0 | 0 |

## 基线
- commit: \`$BASE\`（2026-04-14）
- 未跑的工具：无
EOF
  commit "2026-04-15T09:30:00+0800" "add review report"

  # partial fix of B1: marks dirty, still no lock
  python3 - <<'EOF'
p = "kvs/store.py"
s = open(p).read()
s = s.replace("        self._data.pop(key, None)\n", "        self._data.pop(key, None)\n        self._dirty = True\n")
open(p, "w").write(s)
EOF
  commit "2026-04-20T14:00:00+0800" "store: mark store dirty on delete"

  # full fix of B2 and T1
  python3 - <<'EOF'
p = "kvs/store.py"
s = open(p).read()
s = s.replace("keys[offset:offset + limit - 1]", "keys[offset:offset + limit]")
open(p, "w").write(s)
p = "tests/test_store.py"
s = open(p).read()
s = s.replace("self.assertIsInstance(s.list(limit=3), list)", 'self.assertEqual(s.list(limit=3), ["k0", "k1", "k2"])')
open(p, "w").write(s)
EOF
  commit "2026-04-21T10:10:00+0800" "store: fix list off-by-one and test it"

  # N1
  python3 - <<'EOF'
p = "kvs/store.py"
s = open(p).read()
s = s.replace("    def list(self,", '''    def rename(self, old, new):
        with self._lock:
            self._data[new] = self._data[old]
            del self._data[old]
            self._dirty = True

    def list(self,''')
open(p, "w").write(s)
EOF
  commit "2026-04-28T16:45:00+0800" "store: add rename"
fi

# U1: uncommitted work in progress, outside the commit under review
cat > kvs/export.py <<'EOF'
import json
import os


def export_gz(store, path):
    with open(path, "w") as f:
        json.dump(store._data, f)
    os.system(f"gzip -f {path}")
EOF

echo "kvs fixture ($MODE) built at $(git rev-parse HEAD)"
