#!/usr/bin/env python3
"""Per-rule ablation table for jy-repo-review evals.

Usage: python3 ablation_report.py get verdicts [CASE ...] --iteration <workspace>/iteration-N
                                   [-o text|json]

Reads <case>/<config>/run-*/grading.json for config in with_skill, description_only, without_skill,
matches each expectation to a grader file by its statement text, and prints, per assertion,
the pass rate in each config plus a verdict on the SKILL.md rule behind it. -o text (default)
prints markdown tables, -o json one object per case. Exits 2 on a bad argument.
Verdicts:

  可删       without_skill passes >= THRESH: the model does this unprompted
  可压缩     description_only passes >= THRESH but without_skill does not: one line is enough
  保留       only the full skill gets it done
  skill 失效 with_skill itself passes < THRESH: the rule is not working, fix it before judging
  缺数据     some config has no graded run for this assertion: rerun it before judging
"""

import argparse
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


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="ablation_report.py", allow_abbrev=False)
    verbs = p.add_subparsers(dest="verb", required=True, metavar="VERB")
    get = verbs.add_parser("get", allow_abbrev=False, help="compute and print results")
    resources = get.add_subparsers(dest="resource", required=True, metavar="RESOURCE")
    v = resources.add_parser(
        "verdicts",
        allow_abbrev=False,
        help="per-assertion pass rates and a verdict on each SKILL.md rule",
    )
    v.add_argument("cases", nargs="*", metavar="CASE", help="eval case names (default: all)")
    v.add_argument("--iteration", required=True, help="<workspace>/iteration-N directory")
    v.add_argument("-o", "--output", choices=["text", "json"], default="text")
    args = p.parse_args(argv)
    if not Path(args.iteration).is_dir():
        v.error(f"not a directory: {args.iteration}")
    unknown = [c for c in args.cases if not (EVALS / c / "graders").is_dir()]
    if unknown:
        v.error(f"no such case: {', '.join(unknown)}")
    return args


def verdict_of(full, desc, none) -> str:
    if None in (full, desc, none):
        return "缺数据"
    if full < THRESH:
        return "skill 失效"
    if none >= THRESH:
        return "可删"
    if desc >= THRESH:
        return "可压缩"
    return "保留"


def case_result(case_dir: Path, rule_map: dict) -> dict:
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
    rows = []
    for stem in sorted(tally):
        rate = {c: (p / n if n else None) for c, (p, n) in tally[stem].items()}
        m = rule_map.get(case, {}).get(stem, {})
        rows.append(
            {
                "assertion": stem,
                "rule": m.get("rule", "?"),
                "kind": m.get("kind", "?"),
                **rate,
                "verdict": verdict_of(*(rate[c] for c in CONFIGS)),
            }
        )
    return {"case": case, "runs": nruns, "rows": rows}


def render(r: dict) -> list[str]:
    lines = [
        f"## {r['case']}",
        "",
        "runs: " + ", ".join(f"{c}={r['runs'][c]}" for c in CONFIGS),
        "",
        "| 断言 | 规则 | 类型 | full | desc | none | 结论 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in r["rows"]:
        rates = " | ".join(fmt(row[c]) for c in CONFIGS)
        lines.append(
            f"| {row['assertion']} | {row['rule']} | {row['kind']} | {rates} | {row['verdict']} |"
        )
    return [*lines, ""]


def main() -> None:
    args = parse_args(sys.argv[1:])
    it = Path(args.iteration)
    rule_map = json.loads((EVALS / "rule-map.json").read_text())
    case_dirs = sorted(p for p in it.iterdir() if p.is_dir() and (EVALS / p.name).is_dir())
    if args.cases:
        case_dirs = [p for p in case_dirs if p.name in args.cases]
        for c in sorted(set(args.cases) - {p.name for p in case_dirs}):
            print(f"warning: no runs for case {c} in {it}", file=sys.stderr)
    results = [case_result(d, rule_map) for d in case_dirs]
    if args.output == "json":
        print(json.dumps({"cases": results}, ensure_ascii=False, indent=2))
        return
    print("\n".join(line for r in results for line in render(r)))


if __name__ == "__main__":
    main()
