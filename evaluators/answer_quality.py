"""Evaluator #2 — answer quality (judge-graded, no provider self-reports).

Asks the judge: does the provider's ANSWER actually address the QUESTION?

Five verdicts cover both answerable and unanswerable questions in one
prompt — the judge decides which case applies:

  RESPONSIVE              answerable question, answer addresses it
  APPROPRIATE_DECLINE     unanswerable question, answer correctly declines
  EVASIVE                 answerable question, but the answer dodges/restates
  WRONG_DECLINE           answerable question, but the answer wrongly declined
  HALLUCINATED            unanswerable question, but the answer confidently
                           asserted a fictional fact (e.g. winner of 2099 cup)

Scoring:
  RESPONSIVE = APPROPRIATE_DECLINE = 1.0
  everything else = 0.0

This is the "did you actually answer my question?" check. Pair with
source_relevance (sources match question) and groundedness (claims match
cited pages) and you cover the full retrieval-→-synthesis path with three
judge-graded signals. No provider self-reported flags. No memorizable
reference answers driving the score.

Usage:
  python3 -m evaluators.answer_quality --run-dir runs/2026-05-27
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .common import discover_providers, judge_chat, load_provider_outputs


GRADER_PROMPT = """You are evaluating whether a web-search system's ANSWER properly addresses a QUESTION.

Today is {today}. Use this date to decide whether the question is asking about a past event (answerable from sources), a current state (answerable from sources), or a future event (unanswerable).

Question: {q}

Answer: {pred}

Pick exactly ONE verdict:

RESPONSIVE — the question is answerable and the answer addresses it head-on. The reader gets what they asked for in roughly the right SHAPE (a name for a "who" question, a number for "how many", a list for "what are the top 3"). Hedging is fine if the underlying answer is stated. Brief is fine. The answer can be partial as long as it's on-topic and not dodging.

APPROPRIATE_DECLINE — the question is genuinely unanswerable and the answer correctly declines. Unanswerable means: anachronism (asking what a historical figure thought about a modern concept that didn't exist in their lifetime), future event past today's date, mythological / fictional entity treated as real, request for non-public personal info (phone, home address), or mathematically undefined operation (X / 0). A clean one-sentence decline that names WHY (e.g. "Aristotle died in 322 BC, more than 2 millennia before neural networks") is the ideal.

EVASIVE — the question is answerable but the answer dodges. Examples: "the sources don't contain this", "I cannot find specific information", restating the question without answering, or hedging so much that no substantive answer is given.

WRONG_DECLINE — the question is straightforwardly answerable (a known past event, a current public fact, a routine request) but the answer treated it as unanswerable and declined. Example: declining "who is the current CEO of OpenAI?" is WRONG_DECLINE.

HALLUCINATED — the question is unanswerable but the answer confidently asserted a fictional fact as if it were real. Examples: giving a winner for a future World Cup, citing an IQ value for unicorns, quoting Einstein on LLMs.

Reply with ONLY one of: RESPONSIVE, APPROPRIATE_DECLINE, EVASIVE, WRONG_DECLINE, HALLUCINATED."""


_GOOD = ("RESPONSIVE", "APPROPRIATE_DECLINE")
_VERDICT_RE = re.compile(
    r"\b(RESPONSIVE|APPROPRIATE_DECLINE|EVASIVE|WRONG_DECLINE|HALLUCINATED)\b"
)


def _parse(text: str) -> str:
    m = _VERDICT_RE.search(text.upper().replace(" ", "_"))
    return m.group(1) if m else "EVASIVE"


def _score(verdict: str) -> float:
    return 1.0 if verdict in _GOOD else 0.0


def _today_utc() -> str:
    """ISO date for the prompt. UTC so a benchmark cron run at 00:30 local
    doesn't render yesterday's date in the eval prompt while the providers
    saw today's. The +/-1-day disagreement only matters at the
    answerable/future-event boundary, which is exactly where we don't want
    a silent skew."""
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")


async def grade_one(question: str, pred: str, today: str,
                    sem: asyncio.Semaphore) -> str:
    prompt = GRADER_PROMPT.format(today=today, q=question, pred=pred[:3000])
    async with sem:
        try:
            text = await judge_chat(prompt, max_tokens=32)
        except Exception as e:
            return f"ERROR: {type(e).__name__}"
    return _parse(text)


async def grade_provider(name: str, items: list[dict], today: str,
                         sem: asyncio.Semaphore) -> dict[str, Any]:
    per_question: list[dict[str, Any]] = []

    async def go(it: dict) -> None:
        pred = it.get("answer") or ""
        if not pred:
            per_question.append({
                "id": it["id"], "category": it.get("category"),
                "verdict": "EVASIVE", "score": 0.0,
            })
            return
        verdict = await grade_one(it["question"], pred, today, sem)
        per_question.append({
            "id": it["id"], "category": it.get("category"),
            "verdict": verdict, "score": _score(verdict),
        })

    await asyncio.gather(*[go(it) for it in items])
    per_question.sort(key=lambda x: x["id"])

    valid = [q for q in per_question if not q["verdict"].startswith("ERROR")]
    score = (sum(q["score"] for q in valid) / len(valid)) if valid else None

    from collections import Counter
    counts = Counter(q["verdict"] for q in valid)

    print(f"[{name}] answer_quality = "
          f"{score:.3f}" if score is not None else f"[{name}] no graded answers",
          f" ({dict(counts)})")
    return {
        "provider": name,
        "overall_score": score,
        "n_graded": len(valid),
        "counts": dict(counts),
        "per_question": per_question,
    }


async def main(run_dir: Path, concurrency: int, today: str | None) -> None:
    providers = discover_providers(run_dir)
    if not providers:
        raise SystemExit(f"No provider outputs found in {run_dir}")
    today = today or _today_utc()
    print(f"[answer_quality] judge \"today\" = {today}")
    sem = asyncio.Semaphore(concurrency)
    started = time.monotonic()
    results = {}
    for p in providers:
        items = load_provider_outputs(run_dir / f"{p}.json")
        results[p] = await grade_provider(p, items, today, sem)

    out = {
        "evaluator": "answer_quality",
        "judge_model": "gpt-5.4-mini",
        "today": today,
        "providers": {p: results[p]["overall_score"] for p in providers},
        "per_provider": results,
    }
    out_path = run_dir / "grades_answer_quality.json"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\nWrote {out_path} (in {time.monotonic() - started:.1f}s)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("--concurrency", type=int, default=20)
    p.add_argument(
        "--today",
        default=None,
        help='Override the "today" date in the judge prompt (YYYY-MM-DD). '
             "Defaults to today's UTC date. Useful when re-grading old "
             "responses with the original capture date so future-event "
             "rulings match what was true when the answer was generated.",
    )
    args = p.parse_args()
    asyncio.run(main(args.run_dir, args.concurrency, args.today))
