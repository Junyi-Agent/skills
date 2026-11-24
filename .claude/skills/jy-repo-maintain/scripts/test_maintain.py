#!/usr/bin/env python3
"""Tests for maintain.py: issue classification, merge policy, launchd plist."""

from __future__ import annotations

import plistlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import maintain

CONFIG = dict(maintain.DEFAULTS, guardrails=[".claude/", "scripts/", "CLAUDE.md", ".github/*"])


def issue(assoc: str = "NONE", labels: tuple[str, ...] = (), number: int = 1) -> dict:
    return {
        "number": number,
        "title": "t",
        "author_association": assoc,
        "labels": [{"name": n} for n in labels],
        "html_url": f"https://github.com/o/r/issues/{number}",
    }


def diff(*files: str, version: tuple[str, str] | None = None) -> str:
    parts = [f"diff --git a/{f} b/{f}\n--- a/{f}\n+++ b/{f}\n@@ -1 +1 @@\n-x\n+y" for f in files]
    if version:
        parts.append(
            "diff --git a/VERSION b/VERSION\n--- a/VERSION\n+++ b/VERSION\n"
            f"@@ -1 +1 @@\n-{version[0]}\n+{version[1]}"
        )
    return "\n".join(parts) + "\n"


class ClassifyTest(unittest.TestCase):
    def test_external_issue_waits_for_label(self):
        c = maintain.classify(issue("NONE", ("bug",)), CONFIG)
        self.assertFalse(c["actionable"])
        self.assertEqual(c["kind"], "bug")

        c = maintain.classify(issue("CONTRIBUTOR", ("bug", "accepted")), CONFIG)
        self.assertTrue(c["actionable"])

    def test_owner_issue_also_waits_for_label(self):
        # agents file issues under the owner's account, so the author proves nothing
        for assoc in ("OWNER", "MEMBER", "COLLABORATOR"):
            c = maintain.classify(issue(assoc, ("bug", "from-review")), CONFIG)
            self.assertFalse(c["actionable"], assoc)
            self.assertTrue(maintain.classify(issue(assoc, ("accepted",)), CONFIG)["actionable"])

    def test_full_auto_mode(self):
        config = dict(CONFIG, require_accept_label=False)
        self.assertTrue(maintain.classify(issue("NONE"), config)["actionable"])

    def test_kind_and_approval(self):
        c = maintain.classify(issue("OWNER"), CONFIG)
        self.assertEqual(c["kind"], "untriaged")
        self.assertFalse(c["approved"])

        c = maintain.classify(issue("OWNER", ("proposal", "approved")), CONFIG)
        self.assertEqual(c["kind"], "proposal")
        self.assertTrue(c["approved"])

    def test_skipped(self):
        self.assertIsNone(maintain.classify(issue("OWNER", ("maintain-log",)), CONFIG))
        self.assertIsNone(maintain.classify(issue("OWNER", ("wontfix",)), CONFIG))


class PolicyTest(unittest.TestCase):
    def test_plain_fix_may_merge(self):
        d = diff("skills/a/SKILL.md", "CHANGELOG.md", version=("1.0.2", "1.0.3"))
        self.assertEqual(maintain.policy_problems(d, CONFIG), [])

    def test_guardrails(self):
        for f in (".claude/hooks/git_guard.py", "scripts/review_gate.py", "CLAUDE.md"):
            self.assertTrue(maintain.policy_problems(diff(f), CONFIG), f)

        self.assertTrue(maintain.policy_problems(diff(".github/ci.yml"), CONFIG))
        self.assertFalse(maintain.policy_problems(diff("docs/CLAUDE.md.bak"), CONFIG))

    def test_rename_out_of_guardrail_counts(self):
        d = "diff --git a/scripts/x.py b/docs/x.py\nsimilarity index 100%\n"
        self.assertTrue(maintain.policy_problems(d, CONFIG))

    def test_major_bump_needs_human(self):
        self.assertTrue(maintain.policy_problems(diff(version=("1.4.0", "2.0.0")), CONFIG))
        self.assertFalse(maintain.policy_problems(diff(version=("1.4.0", "1.5.0")), CONFIG))

    def test_version_lines_elsewhere_are_ignored(self):
        d = diff("CHANGELOG.md").replace("-x\n+y", "-1.0.0\n+2.0.0")
        self.assertIsNone(maintain.major_change(d))


class ScheduleTest(unittest.TestCase):
    def test_plist_is_valid_and_sets_loop_env(self):
        p = plistlib.loads(maintain.plist(Path("/tmp/my repo&x"), 6).encode())
        self.assertEqual(p["StartInterval"], 21600)
        self.assertEqual(p["EnvironmentVariables"], {"JY_MAINTAIN_LOOP": "1"})
        self.assertIn("cd '/tmp/my repo&x' &&", p["ProgramArguments"][2])


if __name__ == "__main__":
    unittest.main()
