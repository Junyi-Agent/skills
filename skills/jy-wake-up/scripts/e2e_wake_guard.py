#!/usr/bin/env python3
"""End-to-end check of wake_guard.py in real `claude -p` sessions. Run it after changing the hook:

    zsh -lic 'cd <repo> && python3 skills/jy-wake-up/scripts/e2e_wake_guard.py'

Needs a logged-in `claude` CLI and the Stop hook in <repo>/.claude/settings.json; not run in CI.
Every case tells the agent to load the jy-wake-up skill first: a Skill call adds an isMeta row to
the transcript, which 2.2.0 mistook for the prompt (the hook then let every turn end). A case
where the agent did not call Skill fails, since it did not cover that path. The agent does not
always follow the setup (in the promise case it sometimes does the work right away, so nothing
should be blocked); such a run is retried, up to 3 runs per case.

Cases (no IM tools in `claude -p`, so a reply can only go in the final text):
  promise   reply「接下来…」without working          -> blocked: no work started
  no-send   do the work, reply in the final text      -> blocked: no IM reply
  no-reply  do the work, write「无需回复：…」         -> ends without a block
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wake_guard

WAKE = "你被唤醒了，以下是 IM 新消息。@测试 请在 {path} 写入一行 ok。"
FIRST = "（测试要求：先调用 Skill 工具加载 jy-wake-up。）"
CASES = {
    "promise": (
        FIRST + "第一次回复只说「好的，接下来我去写」，不调用其他工具。",
        wake_guard.MESSAGE,
    ),
    "no-send": (
        FIRST + "写完后只在最终回复里说「写好了」，不要写「无需回复」。",
        wake_guard.NO_SEND,
    ),
    "no-reply": (FIRST + "写完后最终回复只写一行「无需回复：测试」。", None),
}


def run(prompt: str) -> tuple[bool, bool, list[str]]:
    """(called Skill, did work before the first Stop, Stop hook block reasons) for one session."""
    out = subprocess.run(
        [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-hook-events",
            "--allowedTools",
            "Write",
        ],
        capture_output=True,
        text=True,
        timeout=600,
    ).stdout
    skill, worked, stopped, reasons = False, False, False, []
    for line in out.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("type") == "assistant":
            for b in row.get("message", {}).get("content", []):
                if b.get("type") == "tool_use":
                    skill |= b.get("name") == "Skill"
                    worked |= not stopped and wake_guard.is_work(b)
        if row.get("hook_event") == "Stop" and row.get("subtype") == "hook_response":
            stopped = True
            with contextlib.suppress(json.JSONDecodeError):
                reasons.append(json.loads(row.get("stdout") or "{}").get("reason"))
    return skill, worked, [r for r in reasons if r]


def main() -> int:
    failed = 0
    tmp = Path(tempfile.mkdtemp())
    for name, (extra, want) in CASES.items():
        for _ in range(3):
            skill, worked, reasons = run(WAKE.format(path=tmp / f"{name}.txt") + extra)
            if not (name == "promise" and worked):
                break
            print(f"retry {name}: the agent worked before replying, setup not followed")
        first = reasons[0] if reasons else None
        ok = skill and first == want
        failed += not ok
        got = "no block" if first is None else first[:30]
        print(f"{'PASS' if ok else 'FAIL'} {name}: Skill called={skill}, first block={got}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
