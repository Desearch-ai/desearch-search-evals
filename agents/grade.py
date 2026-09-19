"""Grading for the agentic benchmarks: exact answer for BrowseComp, item F1 for DeepSearchQA."""

from evaluators import judge

EXACT = """Judge whether the response is correct against the precise reference answer.
Extract the final answer from the response, then decide. The response is correct when it states
the same thing as the reference: wording, order and extra words around the value do not matter,
and small differences in formatting or precision are fine when the meaning matches. It is
incorrect when it names a different entity or value, or gives no answer.

Return JSON:
{"extracted_final_answer": "the answer taken from the response, or None",
 "correct": "yes" or "no",
 "reason": "one short sentence"}"""

ITEMS = """Grade an answer that should list several items against the reference list.
Match items by identity, not spelling: the same entity written differently is a match, and a
different entity is not. Return JSON:
{"matched": ["reference items the answer covers"], "extra": ["answer items not in the reference"]}"""


async def exact_answer(session, key, model, question, answer, reference):
    """BrowseComp: one short answer, right or wrong."""
    if not answer:
        return {"score": 0.0, "graded": True, "reason": "no answer submitted"}

    payload = {"question": question, "reference": reference, "answer": answer}
    trace = await judge.chat(
        session, key, model, _messages(EXACT, payload), max_tokens=300
    )
    if trace.get("error"):
        return {"score": 0.0, "graded": False, "reason": "grader unavailable"}

    verdict = judge.parse_json(trace.get("content"))
    correct = str(verdict.get("correct", "")).strip().lower() in {"yes", "true"}
    return {
        "score": 1.0 if correct else 0.0,
        "graded": True,
        "extracted_answer": verdict.get("extracted_final_answer"),
        "reason": verdict.get("reason", ""),
        "cost_usd": trace.get("recorded_cost_usd"),
    }


async def answer_set(session, key, model, question, answer, reference):
    """DeepSearchQA: F1 over the items the answer lists against the reference items."""
    expected = [item.strip() for item in _split(reference) if item.strip()]
    if not answer or not expected:
        return {"score": 0.0, "graded": bool(expected), "reason": "no answer submitted"}

    payload = {"question": question, "reference_items": expected, "answer": answer}
    trace = await judge.chat(
        session, key, model, _messages(ITEMS, payload), max_tokens=800
    )
    if trace.get("error"):
        return {"score": 0.0, "graded": False, "reason": "grader unavailable"}

    verdict = judge.parse_json(trace.get("content"))
    # The grader can echo an item twice, or match more items than the reference holds.
    seen = {str(item).strip().lower() for item in (verdict.get("matched") or [])}
    matched = min(len(seen), len(expected))
    extra = {str(item).strip().lower() for item in (verdict.get("extra") or [])} - seen
    returned = matched + len(extra)
    precision = matched / returned if returned else 0.0
    recall = matched / len(expected)
    f1 = 2 * precision * recall / (precision + recall) if matched else 0.0
    return {
        "score": f1,
        "precision": precision,
        "recall": recall,
        "graded": True,
        "cost_usd": trace.get("recorded_cost_usd"),
    }


def _split(reference):
    if isinstance(reference, list):
        return [str(item) for item in reference]
    return str(reference or "").replace("\n", ";").split(";")


def _messages(system, payload):
    import json

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
