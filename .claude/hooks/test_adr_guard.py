#!/usr/bin/env python3
"""Tests for adr_guard.py. Run: python3 .claude/hooks/test_adr_guard.py"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from adr_guard import check

HERE = Path(__file__).resolve().parent

HOOK = HERE / "adr_guard.py"
SETTINGS = HERE.parent / "settings.json"
TEMPLATE = (HERE.parent.parent / "docs" / "adr" / "template.md").read_text()

ADOPTED = TEMPLATE.replace("状态：提议", "状态：采纳")
FILLED = "验证人：Junyi，2026-11-21，跑了 test_x.py，全部通过。"


class AdrGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.adr_dir = Path(self.tmp.name) / "docs" / "adr"
        self.adr_dir.mkdir(parents=True)
        self.adr = self.adr_dir / "0007-x.md"
        self.adr.write_text(ADOPTED)

    def tearDown(self):
        self.tmp.cleanup()

    def edit(self, old: str, new: str, path: Path | None = None) -> str | None:
        p = str(path or self.adr)
        return check("Edit", {"file_path": p, "old_string": old, "new_string": new})

    def test_status_to_verified_denied(self):
        self.assertIsNotNone(self.edit("状态：采纳", "状态：已验证"))
        self.assertIsNotNone(
            check(
                "Write", {"file_path": str(self.adr), "content": ADOPTED.replace("采纳", "已验证")}
            )
        )

    def test_filling_verification_denied(self):
        placeholder = TEMPLATE.split("## 验证", 1)[1].strip()
        self.assertIsNotNone(self.edit(placeholder, FILLED))

    def test_multiedit_denied(self):
        edits = [
            {"old_string": "## 背景", "new_string": "## 背景\n\nx"},
            {"old_string": "状态：采纳", "new_string": "状态：已验证"},
        ]
        self.assertIsNotNone(check("MultiEdit", {"file_path": str(self.adr), "edits": edits}))

    def test_new_adr_with_verified_status_denied(self):
        new = self.adr_dir / "0008-y.md"
        content = TEMPLATE.replace("状态：提议", "状态：已验证")
        self.assertIsNotNone(check("Write", {"file_path": str(new), "content": content}))

    def test_editing_human_verification_denied(self):
        self.adr.write_text(ADOPTED.replace("状态：采纳", "状态：已验证") + "\n" + FILLED + "\n")
        self.assertIsNotNone(self.edit("2026-11-21", "2026-11-22"))

    def test_allowed_edits(self):
        self.assertIsNone(self.edit("## 背景", "## 背景\n\n补充说明"))
        self.adr.write_text(TEMPLATE)
        self.assertIsNone(self.edit("状态：提议", "状态：采纳"))
        self.assertIsNone(self.edit("状态：提议", "状态：已取代（见 0009）"))
        new = self.adr_dir / "0008-y.md"
        self.assertIsNone(check("Write", {"file_path": str(new), "content": TEMPLATE}))

    def test_superseding_verified_adr_allowed(self):
        self.adr.write_text(ADOPTED.replace("状态：采纳", "状态：已验证") + "\n" + FILLED + "\n")
        self.assertIsNone(self.edit("状态：已验证", "状态：已取代（见 0009）"))

    def test_other_files_ignored(self):
        other = Path(self.tmp.name) / "notes.md"
        self.assertIsNone(check("Write", {"file_path": str(other), "content": "状态：已验证"}))
        readme = self.adr_dir / "README.md"
        self.assertIsNone(check("Write", {"file_path": str(readme), "content": "状态：已验证"}))

    def test_failing_edit_ignored(self):
        self.assertIsNone(self.edit("不存在的文字", "状态：已验证"))

    def run_hook(self, payload: str) -> dict | None:
        r = subprocess.run(
            [sys.executable, str(HOOK)], input=payload, capture_output=True, text=True, check=True
        )
        return json.loads(r.stdout) if r.stdout else None

    def test_hook_protocol(self):
        payload = {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(self.adr),
                "old_string": "状态：采纳",
                "new_string": "状态：已验证",
            },
        }
        out = self.run_hook(json.dumps(payload))
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("停在「采纳」", out["hookSpecificOutput"]["permissionDecisionReason"])
        payload["tool_input"]["new_string"] = "状态：采纳 "
        self.assertIsNone(self.run_hook(json.dumps(payload)))

    def test_hook_fails_closed(self):
        out = self.run_hook("not json")
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_registered_in_settings(self):
        entries = json.loads(SETTINGS.read_text())["hooks"]["PreToolUse"]
        matchers = {
            tool
            for e in entries
            if any("adr_guard.py" in h["command"] for h in e["hooks"])
            for tool in e["matcher"].split("|")
        }
        self.assertLessEqual({"Edit", "Write", "MultiEdit"}, matchers)


if __name__ == "__main__":
    unittest.main()
