#!/usr/bin/env python3
"""PreToolUse hook for Edit / Write / MultiEdit: only a human may mark an ADR as 已验证.

docs/adr/README.md: an agent stops at 采纳 and asks the user to test; only a human changes the
status to 已验证 and fills in the 验证 section. This hook simulates the edit on a
docs/adr/NNNN-*.md file and denies it when it

  - changes the status line to 已验证, or
  - changes the 验证 section (the template placeholder counts as empty).

Moving an ADR to 已取代 and every other edit are allowed. Edits through Bash (sed, scripts) are
not seen here; `scripts/repo_check.py check adrs` in pre-commit and CI catches an 已验证 ADR
without a filled-in 验证 section.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from repo_check import adr_status, adr_verification

ADR_PATH = re.compile(r"(^|/)docs/adr/\d{4}-[^/]+\.md$")

REASON = (
    "ADR 的「已验证」状态和「验证」一节只能由人类修改（docs/adr/README.md）。"
    "实现完成后把状态停在「采纳」，请用户测试，由用户自己改状态并填写验证人、日期、测了什么、结果。"
)


def apply_edit(text: str, old: str, new: str, replace_all: bool) -> str:
    return text.replace(old, new) if replace_all else text.replace(old, new, 1)


def after_text(tool: str, tool_input: dict, before: str) -> str | None:
    """The file content after the tool runs, or None when the tool would fail anyway."""
    if tool == "Write":
        return tool_input["content"]
    edits = tool_input["edits"] if tool == "MultiEdit" else [tool_input]
    text = before
    for e in edits:
        if e["old_string"] not in text:
            return None
        text = apply_edit(text, e["old_string"], e["new_string"], e.get("replace_all", False))
    return text


def check(tool: str, tool_input: dict) -> str | None:
    path = Path(tool_input["file_path"])
    if not ADR_PATH.search(path.as_posix()):
        return None
    before = path.read_text() if path.is_file() else ""
    after = after_text(tool, tool_input, before)
    if after is None:
        return None
    if adr_status(after) == "已验证" and adr_status(before) != "已验证":
        return REASON
    if adr_verification(after) != adr_verification(before):
        return REASON
    return None


def deny(reason: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )


def main() -> None:
    # Fail closed, like git_guard: a crashing guard would otherwise let the edit through.
    try:
        data = json.load(sys.stdin)
        reason = check(data["tool_name"], data["tool_input"])
    except Exception as e:
        reason = (
            f"adr_guard could not check this edit ({type(e).__name__}: {e}), so it is denied. "
            "Fix .claude/hooks/adr_guard.py or the hook input."
        )
    if reason:
        deny(reason)


if __name__ == "__main__":
    main()
