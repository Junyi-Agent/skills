#!/usr/bin/env python3
"""pre-push gate: three Claude reviewers with different focuses vote on the pushed changes.

The push goes through only when at least 2 of 3 approve. A reviewer that errors, times out
or returns unparseable output counts as a rejection. Installed via pre-commit:

    pre-commit install --hook-type pre-push

Reviewed range: merge-base(origin/main, pushed ref)..pushed ref, i.e. everything the
feature branch (or its squash-merged result) adds on top of main.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

BASE_REF = "origin/main"
REQUIRED_APPROVALS = 2
TIMEOUT_S = 900
MAX_DIFF_CHARS = 200_000
ZERO_SHA = "0" * 40

CLAUDE_CMD = [
    "claude",
    "-p",
    "--output-format",
    "json",
    "--no-session-persistence",
    "--tools",
    "Read,Grep,Glob",
]

SCHEMA = {
    "type": "object",
    "properties": {
        "approve": {"type": "boolean"},
        "summary": {"type": "string"},
        "blocking_issues": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Each item: file:line and what is wrong. Empty when approving.",
        },
    },
    "required": ["approve", "summary", "blocking_issues"],
}

REVIEWERS: dict[str, str] = {
    "correctness": (
        "Focus: correctness of code and scripts. Look for bugs, wrong edge-case handling, "
        "broken regexes, exceptions on unexpected input, tests that do not actually test "
        "the change, and behaviour that contradicts the docstrings or SKILL.md."
    ),
    "skill-quality": (
        "Focus: the skills themselves. For any changed SKILL.md: does the description still "
        "trigger on the right requests and not on wrong ones; are the instructions "
        "unambiguous and consistent with the scripts they call; are evals updated when "
        "behaviour or description changed. For non-skill changes, check the docs "
        "(README.md, CLAUDE.md, docs/adr/) stay consistent with the code."
    ),
    "workflow-safety": (
        "Focus: repository workflow and safety. Check the change follows CLAUDE.md "
        "(worktree flow, no eval artifacts committed, ADR status rules), does not weaken "
        "or bypass hooks, pre-commit, CI or this review gate, and does not add secrets, "
        "destructive commands or network calls without need."
    ),
}

PROMPT = """You are one of three independent reviewers gating a `git push` in the repo at {root}.
{focus}

Only block for real problems in the diff below (bugs, broken workflow, missing required
updates). Style nits and optional improvements are not blocking: mention them in the
summary and still approve. You may use Read/Grep/Glob to look at surrounding code; note
the working tree may differ slightly from the pushed commits.

Commits:
{log}

Diff ({base}..{head}):
{diff}
"""


@dataclass
class Verdict:
    name: str
    approve: bool
    summary: str
    issues: list[str] = field(default_factory=list)


def git(root: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    r = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, env=env, check=True
    )
    return r.stdout


def parse_output(name: str, stdout: str) -> Verdict:
    try:
        data = json.loads(stdout)
        out = data.get("structured_output")
        if out is None:
            out = json.loads(data["result"])
        return Verdict(
            name, bool(out["approve"]), str(out["summary"]), list(out["blocking_issues"])
        )
    except (ValueError, KeyError, TypeError) as e:
        return Verdict(name, False, f"unparseable reviewer output ({e}): {stdout[:300]}")


def run_reviewer(name: str, focus: str, ctx: dict[str, str], root: Path) -> Verdict:
    prompt = PROMPT.format(focus=focus, **ctx)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        r = subprocess.run(
            [*CLAUDE_CMD, "--json-schema", json.dumps(SCHEMA)],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            cwd=root,
            env=env,
        )
    except FileNotFoundError:
        return Verdict(name, False, "`claude` CLI not found on PATH")
    except subprocess.TimeoutExpired:
        return Verdict(name, False, f"timed out after {TIMEOUT_S}s")
    if r.returncode != 0:
        return Verdict(name, False, f"claude exited {r.returncode}: {r.stderr.strip()[:300]}")
    return parse_output(name, r.stdout)


def decide(verdicts: list[Verdict]) -> bool:
    return sum(v.approve for v in verdicts) >= REQUIRED_APPROVALS


def main() -> int:
    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").strip())
    head = os.environ.get("PRE_COMMIT_TO_REF") or "HEAD"
    if head == ZERO_SHA:  # branch deletion
        return 0
    base = git(root, "merge-base", BASE_REF, head).strip()
    diff = git(root, "diff", f"{base}..{head}")
    if not diff.strip():
        print("review gate: nothing to review")
        return 0
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + "\n[diff truncated; read the files for the rest]\n"
    ctx = {
        "root": str(root),
        "base": base[:12],
        "head": head,
        "log": git(root, "log", "--oneline", f"{base}..{head}"),
        "diff": diff,
    }

    print(f"review gate: {len(REVIEWERS)} reviewers on {base[:12]}..{head}", flush=True)
    with ThreadPoolExecutor(len(REVIEWERS)) as pool:
        futures = [
            pool.submit(run_reviewer, name, focus, ctx, root) for name, focus in REVIEWERS.items()
        ]
        verdicts = [f.result() for f in futures]

    for v in verdicts:
        print(f"\n[{'APPROVE' if v.approve else 'REJECT'}] {v.name}: {v.summary}")
        for issue in v.issues:
            print(f"  - {issue}")
    ok = decide(verdicts)
    approvals = sum(v.approve for v in verdicts)
    print(
        f"\nreview gate: {approvals}/{len(verdicts)} approved, need {REQUIRED_APPROVALS} -> "
        f"{'push allowed' if ok else 'push blocked'}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
