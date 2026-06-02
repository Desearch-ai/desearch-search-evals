"""Aggregator — combines the 3 evaluators into a per-provider composite.

Every signal comes from the judge LLM reading content, not from provider
self-reports:

  source_relevance  45%   for each cited URL, the judge reads the page
                            and says whether it's relevant to the question
  answer_quality    25%   the judge reads question + answer and says
                            whether the answer actually addresses the
                            question (or correctly declines an unanswerable)
  groundedness      30%   for each factual claim, the judge checks that
                            the cited page's actual content supports it

Missing evaluators are skipped and weights renormalized — partial runs
still produce a scoreboard.

Usage:
  python3 -m evaluators.aggregator --run-dir runs/2026-05-27
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

WEIGHTS = {
    "groundedness": 0.30,
    "source_relevance": 0.45,
    "answer_quality": 0.25,
}

EVAL_FILES = {
    "source_relevance": "grades_source_relevance.json",
    "groundedness": "grades_groundedness.json",
    "answer_quality": "grades_answer_quality.json",
}


def load_evals(run_dir: Path) -> dict[str, dict[str, float | None]]:
    out: dict[str, dict[str, float | None]] = {}
    for name, fname in EVAL_FILES.items():
        p = run_dir / fname
        if not p.exists():
            continue
        data = json.loads(p.read_text())
        out[name] = data.get("providers") or {}
    return out


def composite(scores: dict[str, float | None]) -> float | None:
    """Weighted mean over present evaluators; renormalizes."""
    present = {k: v for k, v in scores.items() if v is not None and k in WEIGHTS}
    if not present:
        return None
    total_w = sum(WEIGHTS[k] for k in present)
    return sum(WEIGHTS[k] * v for k, v in present.items()) / total_w


def scoreboard(run_dir: Path) -> dict[str, Any]:
    evals = load_evals(run_dir)
    if not evals:
        raise SystemExit(f"No evaluator outputs found in {run_dir}")

    providers = sorted({p for ev in evals.values() for p in ev.keys()})
    rows: list[dict[str, Any]] = []
    for p in providers:
        row = {"provider": p}
        for ev_name in EVAL_FILES:
            row[ev_name] = evals.get(ev_name, {}).get(p)
        row["composite"] = composite({k: row[k] for k in EVAL_FILES})
        rows.append(row)
    rows.sort(key=lambda r: r["composite"] or -1, reverse=True)
    return {
        "evaluators_present": list(evals.keys()),
        "weights": WEIGHTS,
        "rows": rows,
    }


def print_table(sb: dict[str, Any]) -> None:
    rows = sb["rows"]
    cols = ["provider"] + list(EVAL_FILES) + ["composite"]
    widths = {c: max(len(c), max((len(_fmt(r.get(c))) for r in rows), default=0))
              for c in cols}
    sep = " │ "
    print(sep.join(c.ljust(widths[c]) for c in cols))
    print("─" * (sum(widths.values()) + len(sep) * (len(cols) - 1)))
    for r in rows:
        print(sep.join(_fmt(r.get(c)).ljust(widths[c]) for c in cols))


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v*100:.1f}%"
    if v is None:
        return "—"
    return str(v)


def main(run_dir: Path) -> None:
    sb = scoreboard(run_dir)
    out_path = run_dir / "scoreboard.json"
    out_path.write_text(json.dumps(sb, indent=2, ensure_ascii=False))
    print(f"\n=== Composite scoreboard ({run_dir.name}) ===\n")
    print_table(sb)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--run-dir", type=Path, required=True)
    args = p.parse_args()
    main(args.run_dir)
