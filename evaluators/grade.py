"""Grade every returned result: does its page, and does its returned text, state the verified answer."""

import argparse
import asyncio
import json
import os
import re
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit

import aiohttp

import utils
from evaluators import answer_match, judge, pages, passages, sources
from utils import digest, read_jsonl, write_json

JUDGE = "openai/gpt-5.4-mini"
JUDGED_RANKS = 3
PAGE_CHARS = 6000
OPENING_CHARS = 1500
PASSAGE_CHARS = 1200
MIRROR = re.compile(
    r"(?<![a-z0-9])(?:simple[\s_-]?qa|seal[\s_-]?qa|browse[\s_-]?comp|frames[\s_-]benchmark)(?![a-z0-9])|simple[\s_-]?evals",
    re.IGNORECASE,
)


def is_mirror(source: dict) -> bool:
    """Pages that copy a benchmark's own questions earn nothing for anyone."""
    text = unquote(
        " ".join(str(source.get(key) or "") for key in ("url", "title", "text"))
    )
    return bool(MIRROR.search(text))


def passages_of(text: str, size: int = PASSAGE_CHARS) -> list[tuple[int, str]]:
    """Consecutive paragraphs packed into passages of about `size` characters, with their offsets."""
    out, start, buffer = [], 0, ""
    position = 0
    for paragraph in text.split("\n"):
        if buffer and len(buffer) + len(paragraph) > size:
            out.append((start, buffer))
            start, buffer = position, ""
        buffer = f"{buffer}\n{paragraph}" if buffer else paragraph
        position += len(paragraph) + 1
    if buffer:
        out.append((start, buffer))
    return [
        (offset + i, chunk[i : i + size])
        for offset, chunk in out
        for i in range(0, len(chunk), size)
    ]


def evidence_text(text: str, question: dict, snippet: str = "") -> str:
    """A long page cut to its opening plus the passages most likely to hold the answer, so the page never shows less than its snippet."""
    if len(text) <= PAGE_CHARS:
        return text
    terms = {
        w for w in answer_match.normalize(question["question"]).split() if len(w) > 3
    }
    probes = [s for s in re.split(r"(?<=[.!?])\s+", snippet) if len(s) > 60][:3]
    scored = []
    for offset, chunk in passages_of(text):
        plain = answer_match.normalize(chunk)
        score = len(terms & set(plain.split()))
        if (
            answer_match.contains(
                chunk, question["answer"], question.get("answer_aliases", [])
            )
            != "none"
        ):
            score += 5
        if any(answer_match.normalize(probe) in plain for probe in probes):
            score += 5
        scored.append((score, offset, chunk))
    chosen = [(0, text[:OPENING_CHARS])]
    used = OPENING_CHARS
    for score, offset, chunk in sorted(scored, key=lambda item: (-item[0], item[1])):
        if score == 0 or used + len(chunk) > PAGE_CHARS:
            continue
        if offset + len(chunk) <= OPENING_CHARS:
            continue
        if offset < OPENING_CHARS:
            chunk, offset = chunk[OPENING_CHARS - offset :], OPENING_CHARS
        chosen.append((offset, chunk))
        used += len(chunk)
    return "\n…\n".join(chunk for _, chunk in sorted(chosen))


def url_key(url: str) -> str:
    """One spelling per page: scheme, www, mobile hosts, encoding and trailing slash ignored."""
    parts = urlsplit((url or "").strip())
    host = parts.netloc.lower().removeprefix("www.").removeprefix("m.")
    host = host.replace(".m.wikipedia.org", ".wikipedia.org")
    path = unquote(parts.path).rstrip("/") or "/"
    return host + path + ("?" + parts.query if parts.query else "")


def gold_keys(question: dict) -> set[str]:
    urls = list(question.get("reference_urls") or []) + list(question.get("urls") or [])
    urls += [s.get("url") for s in question.get("reference_sources") or []]
    return {url_key(url) for url in urls if url}


def needs_judgment(level: str, rank: int) -> bool:
    """String evidence is always confirmed; top results are read even without it, to catch paraphrase."""
    return level != "none" or rank <= JUDGED_RANKS


