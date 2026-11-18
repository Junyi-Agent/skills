#!/usr/bin/env python3
"""Tests for review_gate.py (no real claude calls). Run: python3 scripts/test_review_gate.py"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import review_gate
from review_gate import REVIEWERS, Verdict, decide, parse_output


def out(approve: bool, structured: bool = True) -> str:
    payload = {"approve": approve, "summary": "s", "blocking_issues": [] if approve else ["a:1"]}
    if structured:
        return json.dumps({"structured_output": payload, "result": ""})
    return json.dumps({"result": json.dumps(payload)})


class ReviewGateTest(unittest.TestCase):
    def test_three_distinct_reviewers(self):
        self.assertEqual(len(REVIEWERS), 3)
        self.assertEqual(len(set(REVIEWERS.values())), 3)

    def test_vote_threshold(self):
        def votes(*a: bool) -> list[Verdict]:
            return [Verdict(str(i), x, "") for i, x in enumerate(a)]

        self.assertTrue(decide(votes(True, True, False)))
        self.assertTrue(decide(votes(True, True, True)))
        self.assertFalse(decide(votes(True, False, False)))
        self.assertFalse(decide(votes(False, False, False)))

    def test_parse_structured_and_result(self):
        self.assertTrue(parse_output("x", out(True)).approve)
        self.assertFalse(parse_output("x", out(False)).approve)
        self.assertTrue(parse_output("x", out(True, structured=False)).approve)

    def test_bad_output_rejects(self):
        approve_str = {"approve": "false", "summary": "", "blocking_issues": []}
        for bad in (
            "",
            "not json",
            "[]",
            json.dumps({"result": "plain text"}),
            json.dumps({}),
            json.dumps({"structured_output": approve_str}),
        ):
            self.assertFalse(parse_output("x", bad).approve, bad)

    def test_missing_cli_rejects(self):
        orig = review_gate.CLAUDE_CMD
        review_gate.CLAUDE_CMD = ["/nonexistent/claude-binary"]
        try:
            v = review_gate.run_reviewer(
                "x", "f", dict.fromkeys(("root", "base", "head", "log", "diff"), ""), Path.cwd()
            )
        finally:
            review_gate.CLAUDE_CMD = orig
        self.assertFalse(v.approve)


if __name__ == "__main__":
    unittest.main()
