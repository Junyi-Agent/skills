#!/usr/bin/env python3
"""Tests for wake_guard.py. Run: python3 skills/jy-wake-up/scripts/test_wake_guard.py

The reply-only case rebuilds the 2026-11-25 turn (same tool sequence, made-up text): woken with
work assigned, the agent loaded the IM tool, replied「接下来先盘点…」, read and wrote HANDOFF.md,
and ended the turn. Both halves are checked: with no Stop hook configured the turn ends
(the problem), with this repo's .claude/settings.json it is blocked (the fix).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
GUARD = HERE / "wake_guard.py"
sys.path.insert(0, str(HERE))
import wake_guard  # noqa: E402

WAKE = (
    "你被唤醒了，以下是 IM 新消息。\n\n## @ 你的消息（高优先级，请处理）\n"
    "- [公司大群] 上级：@员工 你是新来的维护工程师吗？先盘点积压，结果发给我。"
)


def user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def result():
    return {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]},
    }


def tool(tool_name, **args):
    return {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": tool_name, "input": args}]},
    }


def say(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def reply_only(reply="@上级 是的，刚到岗。接下来先盘点积压，再逐个修，结果发给你。"):
    return [
        user(WAKE),
        tool("ToolSearch", query="select:mcp__im__send_message"),
        result(),
        tool("mcp__im__send_message", chatId="company", text=reply),
        result(),
        tool(
            "Bash", command="cat HANDOFF.md 2>/dev/null | head -5; git check-ignore -q HANDOFF.md"
        ),
        result(),
        tool("Write", file_path="/w/HANDOFF.md", content="## 下一步\n1. 盘点"),
        result(),
        say("已在群里回复。下一步：盘点积压。"),
    ]


def write(rows) -> Path:
    fd, name = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    return Path(name)


def end_turn(settings: dict, transcript: Path) -> str:
    """What Claude Code does at the end of a turn: run each Stop hook command; any
    {"decision": "block"} keeps the turn going. Returns "blocked" or "ended"."""
    for group in settings.get("hooks", {}).get("Stop", []):
        for hook in group.get("hooks", []):
            cmd = hook["command"].replace('"$CLAUDE_PROJECT_DIR"', str(REPO))
            event = {
                "hook_event_name": "Stop",
                "stop_hook_active": False,
                "transcript_path": str(transcript),
            }
            out = subprocess.run(
                cmd, shell=True, input=json.dumps(event), capture_output=True, text=True, check=True
            )
            if out.stdout.strip() and json.loads(out.stdout).get("decision") == "block":
                return "blocked"
    return "ended"


class WakeGuardTest(unittest.TestCase):
    def setUp(self):
        self.paths = []

    def tearDown(self):
        for p in self.paths:
            p.unlink()

    def check(self, rows):
        p = write(rows)
        self.paths.append(p)
        return wake_guard.check(p)

    def test_reply_only_ends_without_hook_and_is_blocked_with_it(self):
        p = write(reply_only())
        self.paths.append(p)
        self.assertEqual(end_turn({}, p), "ended")
        settings = json.loads((REPO / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(end_turn(settings, p), "blocked")

    def test_reason_line_lets_it_end(self):
        self.assertIsNone(self.check(reply_only("@上级 收到。不开工原因：等上级确认盘点范围。")))

    def test_work_command_lets_it_end(self):
        rows = reply_only()
        rows.insert(5, tool("Bash", command="gh issue list --state open"))
        rows.insert(6, result())
        self.assertIsNone(self.check(rows))

    def test_editing_other_files_is_work(self):
        rows = reply_only()
        rows[7] = tool("Edit", file_path="/w/README.md", old_string="a", new_string="b")
        self.assertIsNone(self.check(rows))

    def test_no_promise_lets_it_end(self):
        self.assertIsNone(self.check(reply_only("@上级 是的，我是新来的维护工程师。")[:5]))

    def test_not_a_wake_up(self):
        rows = reply_only()
        rows[0] = user("帮我看看 README")
        self.assertIsNone(self.check(rows))

    def test_only_the_last_turn_counts(self):
        earlier = [user(WAKE), tool("Bash", command="gh pr list"), result(), say("做完了")]
        self.assertEqual(self.check(earlier + reply_only()), wake_guard.MESSAGE)

    def test_reply_only_in_final_text_is_blocked(self):
        rows = [user(WAKE), tool("Bash", command="gh pr list"), result(), say("做完了，PR 已开。")]
        self.assertEqual(self.check(rows), wake_guard.NO_SEND)

    def test_sent_reply_after_work_lets_it_end(self):
        rows = [
            user(WAKE),
            tool("Bash", command="gh pr list"),
            result(),
            tool("mcp__im__send_dm", name="上级", text="做完了，PR 已开。"),
            result(),
            say("做完了，PR 已开。"),
        ]
        self.assertIsNone(self.check(rows))

    def test_no_reply_needed_line_lets_it_end(self):
        for line in ("无需回复：对方只是确认收到。", "无需回复: 只是致谢"):
            self.assertIsNone(self.check([user(WAKE), say(line)]))

    def test_no_reply_needed_does_not_excuse_a_promise(self):
        rows = reply_only()
        rows[3] = say("无需回复：只是确认")
        rows[-1] = say("无需回复：只是确认。下一步：盘点积压。")
        self.assertEqual(self.check(rows), wake_guard.MESSAGE)

    def test_skill_body_row_is_not_the_prompt(self):
        # A Skill call adds a user row with isMeta: true holding the skill text (seen in a real
        # claude -p transcript). It is not a new prompt; the turn is still a wake-up.
        rows = reply_only()
        meta = user("Base directory for this skill: /x/jy-wake-up\n\n# 被唤醒时")
        meta["isMeta"] = True
        rows[1:1] = [tool("Skill", skill="jy-wake-up"), result(), meta]
        self.assertEqual(self.check(rows), wake_guard.MESSAGE)

    def test_hook_io(self):
        p = write(reply_only())
        self.paths.append(p)
        run = lambda event: subprocess.run(  # noqa: E731
            [sys.executable, str(GUARD)], input=event, capture_output=True, text=True, check=True
        ).stdout
        self.assertEqual(
            json.loads(run(json.dumps({"transcript_path": str(p)})))["decision"], "block"
        )
        self.assertEqual(run(json.dumps({"transcript_path": str(p), "stop_hook_active": True})), "")
        self.assertEqual(run("not json"), "")
        self.assertEqual(run(json.dumps({"transcript_path": "/nonexistent"})), "")


if __name__ == "__main__":
    unittest.main()