class Judge:
    def __init__(self, session, key, out, model, concurrency):
        self.session, self.key, self.out, self.model = session, key, out, model
        self.semaphore = asyncio.Semaphore(concurrency)
        self.locks, self.calls = {}, {}

    async def grade(self, question: dict, title: str, text: str, published):
        reader_input = passages.build_input(
            question, {"title": title, "text": text, "published": published}
        )
        inputs = {
            **reader_input,
            "verified_answer": question["answer"],
            "accepted_aliases": question.get("answer_aliases", []),
        }
        identifier = digest(
            {"model": self.model, "input": inputs, "system": passages.JUDGE_SYSTEM}
        )
        path = self.out / "calls" / f"{identifier}.json"
        async with self.locks.setdefault(identifier, asyncio.Lock()):
            trace = json.loads(path.read_text())["trace"] if path.exists() else None
            if trace is None or trace.get("error"):
                if not self.key:
                    raise ValueError(
                        "OPENROUTER_API_KEY is required for uncached judgments"
                    )
                messages = [
                    {"role": "system", "content": passages.JUDGE_SYSTEM},
                    {"role": "user", "content": json.dumps(inputs, ensure_ascii=False)},
                ]
                async with self.semaphore:
                    trace = await judge.chat(
                        self.session, self.key, self.model, messages, max_tokens=900
                    )
                if not trace.get("error"):
                    write_json(
                        path, {"model": self.model, "input": inputs, "trace": trace}
                    )
                if len(self.calls) % 100 == 99:
                    print(f"Judge calls: {len(self.calls) + 1}", flush=True)
        self.calls[identifier] = trace
        if trace.get("error"):
            return identifier, None, "judge_unavailable"
        try:
            judgment = passages.validate_reading(
                judge.parse_json(trace["content"]), reader_input
            )
            return identifier, judgment, "ok"
        except (KeyError, TypeError, ValueError):
            return identifier, None, "judge_error"


async def grade_basis(judge_, question, rank, title, text, published, evidence):
    """One evidence base for one result: screen first, judge only when the policy asks."""
    aliases = question.get("answer_aliases", [])
    level = answer_match.contains(f"{title}\n{text}", question["answer"], aliases)
    row = {
        "screen": level,
        "status": "not_judged",
        "judgment": None,
        "evidence": None,
        "hit": 0,
    }
    if not text.strip() and not title.strip():
        row["status"] = "empty"
        return row
    if not needs_judgment(level, rank):
        return row
    identifier, judgment, status = await judge_.grade(question, title, text, published)
    evidence[identifier] = {"title": title, "text": text, "published": published}
    hit = bool(judgment) and judgment["label"] == "answers"
    if hit and not quoted(judgment["extracted_answer"], f"{title}\n{text}"):
        status, hit = "unsupported_answer", False
    row.update(status=status, judgment=judgment, evidence=identifier, hit=int(hit))
    return row


def squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", answer_match.normalize(text))


def quoted(extracted: str | None, source: str) -> bool:
    """The judge's answer must occur in the judged text; an answer copied from the key alone is rejected."""
    answer = re.sub(r"(?i)^(?:the|a|an)\s+", "", (extracted or "").strip())
    if not answer:
        return False
    if answer_match.contains(source, answer) != "none":
        return True
    compact = squash(answer)
    return len(compact) >= 6 and compact in squash(source)


