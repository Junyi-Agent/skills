#!/usr/bin/env python3
"""Claude Code Stop hook: an agent woken by IM starts assigned work in the same turn.

Rule (skills/jy-wake-up/SKILL.md): when a wake-up message assigns you work, start it in this
turn, or write a line「不开工原因：…」saying why not. This hook blocks the end of a turn when:

  1. the turn's prompt is an IM wake-up (starts with「你被唤醒了」);
  2. no work tool ran: every call is IM, ToolSearch, Read/Grep/Glob, or a Write/Edit/Bash that
     touches HANDOFF.md (loading a skill is not work either);
  3. a sent IM message or a reply text promises later work (接下来 / 下一步 / 准备 / 先…再 ...);
  4. nothing in them says「不开工原因：」.

It also blocks a woken turn that sent no IM message (no mcp__im__send_* call) and has no line
「无需回复：…」: text only in the final reply is not delivered, nobody is notified.

Known gaps: the promise words are a keyword match (misses and false hits), and any one
command that does not mention HANDOFF.md counts as work, read-only or not. It stops the plain
"reply and end the turn" case; a server-side check in jy-dashboard is the stronger fix.

Reads the Stop hook JSON on stdin; prints {"decision": "block", "reason": ...} to block.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

WAKE = "你被唤醒了"
PROMISE = re.compile(r"接下来|下一步|准备|稍后|下一轮|会去|马上|先.{0,30}再")
REASON = re.compile(r"不开工原因\s*[:：]")
NO_REPLY = re.compile(r"无需回复\s*[:：]")
TALK_TOOLS = re.compile(r"^(mcp__im__\w+|ToolSearch|Skill|Read|Grep|Glob)$")
HANDOFF = "HANDOFF.md"
MESSAGE = (
    "消息里有派给你的活，但这一轮只回复了、没有开工（jy-wake-up skill）。"
    "现在就开始做；确实不能开工时，在回复里写一行「不开工原因：…」，说明在等什么、等谁。"
)

NO_SEND = (
    "这一轮是被 IM 唤醒的，但没有用 IM 工具（send_message / send_dm）回复，"
    "只写在最终文本里的话对方收不到（jy-wake-up skill）。用 IM 工具回复；"
    "确实不需要回复时，写一行「无需回复：…」说明原因。"
)


def blocks(row: dict) -> list[dict]:
    content = row.get("message", {}).get("content", [])
    return [{"type": "text", "text": content}] if isinstance(content, str) else content


def prompt_text(row: dict) -> str:
    return "\n".join(b.get("text", "") for b in blocks(row) if b.get("type") == "text")


def turn_rows(transcript: Path) -> list[dict]:
    """Rows from the last user prompt to the end. A tool result is not a prompt, nor is an
    isMeta row (the skill text a Skill call adds)."""
    rows = []
    for line in transcript.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("type") in ("user", "assistant"):
            rows.append(row)
    start = 0
    for i, row in enumerate(rows):
        if row["type"] == "user" and not row.get("isMeta") and prompt_text(row):
            start = i
    return rows[start:]


def is_work(call: dict) -> bool:
    name, args = call.get("name", ""), call.get("input", {})
    if TALK_TOOLS.match(name):
        return False
    if name in ("Write", "Edit", "MultiEdit"):
        return Path(args.get("file_path", "")).name != HANDOFF
    if name == "Bash":
        return HANDOFF not in args.get("command", "")
    return True


def check(transcript: Path) -> str | None:
    """The block reason, or None to let the turn end."""
    rows = turn_rows(transcript)
    if not rows or not prompt_text(rows[0]).lstrip().startswith(WAKE):
        return None
    out = [b for r in rows if r["type"] == "assistant" for b in blocks(r)]
    calls = [b for b in out if b.get("type") == "tool_use"]
    sent = [
        c.get("input", {}).get("text", "")
        for c in calls
        if c.get("name", "").startswith("mcp__im__send")
    ]
    final = [b.get("text", "") for b in out if b.get("type") == "text"]
    text = "\n".join(sent + final)
    if not any(is_work(c) for c in calls) and PROMISE.search(text) and not REASON.search(text):
        return MESSAGE
    if not sent and not NO_REPLY.search(text):
        return NO_SEND
    return None


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        return
    if not isinstance(event, dict) or event.get("stop_hook_active"):
        return
    path = Path(event.get("transcript_path") or "")
    if not path.is_file():
        return
    reason = check(path)
    if reason:
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))


if __name__ == "__main__":
    main()
