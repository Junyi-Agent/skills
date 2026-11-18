#!/usr/bin/env python3
"""Per-rule ablation table for jy-repo-review evals.

Usage: python3 ablation_report.py <workspace>/iteration-N [--out ablation.md]

Reads <case>/<config>/run-*/grading.json for config in with_skill, description_only, without_skill,
matches each expectation to a grader file by its statement text, and prints, per assertion,
the pass rate in each config plus a verdict on the SKILL.md rule behind it:

  可删       without_skill passes >= THRESH: the model does this unprompted
  可压缩     description_only passes >= THRESH but without_skill does not: one line is enough
  保留       only the full skill gets it done
  skill 失效 with_skill itself passes < THRESH: the rule is not working, fix it before judging
"""

import json
import sys
from pathlib import Path

THRESH = 0.8
CONFIGS = ["with_skill", "description_only", "without_skill"]
EVALS = Path(__file__).resolve().parent


def fmt(r):
    return "-" if r is None else f"{r:.0%}"


def grader_texts(case: str) -> dict[str, str]:
    out = {}
    for f in sorted((EVALS / case / "graders").glob("*.md")):
        body = f.read_text().split("---", 2)[2].strip()
        out[body] = f.stem
    return out


def runs(config_dir: Path) -> list[Path]:
    rs = sorted(config_dir.glob("run-*/grading.json"))
    if not rs and (config_dir / "grading.json").exists():
        rs = [config_dir / "grading.json"]
    return rs


def main() -> None:
    it = Path(sys.argv[1])
    out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
    rule_map = json.loads((EVALS / "rule-map.json").read_text())
    lines = []
    for case_dir in sorted(p for p in it.iterdir() if p.is_dir() and (EVALS / p.name).is_dir()):
        case = case_dir.name
        texts = grader_texts(case)
        tally = {g: {c: [0, 0] for c in CONFIGS} for g in texts.values()}
        nruns = {}
        for c in CONFIGS:
            gs = runs(case_dir / c)
            nruns[c] = len(gs)
            for g in gs:
                for e in json.loads(g.read_text())["expectations"]:
                    stem = texts.get(e["text"].strip())
                    if stem is None:
                        print(
                            f"warning: {case}/{c}: unmatched expectation: {e['text'][:60]}",
                            file=sys.stderr,
                        )
                        continue
                    tally[stem][c][0] += bool(e["passed"])
                    tally[stem][c][1] += 1
        lines += [
            f"## {case}",
            "",
            "runs: " + ", ".join(f"{c}={nruns[c]}" for c in CONFIGS),
            "",
            "| 断言 | 规则 | 类型 | full | desc | none | 结论 |",
            "|---|---|---|---|---|---|---|",
        ]
        for stem in sorted(tally):
            rate = {c: (p / n if n else None) for c, (p, n) in tally[stem].items()}
            full, desc, none = rate["with_skill"], rate["description_only"], rate["without_skill"]
            if full is not None and full < THRESH:
                verdict = "skill 失效"
            elif none is not None and none >= THRESH:
                verdict = "可删"
            elif desc is not None and desc >= THRESH:
                verdict = "可压缩"
            else:
                verdict = "保留"
            m = rule_map.get(case, {}).get(stem, {})
            lines.append(
                f"| {stem} | {m.get('rule', '?')} | {m.get('kind', '?')} | "
                f"{fmt(full)} | {fmt(desc)} | {fmt(none)} | {verdict} |"
            )
        lines.append("")
    text = "\n".join(lines)
    print(text)
    if out:
        Path(out).write_text(text)


if __name__ == "__main__":
    main()