async def execute(args):
    utils.load_env(args.env_file)
    run, out = Path(args.run), Path(args.out)
    questions = {q["id"]: q for q in read_jsonl(run / "questions.jsonl")}
    config = json.loads((run / "config.json").read_text())

    searches = []
    for question in questions.values():
        for profile in config["profiles"]:
            path = run / "searches" / profile["id"] / f"{question['id']}.json"
            result = (
                json.loads(path.read_text()) if path.exists() else {"status": "missing"}
            )
            ranked = (
                sources.normalize_sources(result)
                if result.get("status") == "ok"
                else []
            )
            searches.append((question, profile["id"], result, ranked))

    gold_only = getattr(args, "gold_only", False)
    urls = [s["url"] for *_, ranked in searches for s in ranked if s["url"]]
    fetched = (
        {}
        if gold_only
        else await pages.fetch_pages(
            urls, cache_dir=args.page_cache, concurrency=args.fetch_concurrency
        )
    )

    evidence: dict = {}
    async with aiohttp.ClientSession() as session:
        judge_ = Judge(
            session,
            os.environ.get("OPENROUTER_API_KEY", ""),
            out,
            args.model,
            args.concurrency,
        )

        def bases(question, source):
            """(basis, title, text, fetched) for the page and for the returned text."""
            page = fetched.get(source["url"], {})
            page_ok = page.get("status") == "ok" and bool(
                (page.get("text") or "").strip()
            )
            page_text = (
                evidence_text(page["text"], question, source["text"])
                if page_ok
                else source["text"]
            )
            page_title = (
                (page.get("title") or source["title"]) if page_ok else source["title"]
            )
            return {
                "page": (page_title, page_text, page_ok),
                "snippet": (source["title"], source["text"], None),
            }

        async def grade_in_order(question, ranked, basis):
            """Judge results in rank order and stop at the first confirmed hit; later ranks cannot change hit@k or MRR."""
            rows, found = [], False
            if gold_only:
                return [None] * len(ranked)
            for source in ranked:
                if is_mirror(source):
                    rows.append(None)
                    continue
                title, text, page_ok = bases(question, source)[basis]
                if found:
                    row = {
                        "screen": None,
                        "status": "after_first_hit",
                        "judgment": None,
                        "evidence": None,
                        "hit": 0,
                    }
                else:
                    row = await grade_basis(
                        judge_,
                        question,
                        source["rank"],
                        title,
                        text,
                        source["published"],
                        evidence,
                    )
                    found = bool(row["hit"])
                if basis == "page":
                    row["fetched"] = page_ok
                rows.append(row)
            return rows

        async def one_search(question, profile_id, result, ranked):
            page_rows, snippet_rows = await asyncio.gather(
                grade_in_order(question, ranked, "page"),
                grade_in_order(question, ranked, "snippet"),
            )
            results = [
                {
                    **_meta(source),
                    "mirror": is_mirror(source),
                    "page": page,
                    "snippet": snippet,
                }
                for source, page, snippet in zip(ranked, page_rows, snippet_rows)
            ]
            gold = gold_keys(question)
            for item, source in zip(results, ranked):
                item["gold"] = (
                    url_key(source["url"]) if url_key(source["url"]) in gold else None
                )
            return profile_id, {
                "question_id": question["id"],
                "search_status": result.get("status"),
                "search_error": result.get("error"),
                "gold_urls": len(gold),
                "results": results,
            }

        rows = await utils.map_batches(
            searches, lambda item: one_search(*item), args.batch_size
        )

    profiles: dict = {}
    for profile_id, row in rows:
        profiles.setdefault(profile_id, []).append(row)
    costs = judge.reported_costs(
        [
            attempt
            for trace in judge_.calls.values()
            for attempt in trace.get("attempts", [{}])
        ]
    )
    report = {
        "judge": args.model,
        "judged_ranks_without_string_match": JUDGED_RANKS,
        "judge_calls": len(judge_.calls),
        "recorded_cost_usd": costs["recorded_cost_usd"],
        "fetch_status_counts": dict(Counter(p.get("status") for p in fetched.values())),
        "evidence": evidence,
        "profiles": profiles,
    }
    write_json(out / "results.json", report)
    print(
        f"Graded {len(rows)} searches with {len(judge_.calls)} judge calls", flush=True
    )
    return report


def _meta(source: dict) -> dict:
    return {key: source.get(key) for key in ("rank", "url", "title", "published")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--page-cache", required=True)
    parser.add_argument("--model", default=JUDGE)
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--fetch-concurrency", type=int, default=16)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument(
        "--gold-only",
        action="store_true",
        help="Score gold URLs only: no page fetching or model calls",
    )
    parser.add_argument("--env-file", default=".env")
    asyncio.run(execute(parser.parse_args()))


if __name__ == "__main__":
    main()
